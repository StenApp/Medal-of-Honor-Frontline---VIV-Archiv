#!/usr/bin/env python3
"""
MOHF Viewer - OpenGL via GLFW
py -m pip install glfw PyOpenGL PyOpenGL_accelerate pillow numpy
py mohf_viewer_gl.py

Controls:
  LMB drag = rotate    RMB drag = pan    Scroll = zoom
  R = reset   T = top   F = front   I = iso
  O = load BPD   C = load CPT textures   M = load MSH
  ESC = quit
"""

import struct, os, sys, math, threading, ctypes
import numpy as np

import datetime as _dt
class _Log:
    def __init__(self):
        lp=os.path.join(os.path.dirname(os.path.abspath(__file__)),"mohf_viewer.log")
        self.f=open(lp,"a",encoding="utf-8")
        self.s=sys.__stdout__ if hasattr(sys,"__stdout__") else sys.stdout
        self.f.write(f"\n=== "+_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")+" ===\n"); self.f.flush()
    def write(self,s): self.s.write(s); self.s.flush(); self.f.write(s); self.f.flush()
    def flush(self): pass
sys.stdout=sys.stderr=_Log()


try:
    import glfw
    from OpenGL.GL import *
    from OpenGL.GLU import *
    from PIL import Image
except ImportError as e:
    print(f"Missing: {e}")
    print("Run: py -m pip install glfw PyOpenGL PyOpenGL_accelerate pillow numpy")
    input("Press Enter to exit...")
    sys.exit(1)

# ── Binary helpers ────────────────────────────────────────────────────────────
def u32(d,o): return struct.unpack_from('<I',d,o)[0]
def u16(d,o): return struct.unpack_from('<H',d,o)[0]
def f32(d,o): return struct.unpack_from('<f',d,o)[0]


# ── NV2A Tristrip-Decoder ─────────────────────────────────────────────────────
def decode_nv2a(idx, vc):
    tris=[]; s=[]
    for v in idx:
        if v >= vc: s=[]; continue
        if s and v == s[-1]:
            for j in range(len(s)-2):
                a,b,c = s[j],s[j+1],s[j+2]
                if a!=b and b!=c and a!=c:
                    tris.append((a,b,c) if j%2==0 else (a,c,b))
            s=[v]
        else: s.append(v)
    for j in range(len(s)-2):
        a,b,c = s[j],s[j+1],s[j+2]
        if a!=b and b!=c and a!=c:
            tris.append((a,b,c) if j%2==0 else (a,c,b))
    return tris

# ── CPT Geometrie-Loader ──────────────────────────────────────────────────────
def load_cpt_geometry(paths):
    all_verts=[]; all_tris=[]; voff=0
    for path in sorted(paths):
        if not os.path.exists(path): print(f'  fehlt: {path}'); continue
        d = open(path,'rb').read()
        shpx = d.find(b'SHPX'); shpx = shpx if shpx>0 else len(d)
        S=28; vtx0=u32(d,0x4c); ib0=u32(d,0x50)
        vc0=(ib0-vtx0)//28; ic0=0; p=ib0
        while p+2<=shpx:
            v=u16(d,p)
            if v>=vc0 and v!=0: break
            ic0+=1; p+=2
        sm0_end=ib0+ic0*2; found=-1
        for sc in range(sm0_end,shpx-16,4):
            if u32(d,sc)!=0: continue
            vp=u32(d,sc+4)
            if vp!=sc+16: continue
            ip2=u32(d,sc+8); vc2=u16(d,sc+12); ic2=u16(d,sc+14)
            if not(vp<ip2<shpx) or vc2==0 or vc2>10000 or ic2==0 or ic2>100000: continue
            x,y,z=f32(d,vp),f32(d,vp+4),f32(d,vp+8)
            if math.isfinite(x) and -500<x<500 and math.isfinite(y) and -500<y<500 and math.isfinite(z) and -100<z<100:
                found=sc; break
        if found<0: print(f'  {os.path.basename(path)}: keine Geo'); continue
        nv=0; pos=found
        while pos+16<=shpx:
            if u32(d,pos)!=0: break
            vp=u32(d,pos+4); ip2=u32(d,pos+8); vc2=u16(d,pos+12); ic2=u16(d,pos+14)
            if vp!=pos+16 or not(vp<ip2<shpx) or vc2==0 or vc2>10000 or ic2==0 or ic2>100000: break
            if not(math.isfinite(f32(d,vp)) and -500<f32(d,vp)<500): break
            for i in range(vc2):
                all_verts.append([f32(d,vp+i*S),f32(d,vp+i*S+4),f32(d,vp+i*S+8)])
            for a,b,c in decode_nv2a([u16(d,ip2+j*2) for j in range(ic2)],vc2):
                if a<vc2 and b<vc2 and c<vc2:
                    all_tris.append([a+voff,b+voff,c+voff])
            voff+=vc2; nv+=vc2
            pos=((ip2+ic2*2+15)&~15)+8
        print(f'  {os.path.basename(path)}: {nv}v')
    if not all_verts: return None,None,None,None
    V=np.array(all_verts,dtype=np.float32); T=np.array(all_tris,dtype=np.int32)
    ok=np.isfinite(V).all(axis=1); T=T[ok[T[:,0]]&ok[T[:,1]]&ok[T[:,2]]]
    vn=np.zeros_like(V)
    if len(T):
        e1=V[T[:,1]]-V[T[:,0]]; e2=V[T[:,2]]-V[T[:,0]]
        fn=np.cross(e1,e2); l=np.linalg.norm(fn,axis=1,keepdims=True); l[l==0]=1; fn/=l
        np.add.at(vn,T[:,0],fn); np.add.at(vn,T[:,1],fn); np.add.at(vn,T[:,2],fn)
        l2=np.linalg.norm(vn,axis=1,keepdims=True); l2[l2==0]=1; vn/=l2
    print(f'  Gesamt: {len(V)}v {len(T)}t')
    return V, T, np.zeros(len(V),dtype=np.int32), vn.astype(np.float32)

# ── BPD loader ────────────────────────────────────────────────────────────────
def load_bpd(path):
    with open(path,'rb') as f: d=f.read()
    sub_off=u32(d,0x10); sub_cnt=u32(d,0x14)
    vtx_off=u32(d,0x18); vtx_cnt=u32(d,0x1C)
    ib_off = sub_off + sub_cnt*12
    ib_cnt = (vtx_off - ib_off)//2

    verts = np.zeros((vtx_cnt,3),dtype=np.float32)
    for i in range(vtx_cnt):
        b=vtx_off+i*24
        if b+12<=len(d):
            verts[i]=[f32(d,b),f32(d,b+4),f32(d,b+8)]

    strip=[u16(d,ib_off+i*2) for i in range(ib_cnt) if ib_off+i*2+2<=len(d)]
    tris=[]
    for i in range(len(strip)-2):
        a,b,c=strip[i],strip[i+1],strip[i+2]
        if a!=b and b!=c and a!=c and a<vtx_cnt and b<vtx_cnt and c<vtx_cnt:
            if i%2==0: tris.append((a,b,c))
            else:       tris.append((a,c,b))

    tex_groups=[]
    for i in range(sub_cnt):
        base=sub_off+i*12
        if base+12>len(d): continue
        ti=u32(d,base); vc=u32(d,base+4); ptr=u32(d,base+8)
        if ptr==0 or ptr+vc*12>len(d): continue
        cs=[(f32(d,ptr+j*12),f32(d,ptr+j*12+4),f32(d,ptr+j*12+8)) for j in range(vc)]
        tex_groups.append((ti,cs))

    def nearest(x,y,z):
        bd=float('inf'); bt=0
        for ti,cs in tex_groups:
            for cx,cy,cz in cs:
                dd=(x-cx)**2+(y-cy)**2+(z-cz)**2
                if dd<bd: bd=dd; bt=ti
        return bt
    vtx_tex=[nearest(verts[i,0],verts[i,1],verts[i,2]) for i in range(vtx_cnt)]

    tris_arr=np.array(tris,dtype=np.int32)
    vnorm=np.zeros((vtx_cnt,3),dtype=np.float32)
    if len(tris_arr):
        e1=verts[tris_arr[:,1]]-verts[tris_arr[:,0]]
        e2=verts[tris_arr[:,2]]-verts[tris_arr[:,0]]
        fn=np.cross(e1,e2)
        l=np.linalg.norm(fn,axis=1,keepdims=True); l[l==0]=1; fn/=l
        for i,(a,b,c) in enumerate(tris_arr):
            vnorm[a]+=fn[i]; vnorm[b]+=fn[i]; vnorm[c]+=fn[i]
        l2=np.linalg.norm(vnorm,axis=1,keepdims=True); l2[l2==0]=1; vnorm/=l2

    return verts, tris_arr, np.array(vtx_tex,dtype=np.int32), vnorm

# ── MSH loader ────────────────────────────────────────────────────────────────
def load_msh(path):
    with open(path,'rb') as f: d=f.read()
    mc=u32(d,0x1C); mt=u32(d,0x18)
    av=[]; at=[]; off=0
    for mi in range(mc):
        entry=mt+mi*32; mb=u32(d,entry+8)
        if mb==0: continue
        vb=u32(d,mb+12); ib=u32(d,mb+16)
        vc=u16(d,mb+20); ic=u16(d,mb+22)
        if vc==0 or vb==0: continue
        for vi in range(vc):
            b=vb+vi*28
            if b+12<=len(d): av.append([f32(d,b),f32(d,b+4),f32(d,b+8)])
        strip=[u16(d,ib+i*2) for i in range(ic)]
        for i in range(len(strip)-2):
            a,b_,c=strip[i],strip[i+1],strip[i+2]
            if a!=b_ and b_!=c and a!=c:
                if i%2==0: at.append((a+off,b_+off,c+off))
                else:       at.append((a+off,c+off,b_+off))
        off+=vc
    if not av: return None,None,None,None
    v=np.array(av,dtype=np.float32); t=np.array(at,dtype=np.int32)
    vn=np.zeros((len(v),3),dtype=np.float32)
    if len(t):
        e1=v[t[:,1]]-v[t[:,0]]; e2=v[t[:,2]]-v[t[:,0]]
        fn=np.cross(e1,e2); l=np.linalg.norm(fn,axis=1,keepdims=True)
        l[l==0]=1; fn/=l
        for i,(a,b,c) in enumerate(t):
            vn[a]+=fn[i]; vn[b]+=fn[i]; vn[c]+=fn[i]
        l2=np.linalg.norm(vn,axis=1,keepdims=True); l2[l2==0]=1; vn/=l2
    return v, t, np.zeros(len(v),dtype=np.int32), vn

# ── CPT texture decoder ───────────────────────────────────────────────────────
CPT_RANGES=[('c0',0,54),('c1',54,70),('c2',70,110),('c3',110,131),
            ('c4',131,145),('c5',145,186),('c6',186,226)]

def cpt_label_for(path):
    base=os.path.basename(path).lower()
    for label,start,_ in CPT_RANGES:
        if f'_{label}.' in base: return label,start
    return None,None

def decode_shpx(d,off):
    fmt=d[off+0x70]; w=u16(d,off+0x74); h=u16(d,off+0x76)
    if w==0 or h==0: return None,0,0
    po=off+0x80
    n=w*h
    xs=np.zeros(n,dtype=np.int32); ys=np.zeros(n,dtype=np.int32)
    bit=0
    while (1<<bit)<max(w,h):
        if (1<<bit)<w: xs|=((np.arange(n)>>(2*bit))&1)<<bit
        if (1<<bit)<h: ys|=((np.arange(n)>>(2*bit+1))&1)<<bit
        bit+=1
    mask=(xs<w)&(ys<h)
    arr=np.zeros((h,w,4),dtype=np.uint8)
    if fmt==123:
        if po+n+0x40+1024>len(d): return None,w,h
        pix=np.frombuffer(d[po:po+n],dtype=np.uint8)
        pal=np.frombuffer(d[po+n+0x40:po+n+0x40+1024],dtype=np.uint8).reshape(256,4)
        arr[ys[mask],xs[mask]]=pal[pix[mask]]
    elif fmt==125:
        if po+n*4>len(d): return None,w,h
        pix=np.frombuffer(d[po:po+n*4],dtype=np.uint8).reshape(-1,4)
        arr[ys[mask],xs[mask]]=pix[mask][:,[2,1,0,3]]
    else:
        return None,w,h
    # Gamma boost
    rgb=np.power(np.clip(arr[:,:,:3].astype(np.float32)/255,0,1),0.45)*255
    arr[:,:,:3]=rgb.astype(np.uint8)
    return arr,w,h

def load_cpt_textures(paths):
    tex={}
    for path in paths:
        if not os.path.exists(path): continue
        label,base_idx=cpt_label_for(path)
        if label is None: continue
        with open(path,'rb') as f: d=f.read()
        pos=0; li=0
        while True:
            p=d.find(b'SHPX',pos)
            if p<0: break
            arr,w,h=decode_shpx(d,p)
            if arr is not None: tex[base_idx+li]=(w,h,arr)
            pos=p+4; li+=1
    return tex

# ── File dialog (tkinter, runs in main thread) ────────────────────────────────
def ask_file(title, filetypes=None):
    import tkinter as tk
    from tkinter import filedialog
    r=tk.Tk(); r.withdraw(); r.attributes('-topmost',True)
    ft=filetypes or [("All","*.*")]
    result=filedialog.askopenfilename(title=title,filetypes=ft,parent=r)
    r.destroy()
    return result or None

def ask_files(title, filetypes=None):
    import tkinter as tk
    from tkinter import filedialog
    r=tk.Tk(); r.withdraw(); r.attributes('-topmost',True)
    ft=filetypes or [("All","*.*")]
    result=filedialog.askopenfilenames(title=title,filetypes=ft,parent=r)
    r.destroy()
    return list(result)

# ── App state ─────────────────────────────────────────────────────────────────
class App:
    def __init__(self):
        self.verts   = None
        self.tris    = None
        self.vtx_tex = None
        self.normals = None
        self.gl_textures = {}
        self.tex_data    = {}

        self.rot_x = -45.0; self.rot_y = 20.0
        self.pan_x = 0.0;   self.pan_y = 0.0
        self.zoom  = 1.0
        self.center= np.zeros(3)
        self.scale = 1.0
        self._xy_range = 1.0
        self._z_range  = 1.0

        self.mb_left  = False
        self.mb_right = False
        self.last_pos = (0,0)

        self.status   = "O=BPD  G=CPT-Geo  C=CPT-Tex  M=MSH  ESC=quit"
        self.info     = ""
        self.W = 1280; self.H = 800
        self.pending_load = None   # ('bpd'|'msh'|'cpt', path(s))

    def setup_gl(self):
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glEnable(GL_COLOR_MATERIAL)
        glEnable(GL_NORMALIZE)
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
        glLightfv(GL_LIGHT0, GL_POSITION, [1.0, 2.0, 3.0, 0.0])
        glLightfv(GL_LIGHT0, GL_DIFFUSE,  [1.0, 1.0, 1.0, 1.0])
        glLightfv(GL_LIGHT0, GL_AMBIENT,  [0.3, 0.3, 0.3, 1.0])
        glClearColor(0.07, 0.08, 0.11, 1.0)
        glShadeModel(GL_SMOOTH)
        self.vbo_id=None; self.ibo_id=None; self.ibo_count=0

    def upload_textures(self):
        if self.gl_textures:
            glDeleteTextures(list(self.gl_textures.values()))
            self.gl_textures = {}
        for gi,(w,h,arr) in self.tex_data.items():
            tid = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, tid)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR_MIPMAP_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA,w,h,0,GL_RGBA,GL_UNSIGNED_BYTE,
                         np.flipud(arr).tobytes())
            glGenerateMipmap(GL_TEXTURE_2D)
            self.gl_textures[gi] = tid

    def set_geometry(self, verts, tris, vtx_tex, normals):
        self.verts=verts; self.tris=tris
        self.vtx_tex=vtx_tex; self.normals=normals
        if verts is not None and len(verts):
            self.center = verts.mean(axis=0)
            horiz = np.abs(verts[:,:2]-self.center[:2]).max()
            self.scale  = 1.0/max(horiz, 1e-6)
            self._xy_range = verts[:,:2].max() - verts[:,:2].min()
            self._z_range  = verts[:,2].max()  - verts[:,2].min()
        self.rot_x=-45; self.rot_y=20; self.pan_x=0; self.pan_y=0; self.zoom=1.0
        self.vbo_id=None; self.ibo_id=None; self.ibo_count=0
        bb_min=verts.min(axis=0); bb_max=verts.max(axis=0)
        self.info=(f"{len(verts)} verts  {len(tris)} tris\n"
                   f"X {bb_min[0]:.1f}..{bb_max[0]:.1f}\n"
                   f"Y {bb_min[1]:.1f}..{bb_max[1]:.1f}\n"
                   f"Z {bb_min[2]:.1f}..{bb_max[2]:.1f}")


    def _upload_vbo(self):
        if self.verts is None or self.tris is None: return
        vbo_data = np.concatenate([self.verts, self.normals], axis=1).astype(np.float32)
        ibo_data = self.tris.astype(np.uint32).flatten()
        self.ibo_count = len(ibo_data)
        if self.vbo_id is not None:
            glDeleteBuffers(1,[self.vbo_id]); glDeleteBuffers(1,[self.ibo_id])
        ids = glGenBuffers(2)
        self.vbo_id, self.ibo_id = int(ids[0]), int(ids[1])
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo_id)
        glBufferData(GL_ARRAY_BUFFER, vbo_data.nbytes, vbo_data, GL_STATIC_DRAW)
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, self.ibo_id)
        glBufferData(GL_ELEMENT_ARRAY_BUFFER, ibo_data.nbytes, ibo_data, GL_STATIC_DRAW)
        glBindBuffer(GL_ARRAY_BUFFER, 0); glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0)
        print(f'  VBO: {len(self.verts)}v {len(self.tris)}t hochgeladen')

    def draw(self):
        glClear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT)
        if self.verts is None or self.tris is None: return

        if self.vbo_id is None: self._upload_vbo()
        if self.vbo_id is None: return

        glViewport(0,0,self.W,self.H)
        glMatrixMode(GL_PROJECTION); glLoadIdentity()
        gluPerspective(60.0, self.W/max(self.H,1), 0.1, 5000.0)

        glMatrixMode(GL_MODELVIEW); glLoadIdentity()
        glTranslatef(self.pan_x*0.004, self.pan_y*0.004, -3.0/max(self.zoom,0.001))
        glRotatef(self.rot_x,1,0,0)
        glRotatef(self.rot_y,0,0,1)
        glScalef(self.scale,self.scale,self.scale)
        # MOHF → OpenGL: X=X, Y(forward)→-Z, Z(up)→Y
        glMultMatrixf([1,0,0,0, 0,0,1,0, 0,-1,0,0, 0,0,0,1])
        glTranslatef(-self.center[0],-self.center[1],-self.center[2])

        glLightfv(GL_LIGHT0, GL_POSITION, [1.0,2.0,3.0,0.0])
        glLightfv(GL_LIGHT1, GL_POSITION, [-0.5,-1.0,0.3,0.0])
        glColor3f(0.72, 0.65, 0.54)

        STRIDE = 6 * 4
        glBindBuffer(GL_ARRAY_BUFFER, self.vbo_id)
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, self.ibo_id)
        glEnableClientState(GL_VERTEX_ARRAY)
        glEnableClientState(GL_NORMAL_ARRAY)
        glVertexPointer(3, GL_FLOAT, STRIDE, ctypes.c_void_p(0))
        glNormalPointer(GL_FLOAT, STRIDE, ctypes.c_void_p(12))
        glDrawElements(GL_TRIANGLES, self.ibo_count, GL_UNSIGNED_INT, None)
        glDisableClientState(GL_VERTEX_ARRAY)
        glDisableClientState(GL_NORMAL_ARRAY)
        glBindBuffer(GL_ARRAY_BUFFER, 0)
        glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0)

# ── GLFW callbacks ────────────────────────────────────────────────────────────
app = App()

def cb_resize(win, w, h):
    app.W=w; app.H=max(h,1)

def cb_mouse_button(win, btn, action, mods):
    if btn==glfw.MOUSE_BUTTON_LEFT:
        app.mb_left  = (action==glfw.PRESS)
    if btn==glfw.MOUSE_BUTTON_RIGHT:
        app.mb_right = (action==glfw.PRESS)
    if action==glfw.PRESS:
        app.last_pos = glfw.get_cursor_pos(win)

def cb_cursor(win, x, y):
    dx = x - app.last_pos[0]
    dy = y - app.last_pos[1]
    app.last_pos = (x,y)
    if app.mb_left:
        app.rot_y += dx*0.4; app.rot_x += dy*0.4
    elif app.mb_right:
        app.pan_x += dx;     app.pan_y -= dy

def cb_scroll(win, xoff, yoff):
    if yoff>0: app.zoom*=1.1
    else:      app.zoom*=0.9

def cb_key(win, key, sc, action, mods):
    if action not in (glfw.PRESS, glfw.REPEAT): return
    if key in (glfw.KEY_ESCAPE, glfw.KEY_Q):
        glfw.set_window_should_close(win, True)
    elif key==glfw.KEY_R:
        app.rot_x=-45; app.rot_y=20; app.pan_x=0; app.pan_y=0; app.zoom=1.0
    elif key==glfw.KEY_T:
        app.rot_x=-89; app.rot_y=0
    elif key==glfw.KEY_F:
        app.rot_x=0;   app.rot_y=0
    elif key==glfw.KEY_I:
        app.rot_x=-45; app.rot_y=30
    elif key==glfw.KEY_O:
        app.pending_load=('bpd',None)
    elif key==glfw.KEY_G:
        app.pending_load=('cpt_geo',None)
    elif key==glfw.KEY_C:
        app.pending_load=('cpt',None)
    elif key==glfw.KEY_M:
        app.pending_load=('msh',None)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not glfw.init():
        print("GLFW init failed"); sys.exit(1)

    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 2)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 1)
    win = glfw.create_window(1280, 800, "MOHF Viewer  [OpenGL]", None, None)
    if not win:
        glfw.terminate(); print("Window creation failed"); sys.exit(1)

    glfw.make_context_current(win)
    glfw.swap_interval(1)
    glfw.set_framebuffer_size_callback(win, cb_resize)
    glfw.set_mouse_button_callback(win, cb_mouse_button)
    glfw.set_cursor_pos_callback(win, cb_cursor)
    glfw.set_scroll_callback(win, cb_scroll)
    glfw.set_key_callback(win, cb_key)

    app.setup_gl()

    print("MOHF Viewer OpenGL bereit")
    print("O=BPD  G=CPT-Geometrie  C=CPT-Texturen  M=MSH  ESC=quit")
    print("LMB=drehen  RMB=schwenken  Scroll=zoom  R=reset  T=top  F=vorne  I=iso")

    while not glfw.window_should_close(win):
        glfw.poll_events()

        # Handle pending file loads (needs to happen in main thread for tkinter)
        if app.pending_load:
            kind, _ = app.pending_load
            app.pending_load = None
            try:
                if kind=='cpt_geo':
                    paths=ask_files('CPT-Geometrie',[('CPT','*.cpt'),('Alle','*.*')])
                    if paths:
                        print(f'Lade {len(paths)} CPT-Dateien...')
                        v,t,vt,n=load_cpt_geometry(paths)
                        if v is not None:
                            app.set_geometry(v,t,vt,n)
                            app.status=f'CPT {len(v)}v {len(t)}t'
                            glfw.set_window_title(win,f'MOHF Viewer [{len(v)}v {len(t)}t]')
                elif kind=='bpd':
                    path=ask_file("Open BPD",[("BPD","*.bpd"),("All","*.*")])
                    if path:
                        print(f"Loading {os.path.basename(path)}...")
                        v,t,vt,n=load_bpd(path)
                        app.set_geometry(v,t,vt,n)
                        app.status=f"Loaded: {os.path.basename(path)}"
                        glfw.set_window_title(win,f"MOHF Viewer  [{os.path.basename(path)}]")
                        print(f"  {len(v)} verts, {len(t)} tris")
                elif kind=='cpt':
                    paths=ask_files("Open CPT",[("CPT","*.cpt"),("All","*.*")])
                    if paths:
                        print(f"Loading {len(paths)} CPT files...")
                        app.tex_data=load_cpt_textures(paths)
                        app.upload_textures()
                        app.status=f"{len(app.tex_data)} textures loaded"
                        print(f"  {len(app.tex_data)} textures")
                elif kind=='msh':
                    path=ask_file("Open MSH",[("MSH","*.msh"),("All","*.*")])
                    if path:
                        print(f"Loading {os.path.basename(path)}...")
                        v,t,vt,n=load_msh(path)
                        if v is not None:
                            app.set_geometry(v,t,vt,n)
                            app.status=f"Loaded: {os.path.basename(path)}"
                            glfw.set_window_title(win,f"MOHF Viewer  [{os.path.basename(path)}]")
            except Exception as e:
                print(f"Error: {e}")
                app.status=f"Error: {e}"

        app.draw()
        glfw.swap_buffers(win)

    glfw.terminate()

if __name__=='__main__':
    main()
