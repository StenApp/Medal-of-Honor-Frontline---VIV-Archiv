#!/usr/bin/env python3
"""
MOHF Viewer - OpenGL via GLFW
Abhängigkeiten: pip install glfw PyOpenGL PyOpenGL_accelerate pillow numpy

Controls:
  LMB drag = rotate    RMB drag = pan    Scroll = zoom
  R = reset   T = top   F = front   I = iso
  G = CPT-Geometrie laden   A = ART.cpt Texturen laden
  ESC = quit
"""
import struct, os, sys, math, ctypes, datetime as _dt
import numpy as np

class _Log:
    def __init__(self):
        lp = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mohf_viewer.log")
        self.f = open(lp, "a", encoding="utf-8")
        self.s = sys.__stdout__ if hasattr(sys, "__stdout__") else sys.stdout
        self.f.write(f"\n=== " + _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S") + " ===\n")
    def write(self, s): self.s.write(s); self.s.flush(); self.f.write(s); self.f.flush()
    def flush(self): pass
sys.stdout = sys.stderr = _Log()

try:
    import glfw
    from OpenGL.GL import *
    from OpenGL.GLU import *
    from PIL import Image
except ImportError as e:
    print(f"Missing: {e}")
    print("pip install glfw PyOpenGL PyOpenGL_accelerate pillow numpy")
    input("Press Enter..."); sys.exit(1)

# EA-GM optional — lege src/ (EA-Graphics-Manager) neben den Viewer
try:
    from src.EA_Image.ea_image_main import EAImage as _; del _
    print("EA-GM verfügbar (alle PAL8 Texturen aktiv)")
except ImportError:
    print("HINWEIS: src/EA_Image nicht gefunden — nur fmt=125 Texturen werden geladen")

# ── Binary helpers ─────────────────────────────────────────────────────────────
def u32(d, o): return struct.unpack_from('<I', d, o)[0] if o+4 <= len(d) else 0
def u16(d, o): return struct.unpack_from('<H', d, o)[0] if o+2 <= len(d) else 0
def f32(d, o): return struct.unpack_from('<f', d, o)[0] if o+4 <= len(d) else 0.0

# ── Morton-Unswizzle (reversebox wenn verfuegbar, sonst pure Python) ──────
try:
    from reversebox.image.swizzling.swizzle_morton import unswizzle_morton as _rb_morton
    def unswizzle_morton(data, w, h, bpp): return _rb_morton(bytes(data), w, h, bpp)
    print("reversebox Morton-Decoder aktiv (schnell)")
except ImportError:
    print("HINWEIS: reversebox nicht gefunden — langsamer Python-Fallback")
    def _morton_index(t, w, h):
        n1 = n2 = 1; n3 = n4 = 0; iw = w; ih = h
        while iw > 1 or ih > 1:
            if iw > 1: n3 += n2*(t&1); t >>= 1; n2 *= 2; iw >>= 1
            if ih > 1: n4 += n1*(t&1); t >>= 1; n1 *= 2; ih >>= 1
        return n4*w + n3
    def unswizzle_morton(data, w, h, bpp):
        bds = 1 if bpp <= 8 else bpp // 8
        out = bytearray(len(data))
        for t in range(w * h):
            idx = _morton_index(t, w, h); dst = bds*idx; src = bds*t
            out[dst:dst+bds] = data[src:src+bds]
        return bytes(out)

# ── SHPX Decoder (EA-GM für Palette-Lokalisierung, inline Morton-Unswizzle) ───
try:
    import tempfile as _tempfile
    from src.EA_Image.ea_image_main import EAImage as _EAImage
    _EAGM_AVAILABLE = True
except ImportError:
    _EAGM_AVAILABLE = False

def decode_shpx(blob):
    """Dekodiert einen SHPX-Blob → (w, h, rgba_array) oder None."""
    if len(blob) < 0x80: return None
    fmt = blob[0x70]
    w = u16(blob, 0x74); h = u16(blob, 0x76)
    if w == 0 or h == 0: return None
    po = 0x80; n = w * h

    if fmt == 125:  # BGRA8888 Morton
        if po + n*4 > len(blob): return None
        raw = unswizzle_morton(blob[po:po+n*4], w, h, 32)
        pix = np.frombuffer(raw, dtype=np.uint8).reshape(n, 4)
        arr = np.zeros((h, w, 4), dtype=np.uint8)
        arr[np.arange(n)//w, np.arange(n)%w] = pix[:, [2,1,0,3]]
        arr[:,:,3] = 255
        return w, h, arr

    elif fmt == 123:  # PAL8 Morton — EA-GM für Palette
        if not _EAGM_AVAILABLE: return None
        with _tempfile.NamedTemporaryFile(suffix='.shpx', delete=False) as tf:
            tf.write(blob); tf_path = tf.name
        try:
            ea = _EAImage()
            with open(tf_path,'rb') as f:
                status,_ = ea.check_file_signature_and_size(f)
                if status != 'OK': return None
                f.seek(0)
                ea.parse_header(f, tf_path, os.path.basename(tf_path))
                f.seek(16)
                ea.parse_directory(f)
                ea.parse_bin_attachments(f)
        finally:
            os.unlink(tf_path)

        for e in ea.dir_entry_list:
            pal_raw = None
            n_colors = 0
            for att in e.bin_attachments_list:
                if att.h_record_id == 42 and att.raw_data:
                    pal_raw = att.raw_data
                    n_colors = len(pal_raw) // 4
                    break
            if pal_raw is None or n_colors == 0: return None


            raw = unswizzle_morton(blob[po:po+n], w, h, 8)
            pix = np.frombuffer(raw, dtype=np.uint8)[:n]
            pal = np.frombuffer(pal_raw, dtype=np.uint8).reshape(n_colors, 4)
            # RGB=0 → Alpha-only Maske → kein Swap; sonst BGRA→RGBA
            order = [0,1,2,3] if int(pal[:,:3].max()) == 0 else [2,1,0,3]
            arr = pal[np.clip(pix, 0, n_colors-1).reshape(h, w)][:,:,order].copy().astype(np.uint8)
            return w, h, arr

    return None

# ── NV2A Tristrip-Decoder ──────────────────────────────────────────────────────
def decode_nv2a(idx, vc):
    tris=[]; s=[]
    for v in idx:
        if v >= vc: s=[]; continue
        if s and v == s[-1]:
            for j in range(len(s)-2):
                a,b,c=s[j],s[j+1],s[j+2]
                if a!=b and b!=c and a!=c:
                    tris.append((a,b,c) if j%2==0 else (a,c,b))
            s=[v]
        else: s.append(v)
    for j in range(len(s)-2):
        a,b,c=s[j],s[j+1],s[j+2]
        if a!=b and b!=c and a!=c:
            tris.append((a,b,c) if j%2==0 else (a,c,b))
    return tris

# ── ART.cpt Textur-Loader ──────────────────────────────────────────────────────
def load_art_textures(art_path):
    d = open(art_path,'rb').read()
    textures = {}
    p = 0; idx = 0
    while True:
        np_ = d.find(b'SHPX', p)
        if np_ < 0: break
        sz = u32(d, np_+4)
        if sz >= 16 and np_+sz <= len(d):
            r = decode_shpx(d[np_:np_+sz])
            if r: textures[idx] = r
        p = np_+4; idx += 1
    print(f"  ART.cpt: {idx} SHPX, {len(textures)} dekodiert")
    return textures

# ── Desc→ART-SHPX Mapping ─────────────────────────────────────────────────────
def build_mapping(cpt_paths, art_path):
    dart = open(art_path,'rb').read()
    art_nodes_off = u32(dart, 0x0c)
    shpx_offs_art = []
    p = 0
    while True:
        np_ = dart.find(b'SHPX', p)
        if np_ < 0: break
        shpx_offs_art.append(np_); p = np_+4

    def get_si(art_idx):
        if art_idx == 0: return -1
        sp = u32(dart, art_nodes_off + art_idx*128 + 0x70)
        return shpx_offs_art.index(sp) if sp in shpx_offs_art else -1

    mapping = {}
    for ci, cpt_path in enumerate(sorted(cpt_paths)):
        if not os.path.exists(cpt_path): continue
        dc = open(cpt_path,'rb').read()
        shpx_base = dc.find(b'SHPX'); shpx_base = shpx_base if shpx_base>0 else len(dc)
        ldata_off=u32(dc,0x1c); ldata_cnt=u32(dc,0x20)
        vtx0=u32(dc,0x4c); ib0=u32(dc,0x50); vc0=(ib0-vtx0)//28
        ic0=0; p=ib0
        while p+2<=shpx_base:
            if u16(dc,p)>=vc0: break
            ic0+=1; p+=2
        sm0_end=ib0+ic0*2; sm1_table=u32(dc,sm0_end+4)
        found=-1
        for sc in range(sm1_table,shpx_base-16,4):
            if u32(dc,sc)!=0: continue
            vp=u32(dc,sc+4)
            if vp!=sc+16: continue
            ip2=u32(dc,sc+8); vc2=u16(dc,sc+12); ic2=u16(dc,sc+14)
            if not(vp<ip2<shpx_base) or vc2==0 or vc2>10000 or ic2==0: continue
            if math.isfinite(f32(dc,vp)) and -500<f32(dc,vp)<500: found=sc; break
        if found<0: continue
        descs=[]
        pos=found
        while pos+16<=shpx_base:
            if u32(dc,pos)!=0: break
            vp=u32(dc,pos+4); ip2=u32(dc,pos+8); vc2=u16(dc,pos+12); ic2=u16(dc,pos+14)
            if vp!=pos+16 or not(vp<ip2<shpx_base) or vc2==0 or vc2>10000 or ic2==0: break
            if not(math.isfinite(f32(dc,vp)) and -500<f32(dc,vp)<500): break
            descs.append(pos); pos=((ip2+ic2*2+15)&~15)+8
        dhi={pos:di for di,pos in enumerate(descs)}
        entries=[]
        for i in range(ldata_cnt):
            base=ldata_off+i*20; v0=u32(dc,base); v2=u32(dc,base+8); v3=u32(dc,base+12)
            if v0==0: continue
            hdr=v3+8
            if hdr not in dhi: continue
            entries.append((dhi[hdr], get_si(u32(dc,v2+0x7c))))
        entries.sort()
        last=-1
        for di,si in entries:
            if si>=0: last=si
            if last>=0: mapping[(ci,di)]=last
        # Lücken füllen
        last=-1
        for di in range(len(descs)):
            v=mapping.get((ci,di),-1)
            if v>=0: last=v
            elif last>=0: mapping[(ci,di)]=last
        print(f"  c{ci}: {len(descs)}descs {sum(1 for k in mapping if k[0]==ci)}gemappt")
    return mapping

# ── CPT Geometrie + UV laden ───────────────────────────────────────────────────
def load_geometry(cpt_paths, mapping):
    all_verts=[]; all_uvs=[]; all_tris=[]; all_tt=[]; voff=0
    for ci, path in enumerate(sorted(cpt_paths)):
        if not os.path.exists(path): continue
        d=open(path,'rb').read()
        shpx_base=d.find(b'SHPX'); shpx_base=shpx_base if shpx_base>0 else len(d)
        vtx0=u32(d,0x4c); ib0=u32(d,0x50); vc0=(ib0-vtx0)//28
        ic0=0; p=ib0
        while p+2<=shpx_base:
            if u16(d,p)>=vc0: break
            ic0+=1; p+=2
        sm0_end=ib0+ic0*2; sm1_table=u32(d,sm0_end+4)
        found=-1
        for sc in range(sm1_table,shpx_base-16,4):
            if u32(d,sc)!=0: continue
            vp=u32(d,sc+4)
            if vp!=sc+16: continue
            ip2=u32(d,sc+8); vc2=u16(d,sc+12); ic2=u16(d,sc+14)
            if not(vp<ip2<shpx_base) or vc2==0 or vc2>10000 or ic2==0: continue
            if math.isfinite(f32(d,vp)) and -500<f32(d,vp)<500: found=sc; break
        if found<0: continue
        S=28; pos=found; di=0; nv=0
        while pos+16<=shpx_base:
            if u32(d,pos)!=0: break
            vp=u32(d,pos+4); ip2=u32(d,pos+8); vc2=u16(d,pos+12); ic2=u16(d,pos+14)
            if vp!=pos+16 or not(vp<ip2<shpx_base) or vc2==0 or vc2>10000 or ic2==0: break
            if not(math.isfinite(f32(d,vp)) and -500<f32(d,vp)<500): break
            ti=mapping.get((ci,di),-1)
            for i in range(vc2):
                b=vp+i*S
                all_verts.append([f32(d,b),f32(d,b+4),f32(d,b+8)])
                all_uvs.append([f32(d,b+20), f32(d,b+24)])
            for a,b,c in decode_nv2a([u16(d,ip2+j*2) for j in range(ic2)],vc2):
                if a<vc2 and b<vc2 and c<vc2:
                    all_tris.append([a+voff,b+voff,c+voff]); all_tt.append(ti)
            voff+=vc2; nv+=vc2; di+=1; pos=((ip2+ic2*2+15)&~15)+8
        print(f"  {os.path.basename(path)}: {nv}v {di}descs")
    if not all_verts: return None,None,None,None,None
    V=np.array(all_verts,dtype=np.float32); UV=np.array(all_uvs,dtype=np.float32)
    T=np.array(all_tris,dtype=np.int32); TT=np.array(all_tt,dtype=np.int32)
    vn=np.zeros_like(V)
    if len(T):
        e1=V[T[:,1]]-V[T[:,0]]; e2=V[T[:,2]]-V[T[:,0]]
        fn=np.cross(e1,e2); l=np.linalg.norm(fn,axis=1,keepdims=True); l[l==0]=1; fn/=l
        np.add.at(vn,T[:,0],fn); np.add.at(vn,T[:,1],fn); np.add.at(vn,T[:,2],fn)
        l2=np.linalg.norm(vn,axis=1,keepdims=True); l2[l2==0]=1; vn/=l2
    print(f"  Gesamt: {len(V)}v {len(T)}t")
    return V,UV,T,TT,vn.astype(np.float32)

# ── File dialogs ───────────────────────────────────────────────────────────────
def ask_file(title, ft=None):
    import tkinter as tk; from tkinter import filedialog
    r=tk.Tk(); r.withdraw(); r.attributes('-topmost',True)
    res=filedialog.askopenfilename(title=title,filetypes=ft or [("All","*.*")],parent=r)
    r.destroy(); return res or None

def ask_files(title, ft=None):
    import tkinter as tk; from tkinter import filedialog
    r=tk.Tk(); r.withdraw(); r.attributes('-topmost',True)
    res=filedialog.askopenfilenames(title=title,filetypes=ft or [("All","*.*")],parent=r)
    r.destroy(); return list(res)

# ── App ────────────────────────────────────────────────────────────────────────
class App:
    def __init__(self):
        self.verts=self.uvs=self.tris=self.tri_tex=self.normals=None
        self.gl_tex={}; self.art_data={}
        self.rot_x=45.0; self.rot_y=20.0; self.pan_x=0.0; self.pan_y=0.0; self.zoom=1.0
        self.center=np.zeros(3); self.scale=1.0
        self.mb_l=self.mb_r=False; self.last_pos=(0,0)
        self.status="G=Geo  A=ART.cpt  ESC=quit"; self.W=1280; self.H=800
        self.pending=None; self.vbo=None; self.ibo=None; self.ibo_n=0
        self.cpt_paths=[]; self.art_path=None

    def setup_gl(self):
        glEnable(GL_DEPTH_TEST); glEnable(GL_TEXTURE_2D)
        glEnable(GL_LIGHTING); glEnable(GL_LIGHT0)
        glEnable(GL_COLOR_MATERIAL); glEnable(GL_NORMALIZE)
        glEnable(GL_BLEND); glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_ALPHA_TEST); glAlphaFunc(GL_GREATER, 0.1)
        glColorMaterial(GL_FRONT_AND_BACK,GL_AMBIENT_AND_DIFFUSE)
        glLightfv(GL_LIGHT0,GL_POSITION,[1,2,3,0])
        glLightfv(GL_LIGHT0,GL_DIFFUSE,[1,1,1,1])
        glLightfv(GL_LIGHT0,GL_AMBIENT,[0.4,0.4,0.4,1])
        glClearColor(0.07,0.08,0.11,1); glShadeModel(GL_SMOOTH)

    def upload_textures(self):
        if self.gl_tex: glDeleteTextures(list(self.gl_tex.values())); self.gl_tex={}
        for idx,(w,h,arr) in self.art_data.items():
            tid=glGenTextures(1); glBindTexture(GL_TEXTURE_2D,tid)
            glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR_MIPMAP_LINEAR)
            glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_REPEAT)
            glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_REPEAT)
            glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA,w,h,0,GL_RGBA,GL_UNSIGNED_BYTE,np.flipud(arr).tobytes())
            glGenerateMipmap(GL_TEXTURE_2D); self.gl_tex[idx]=tid
        print(f"  {len(self.gl_tex)} GL-Texturen hochgeladen")

    def set_geo(self,V,UV,T,TT,N):
        self.verts=V; self.uvs=UV; self.tris=T; self.tri_tex=TT; self.normals=N
        self.center=V.mean(axis=0)
        # Radius = maximale Ausdehnung vom Zentrum in alle Richtungen
        self.radius=float(np.linalg.norm(V-self.center,axis=1).max())
        horiz=np.abs(V[:,:2]-self.center[:2]).max()
        self.scale=1.0/max(horiz,1e-6)
        self.rot_x=45; self.rot_y=20; self.pan_x=self.pan_y=0; self.zoom=1.0
        self.vbo=self.ibo=None
        print(f"  Level-Radius: {self.radius:.1f} units")

    def draw(self):
        glClear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT)
        if self.verts is None: return
        glViewport(0,0,self.W,self.H)
        glMatrixMode(GL_PROJECTION); glLoadIdentity()
        # Far-plane = 4× Level-Radius in Welt-Koordinaten / scale, Near = far/10000
        r = getattr(self,'radius',500.0)
        far  = max(r * self.scale * 4.0 / max(self.zoom,0.001), 20.0)
        near = far / 10000.0
        gluPerspective(60,self.W/max(self.H,1),near,far)
        glMatrixMode(GL_MODELVIEW); glLoadIdentity()
        glTranslatef(self.pan_x*0.004,self.pan_y*0.004,-3/max(self.zoom,0.001))
        glRotatef(self.rot_x,1,0,0); glRotatef(self.rot_y,0,0,1)
        glScalef(self.scale,self.scale,self.scale)
        #glMultMatrixf([1,0,0,0, 0,0,1,0, 0,-1,0,0, 0,0,0,1])
        glMultMatrixf([1,0,0,0, 0,0,-1,0, 0,1,0,0, 0,0,0,1])
        glTranslatef(-self.center[0],-self.center[1],-self.center[2])
        glLightfv(GL_LIGHT0,GL_POSITION,[1,2,3,0])

        if self.gl_tex and self.tri_tex is not None:
            glEnable(GL_TEXTURE_2D); glColor3f(1,1,1)
            glEnableClientState(GL_VERTEX_ARRAY)
            glEnableClientState(GL_NORMAL_ARRAY)
            glEnableClientState(GL_TEXTURE_COORD_ARRAY)
            from collections import defaultdict

            # Transparenz-Check: Alpha-only Texturen (art_data RGB=0)
            def is_transparent(tx):
                if tx not in self.art_data: return False
                w2,h2,arr=self.art_data[tx]
                return int(arr[:,:,:3].max())==0

            groups=defaultdict(list)
            for ti,tx in enumerate(self.tri_tex): groups[tx].append(ti)

            def render_group(tx, tlist):
                glBindTexture(GL_TEXTURE_2D, self.gl_tex.get(tx,0))
                tri_arr=self.tris[tlist].flatten()
                vd=self.verts[tri_arr].astype(np.float32)
                nd=self.normals[tri_arr].astype(np.float32)
                ud=self.uvs[tri_arr].astype(np.float32)
                glVertexPointer(3,GL_FLOAT,0,vd.tobytes())
                glNormalPointer(GL_FLOAT,0,nd.tobytes())
                glTexCoordPointer(2,GL_FLOAT,0,ud.tobytes())
                glDrawArrays(GL_TRIANGLES,0,len(tri_arr))

            # Pass 1: opake Texturen (Z-Buffer schreiben)
            glDepthMask(GL_TRUE)
            for tx,tlist in groups.items():
                if not is_transparent(tx):
                    render_group(tx, tlist)

            # Pass 2: transparente Texturen (kein Z-Buffer schreiben)
            glDepthMask(GL_FALSE)
            for tx,tlist in groups.items():
                if is_transparent(tx):
                    render_group(tx, tlist)
            glDepthMask(GL_TRUE)

            glDisableClientState(GL_VERTEX_ARRAY)
            glDisableClientState(GL_NORMAL_ARRAY)
            glDisableClientState(GL_TEXTURE_COORD_ARRAY)
            glDisable(GL_TEXTURE_2D)
        else:
            glColor3f(0.72,0.65,0.54)
            glEnableClientState(GL_VERTEX_ARRAY); glEnableClientState(GL_NORMAL_ARRAY)
            flat=self.tris.flatten()
            glVertexPointer(3,GL_FLOAT,0,self.verts[flat].astype(np.float32).tobytes())
            glNormalPointer(GL_FLOAT,0,self.normals[flat].astype(np.float32).tobytes())
            glDrawArrays(GL_TRIANGLES,0,len(flat))
            glDisableClientState(GL_VERTEX_ARRAY); glDisableClientState(GL_NORMAL_ARRAY)

app=App()

def _reload_geo():
    if not app.cpt_paths: return
    if app.art_path and os.path.exists(app.art_path):
        print("  Baue Mapping..."); m=build_mapping(app.cpt_paths,app.art_path)
    else:
        m={}
    r=load_geometry(app.cpt_paths,m)
    if r[0] is not None: app.set_geo(*r)

def cb_resize(w,h): app.W,app.H=w,max(h,1)
def cb_mb(win,btn,act,mod):
    if btn==glfw.MOUSE_BUTTON_LEFT: app.mb_l=(act==glfw.PRESS)
    if btn==glfw.MOUSE_BUTTON_RIGHT: app.mb_r=(act==glfw.PRESS)
    if act==glfw.PRESS: app.last_pos=glfw.get_cursor_pos(win)
def cb_cur(win,x,y):
    dx=x-app.last_pos[0]; dy=y-app.last_pos[1]; app.last_pos=(x,y)
    if app.mb_l: app.rot_y+=dx*0.4; app.rot_x+=dy*0.4
    elif app.mb_r: app.pan_x+=dx; app.pan_y-=dy
def cb_scroll(win,xo,yo): app.zoom*=1.1 if yo>0 else 0.9
def cb_key(win,key,sc,act,mod):
    if act not in (glfw.PRESS,glfw.REPEAT): return
    if key in (glfw.KEY_ESCAPE,glfw.KEY_Q): glfw.set_window_should_close(win,True)
    elif key==glfw.KEY_R: app.rot_x=45;app.rot_y=20;app.pan_x=app.pan_y=0;app.zoom=1
    elif key==glfw.KEY_T: app.rot_x=-89;app.rot_y=0
    elif key==glfw.KEY_F: app.rot_x=0;app.rot_y=0
    elif key==glfw.KEY_I: app.rot_x=45;app.rot_y=30
    elif key==glfw.KEY_G: app.pending='geo'
    elif key==glfw.KEY_A: app.pending='art'

def main():
    if not glfw.init(): sys.exit(1)
    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR,2)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR,1)
    win=glfw.create_window(1280,800,"MOHF Viewer",None,None)
    if not win: glfw.terminate(); sys.exit(1)
    glfw.make_context_current(win); glfw.swap_interval(1)
    glfw.set_framebuffer_size_callback(win,cb_resize)
    glfw.set_mouse_button_callback(win,cb_mb)
    glfw.set_cursor_pos_callback(win,cb_cur)
    glfw.set_scroll_callback(win,cb_scroll)
    glfw.set_key_callback(win,cb_key)
    app.setup_gl()
    print("MOHF Viewer  |  G=Geo  A=ART.cpt  R=Reset  T=Top  F=Front  ESC=Quit")

    while not glfw.window_should_close(win):
        glfw.poll_events()
        if app.pending:
            kind=app.pending; app.pending=None
            try:
                if kind=='geo':
                    paths=ask_files('CPT-Dateien',[('CPT','*.cpt'),('Alle','*.*')])
                    if paths:
                        app.cpt_paths=sorted(paths)
                        print(f"Lade {len(app.cpt_paths)} CPTs..."); _reload_geo()
                        if app.verts is not None:
                            glfw.set_window_title(win,f"MOHF Viewer [{len(app.verts)}v {len(app.tris)}t]")
                elif kind=='art':
                    path=ask_file('ART.cpt laden',[('CPT','*.cpt'),('Alle','*.*')])
                    if path:
                        app.art_path=path
                        print(f"Lade Texturen aus {os.path.basename(path)}...")
                        app.art_data=load_art_textures(path)
                        app.upload_textures()
                        if app.cpt_paths:
                            print("  Rebuild Mapping..."); _reload_geo()
            except Exception as e:
                import traceback; traceback.print_exc(); print(f"Fehler: {e}")
        app.draw(); glfw.swap_buffers(win)
    glfw.terminate()

if __name__=='__main__': main()
