#!/usr/bin/env python3
"""
MOHF Viewer - OpenGL via GLFW                          Version: 2026-05-28
py -m pip install glfw PyOpenGL PyOpenGL_accelerate pillow numpy
py mohf_viewer_gl.py

━━━ CONTROLS ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  LMB drag = rotate    RMB drag = pan    Scroll = zoom
  R = reset   T = top   F = front   I = iso   ESC = quit

  L = Load Level folder  (auto: BPD terrain + all CDB chunks + CPT textures)
  O = Load BPD           (single BPD, replaces BPD layer)
  C = Load CPT textures  (one or more CPT files)
  D = Load CDB folder    (all *_C_c*.cdb in folder)
  M = Load MSH           (additive, adds to MSH list -- local space, no WorldPos yet)
  Z = Clear MSH list

  B = Toggle BPD layer   (terrain/floor, tristrip decoded, span-filtered)
  X = Toggle CDB layer   (collision wireframe, SecA edges, full 3D incl. walls)
  P = Toggle MSH layer   (green, local space at origin)

━━━ CURRENT STATUS (2026-05-28) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  WORKING:
  ✓ BPD terrain loader  -- tristrip with AA-duplicate restart markers,
                           stride=24B (XYZ only, +12..+23 = runtime fields),
                           span filter <40m removes cross-strip artifacts,
                           two-sided lighting
  ✓ CDB collision wireframe -- SecA EdgeList (edge_count × 16B),
                               GL_LINES via vertex array, all 7/8 chunks merged
  ✓ CPT texture decode  -- SHPX fmt 123 (paletted) and 125 (RGBA)
  ✓ MSH loader          -- local-space props, additive list
  ✓ Level folder auto-loader (L key) -- finds *_P.bpd, *_ART*.cpt, *_C_c*.cdb
  ✓ All levels 1_1..1_4, 2_1..2_3 load without crash

  OPEN / NEXT STEPS:
  ✗ BPD texturing       -- SubMesh centroid-nearest matching implemented but
                           CPT texture indices don't align yet (BPD untextured)
  ✗ MSH WorldPos        -- EMT Section-A Prolog type2 records contain WorldPos
                           as int32/65536 fixed-point (X,Y at +08,+0C; Z at +10).
                           Need to parse emt_X_Y.emt, read type2 records,
                           and translate/rotate MSH at load time.
                           Approx transform: world = emt_units * 2.115 - 232 (X)
                                                     emt_units * 1.624 - 179 (Y)
                           (derived from 29 data points only, needs refinement)
  ✗ BPD remaining zags  -- a few SubMesh strips still have wrong winding;
                           root cause: strip-restart detection misses some
                           skip-1 duplicates (A,?,A pattern, 127 occurrences)

━━━ FORMAT NOTES ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  BPD header pairs (11 × 8B at file start):
    [2] SubMesh table offset + count (20 entries × 12B: tex,centroid_cnt,centroid_ptr)
    [3] Vertex buffer offset + count  (stride 24B, only +0..+11 = XYZ float3)
    IB  = SubMesh_table_end .. vtx_off  (uint16 tristrip with AA-restart markers)

  CDB header (56B = 14 × uint32 LE):
    [08]=vtx_count  [10]=edge_count  [14]=poly_count  [18]=leaf_count  [1C]=node_count
    [24]=SecA_off   [28]=SecB_off    [2C]=SecC_off    [30]=SecD_off
    VtxBuf @ 0x38: (vtx_count+5) × float3 world-space
    SecA EdgeList: edge_count × 16B  (VtxPtr0, VtxPtr1, PlaneA, PlaneB)

  EMT WorldPos (emt_X_Y.emt, Section-A Prolog):
    Records: type(u16) + size(u16) + payload
    type=2, size=32: [+00..+07]=Pad, [+08]=X int32/65536, [+0C]=Y int32/65536,
                     [+10]=Z int32/65536, [+14]=Yf float, [+18]=SinH, [+1C]=CosH
    29 records in emt_1_1.emt, X:0..220, Y:0..255 (level-grid units)
"""

import struct, os, sys, math
import numpy as np

# ── Logging to file ───────────────────────────────────────────────────────────
import logging, traceback
_log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mohf_viewer.log')
logging.basicConfig(
    filename=_log_path, filemode='w',
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('mohf')
# also mirror to console
_ch = logging.StreamHandler(sys.stdout)
_ch.setLevel(logging.DEBUG)
log.addHandler(_ch)
log.info(f"Log: {_log_path}")

try:
    import glfw
    from OpenGL.GL import *
    from OpenGL.GLU import *
    from PIL import Image
except ImportError as e:
    log.error(f"Missing: {e}")
    print(f"Missing: {e}")
    print("Run: py -m pip install glfw PyOpenGL PyOpenGL_accelerate pillow numpy")
    input("Press Enter to exit...")
    sys.exit(1)

# ── Binary helpers ────────────────────────────────────────────────────────────
def u32(d,o): return struct.unpack_from('<I',d,o)[0]
def u16(d,o): return struct.unpack_from('<H',d,o)[0]
def f32(d,o): return struct.unpack_from('<f',d,o)[0]

# ── BPD loader ────────────────────────────────────────────────────────────────
def load_bpd(path):
    log.info(f"load_bpd: {path}")
    with open(path,'rb') as f: d=f.read()
    log.info(f"  size={len(d)}")
    sub_off=u32(d,0x10); sub_cnt=u32(d,0x14)
    vtx_off=u32(d,0x18); vtx_cnt=u32(d,0x1C)
    log.info(f"  sub_off=0x{sub_off:x} sub_cnt={sub_cnt} vtx_off=0x{vtx_off:x} vtx_cnt={vtx_cnt}")
    ib_off = sub_off + sub_cnt*12
    ib_cnt = (vtx_off - ib_off)//2
    log.info(f"  ib_off=0x{ib_off:x} ib_cnt={ib_cnt}")

    verts = np.zeros((vtx_cnt,3),dtype=np.float32)
    for i in range(vtx_cnt):
        b=vtx_off+i*24
        if b+12<=len(d):
            verts[i]=[f32(d,b),f32(d,b+4),f32(d,b+8)]

    strip=[u16(d,ib_off+i*2) for i in range(ib_cnt) if ib_off+i*2+2<=len(d)]
    # Tristrips with degenerate (duplicate consecutive) indices as strip restarts.
    # Pattern: [...,A,A,...] -> strip ends before first A, new strip starts after second A.
    tris=[]
    def decode_strip(s):
        for i in range(len(s)-2):
            a,b,c=s[i],s[i+1],s[i+2]
            if a!=b and b!=c and a!=c and a<vtx_cnt and b<vtx_cnt and c<vtx_cnt:
                if i%2==0: tris.append((a,b,c))
                else:       tris.append((a,c,b))
    cur=[]; i=0; n=len(strip)
    while i < n:
        v=strip[i]
        if v>=vtx_cnt: cur=[]; i+=1; continue
        if cur and cur[-1]==v:  # consecutive duplicate = restart
            decode_strip(cur); cur=[]; i+=1; continue
        cur.append(v); i+=1
    decode_strip(cur)
    # Remove artifacts: large-span triangles and needle triangles (huge span, tiny area)
    def tri_span(a,b,c):
        xs=[verts[a,0],verts[b,0],verts[c,0]]; ys=[verts[a,1],verts[b,1],verts[c,1]]
        return max(max(xs)-min(xs), max(ys)-min(ys))
    def tri_area(a,b,c):
        e1=verts[b]-verts[a]; e2=verts[c]-verts[a]
        return float(np.linalg.norm(np.cross(e1,e2)))/2
    tris=[t for t in tris if tri_span(*t)<40 and
          not (tri_span(*t)>15 and tri_area(*t)<10)]

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

    log.info(f"  done: {len(verts)} verts {len(tris_arr)} tris")
    return verts, tris_arr, np.array(vtx_tex,dtype=np.int32), vnorm

# ── CDB loader ────────────────────────────────────────────────────────────────
def load_cdb_chunk(path):
    """Load one CDB chunk. Returns (verts Nx3, edges Mx2) from SecA EdgeList."""
    log.info(f"load_cdb_chunk: {path}")
    with open(path,'rb') as f: d=f.read()
    if len(d) < 56: return None, None

    vtx_count  = u32(d, 8)
    edge_count = u32(d, 16)
    secA_off   = u32(d, 36)
    vtx_start  = 0x38
    total_verts = vtx_count + 5
    log.info(f"  vtx={vtx_count} edges={edge_count} secA=0x{secA_off:x}")

    # Vertex buffer
    verts = np.zeros((total_verts, 3), dtype=np.float32)
    for i in range(total_verts):
        b = vtx_start + i*12
        if b+12 <= len(d):
            verts[i] = [f32(d,b), f32(d,b+4), f32(d,b+8)]

    # SecA EdgeList: each edge = VtxPtr0(4B) + VtxPtr1(4B) + 8B
    edges = []
    for i in range(edge_count):
        base = secA_off + i*16
        if base+8 > len(d): break
        vp0 = u32(d, base)
        vp1 = u32(d, base+4)
        vi0 = (vp0 - vtx_start) // 12
        vi1 = (vp1 - vtx_start) // 12
        if 0 <= vi0 < total_verts and 0 <= vi1 < total_verts:
            edges.append((vi0, vi1))

    if not edges:
        return verts[:vtx_count], np.zeros((0,2), dtype=np.int32)

    log.info(f"  done: {len(verts[:vtx_count])} verts {len(edges)} edges")
    return verts[:vtx_count], np.array(edges, dtype=np.int32)


def load_cdb_dir(folder):
    """Load all *_C_c*.cdb chunks, merge into one edge-wireframe mesh."""
    files = os.listdir(folder)
    chunks = sorted(os.path.join(folder,f) for f in files
                    if f.lower().endswith('.cdb') and '_c_c' in f.lower())
    if not chunks:
        chunks = sorted(os.path.join(folder,f) for f in files
                        if f.lower().endswith('.cdb') and
                        not f.lower().endswith('_c.cdb'))
    if not chunks:
        log.warning(f"No CDB chunks found in {folder}")
        return None, None

    all_v = []; all_e = []; offset = 0
    for path in chunks:
        v, e = load_cdb_chunk(path)
        if v is None or len(v) == 0: continue
        all_v.append(v)
        if e is not None and len(e):
            # clamp edge indices to valid range before offsetting
            valid = (e[:,0] < len(v)) & (e[:,1] < len(v))
            e = e[valid]
            if len(e):
                all_e.append(e + offset)
        offset += len(v)   # offset by actual returned vtx count
        log.info(f"  {os.path.basename(path)}: {len(v)} verts, {len(e) if e is not None else 0} edges")

    if not all_v:
        return None, None

    verts = np.vstack(all_v)
    edges = np.vstack(all_e) if all_e else np.zeros((0,2), dtype=np.int32)
    log.info(f"CDB total: {len(verts)} verts {len(edges)} edges")
    return verts, edges


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
    if not av: return None,None
    v=np.array(av,dtype=np.float32); t=np.array(at,dtype=np.int32)
    vn=np.zeros((len(v),3),dtype=np.float32)
    if len(t):
        e1=v[t[:,1]]-v[t[:,0]]; e2=v[t[:,2]]-v[t[:,0]]
        fn=np.cross(e1,e2); l=np.linalg.norm(fn,axis=1,keepdims=True)
        l[l==0]=1; fn/=l
        for i,(a,b,c) in enumerate(t):
            vn[a]+=fn[i]; vn[b]+=fn[i]; vn[c]+=fn[i]
        l2=np.linalg.norm(vn,axis=1,keepdims=True); l2[l2==0]=1; vn/=l2
    return v, t

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

# ── Level folder auto-loader ──────────────────────────────────────────────────
def find_level_files(folder):
    """Scan folder for BPD, CPT chunks, and CDB chunks. Returns full paths."""
    try:
        files = os.listdir(folder)
    except Exception:
        return None, [], []
    bpd   = next((os.path.join(folder,f) for f in sorted(files)
                  if f.lower().endswith('_p.bpd')), None)
    cpts  = sorted(os.path.join(folder,f) for f in files
                   if f.lower().endswith('.cpt') and '_art' in f.lower())
    cdbs  = sorted(os.path.join(folder,f) for f in files
                   if f.lower().endswith('.cdb') and '_c_c' in f.lower())
    if not cdbs:
        cdbs = sorted(os.path.join(folder,f) for f in files
                      if f.lower().endswith('.cdb') and
                      not f.lower().endswith('_c.cdb'))
    return bpd, cpts, cdbs

# ── File dialog helpers ───────────────────────────────────────────────────────
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

def ask_dir(title):
    import tkinter as tk
    from tkinter import filedialog
    r=tk.Tk(); r.withdraw(); r.attributes('-topmost',True)
    result=filedialog.askdirectory(title=title,parent=r)
    r.destroy()
    return result or None

# ── GL mesh helper ────────────────────────────────────────────────────────────
def compute_normals(verts, tris):
    vn = np.zeros((len(verts),3), dtype=np.float32)
    if len(tris) == 0: return vn
    e1 = verts[tris[:,1]] - verts[tris[:,0]]
    e2 = verts[tris[:,2]] - verts[tris[:,0]]
    fn = np.cross(e1, e2)
    l  = np.linalg.norm(fn, axis=1, keepdims=True); l[l==0]=1; fn/=l
    for i,(a,b,c) in enumerate(tris):
        vn[a]+=fn[i]; vn[b]+=fn[i]; vn[c]+=fn[i]
    l2 = np.linalg.norm(vn, axis=1, keepdims=True); l2[l2==0]=1; vn/=l2
    return vn

# ── App state ─────────────────────────────────────────────────────────────────
class App:
    def __init__(self):
        # BPD layer
        self.bpd_verts   = None
        self.bpd_tris    = None
        self.bpd_vtx_tex = None
        self.bpd_normals = None
        self.show_bpd    = True

        # CDB layer
        self.cdb_verts   = None
        self.cdb_edges   = None
        self.show_cdb    = True

        # MSH list (additive)
        self.msh_list    = []   # list of (verts, tris, normals)
        self.show_msh    = True

        # Textures
        self.gl_textures = {}
        self.tex_data    = {}

        # Camera
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

        self.status = "L=Level  O=BPD  C=CPT  D=CDB  M=MSH  B/X/P=toggle"
        self.info   = ""
        self.W = 1280; self.H = 800
        self.pending_load = None

    def setup_gl(self):
        glEnable(GL_DEPTH_TEST)
        glDisable(GL_CULL_FACE)
        glLightModeli(GL_LIGHT_MODEL_TWO_SIDE, GL_TRUE)
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

    def _all_verts(self):
        parts = []
        if self.bpd_verts is not None: parts.append(self.bpd_verts)
        if self.cdb_verts is not None: parts.append(self.cdb_verts)
        for v,_,_ in self.msh_list:    parts.append(v)
        return np.vstack(parts) if parts else None

    def recenter(self):
        all_v = self._all_verts()
        if all_v is None: return
        self.center = all_v.mean(axis=0)
        horiz = np.abs(all_v[:,:2] - self.center[:2]).max()
        self.scale  = 1.0 / max(horiz, 1e-6)
        self._xy_range = all_v[:,:2].max() - all_v[:,:2].min()
        self._z_range  = all_v[:,2].max()  - all_v[:,2].min()
        self.rot_x=-45; self.rot_y=20; self.pan_x=0; self.pan_y=0; self.zoom=1.0
        self._update_info()

    def _update_info(self):
        lines = []
        if self.bpd_verts is not None:
            lines.append(f"BPD: {len(self.bpd_verts)}v {len(self.bpd_tris)}t")
        if self.cdb_verts is not None:
            lines.append(f"CDB: {len(self.cdb_verts)}v {len(self.cdb_edges)}e")
        if self.msh_list:
            tv = sum(len(m[0]) for m in self.msh_list)
            tt = sum(len(m[1]) for m in self.msh_list)
            lines.append(f"MSH: {len(self.msh_list)} files  {tv}v {tt}t")
        self.info = "  ".join(lines)

    # ── draw one mesh layer ───────────────────────────────────────────────────
    def _draw_solid(self, verts, tris, normals, vtx_tex=None, color=(0.72,0.65,0.52)):
        if verts is None or tris is None or len(tris) == 0: return
        use_tex = bool(self.gl_textures) and vtx_tex is not None

        from collections import defaultdict
        if vtx_tex is not None:
            mat_tris = defaultdict(list)
            for a,b,c in tris:
                mat_tris[int(vtx_tex[a])].append((a,b,c))
        else:
            mat_tris = {-1: list(map(tuple, tris))}

        if use_tex: glEnable(GL_TEXTURE_2D)

        for ti, group in mat_tris.items():
            if not group: continue
            arr = np.array(group, dtype=np.int32)
            idx = arr.ravel()

            if use_tex and ti in self.gl_textures:
                glBindTexture(GL_TEXTURE_2D, self.gl_textures[ti])
                glColor3f(1,1,1)
            else:
                if use_tex: glDisable(GL_TEXTURE_2D)
                glColor3f(*color)

            flat_v = verts[idx].astype(np.float32)
            glEnableClientState(GL_VERTEX_ARRAY)
            glVertexPointer(3, GL_FLOAT, 0, flat_v.tobytes())
            if normals is not None:
                flat_n = normals[idx].astype(np.float32)
                glEnableClientState(GL_NORMAL_ARRAY)
                glNormalPointer(GL_FLOAT, 0, flat_n.tobytes())
            glDrawArrays(GL_TRIANGLES, 0, len(flat_v))
            glDisableClientState(GL_VERTEX_ARRAY)
            if normals is not None:
                glDisableClientState(GL_NORMAL_ARRAY)

            if use_tex and ti not in self.gl_textures:
                glEnable(GL_TEXTURE_2D)

        if use_tex: glDisable(GL_TEXTURE_2D)

    def _draw_cdb_edges(self, verts, edges, color=(0.3,0.6,1.0), alpha=0.5):
        if verts is None or edges is None or len(edges) == 0: return
        # safety clamp
        valid = (edges[:,0] < len(verts)) & (edges[:,1] < len(verts))
        edges = edges[valid]
        if len(edges) == 0: return
        glDisable(GL_LIGHTING)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glLineWidth(0.8)
        glColor4f(*color, alpha)
        # Build flat line vertex array: [v0a, v0b, v1a, v1b, ...]
        line_verts = verts[edges.ravel()].astype(np.float32)
        glEnableClientState(GL_VERTEX_ARRAY)
        glVertexPointer(3, GL_FLOAT, 0, line_verts.tobytes())
        glDrawArrays(GL_LINES, 0, len(line_verts))
        glDisableClientState(GL_VERTEX_ARRAY)
        glDisable(GL_BLEND)
        glEnable(GL_LIGHTING)

    def draw(self):
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        all_v = self._all_verts()
        if all_v is None: return

        glViewport(0,0,self.W,self.H)
        glMatrixMode(GL_PROJECTION); glLoadIdentity()
        gluPerspective(45.0, self.W/max(self.H,1), 0.001, 100.0)

        glMatrixMode(GL_MODELVIEW); glLoadIdentity()
        glTranslatef(self.pan_x*0.004, self.pan_y*0.004, -3.0/self.zoom)
        glRotatef(self.rot_x,1,0,0)
        glRotatef(self.rot_y,0,1,0)
        glScalef(self.scale, self.scale, self.scale)
        # MOHF axis remap: game X→GL X, game Y→GL -Z, game Z→GL Y
        glMultMatrixf([1,0,0,0,  0,0,-1,0,  0,1,0,0,  0,0,0,1])
        glTranslatef(-self.center[0],-self.center[1],-self.center[2])

        # BPD layer (solid, textured)
        if self.show_bpd:
            self._draw_solid(self.bpd_verts, self.bpd_tris,
                             self.bpd_normals, self.bpd_vtx_tex)

        # CDB layer (edge wireframe)
        if self.show_cdb:
            self._draw_cdb_edges(self.cdb_verts, self.cdb_edges,
                                 color=(0.3,0.6,1.0), alpha=0.5)

        # MSH list (solid, green tint)
        if self.show_msh:
            for v,t,n in self.msh_list:
                self._draw_solid(v, t, n, vtx_tex=None, color=(0.4,0.8,0.4))

app = App()

# ── GLFW callbacks ────────────────────────────────────────────────────────────
def cb_resize(win, w, h):
    app.W=w; app.H=max(h,1)

def cb_mouse_button(win, btn, action, mods):
    if btn==glfw.MOUSE_BUTTON_LEFT:  app.mb_left  = (action==glfw.PRESS)
    if btn==glfw.MOUSE_BUTTON_RIGHT: app.mb_right = (action==glfw.PRESS)
    if action==glfw.PRESS: app.last_pos = glfw.get_cursor_pos(win)

def cb_cursor(win, x, y):
    dx = x - app.last_pos[0]; dy = y - app.last_pos[1]
    app.last_pos = (x,y)
    if app.mb_left:  app.rot_y += dx*0.4; app.rot_x += dy*0.4
    elif app.mb_right: app.pan_x += dx;   app.pan_y -= dy

def cb_scroll(win, xoff, yoff):
    app.zoom *= 1.1 if yoff>0 else 0.9

def cb_key(win, key, sc, action, mods):
    if action not in (glfw.PRESS, glfw.REPEAT): return
    if key in (glfw.KEY_ESCAPE, glfw.KEY_Q):
        glfw.set_window_should_close(win, True)
    elif key==glfw.KEY_R:
        app.rot_x=-45; app.rot_y=20; app.pan_x=0; app.pan_y=0; app.zoom=1.0
    elif key==glfw.KEY_T:  app.rot_x=-89; app.rot_y=0
    elif key==glfw.KEY_F:  app.rot_x=0;   app.rot_y=0
    elif key==glfw.KEY_I:  app.rot_x=-45; app.rot_y=30
    elif key==glfw.KEY_B:
        app.show_bpd = not app.show_bpd
        print(f"BPD layer: {'ON' if app.show_bpd else 'OFF'}")
    elif key==glfw.KEY_X:
        app.show_cdb = not app.show_cdb
        print(f"CDB layer: {'ON' if app.show_cdb else 'OFF'}")
    elif key==glfw.KEY_P:
        app.show_msh = not app.show_msh
        print(f"MSH layer: {'ON' if app.show_msh else 'OFF'}")
    elif key==glfw.KEY_Z:
        app.msh_list.clear(); app._update_info()
        print("MSH list cleared")
    elif key==glfw.KEY_L: app.pending_load=('level',None)
    elif key==glfw.KEY_O: app.pending_load=('bpd',None)
    elif key==glfw.KEY_C: app.pending_load=('cpt',None)
    elif key==glfw.KEY_D: app.pending_load=('cdb',None)
    elif key==glfw.KEY_M: app.pending_load=('msh',None)

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    if not glfw.init():
        print("GLFW init failed"); sys.exit(1)

    glfw.window_hint(glfw.CONTEXT_VERSION_MAJOR, 2)
    glfw.window_hint(glfw.CONTEXT_VERSION_MINOR, 1)
    win = glfw.create_window(1280, 800, "MOHF Viewer", None, None)
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

    print("MOHF Viewer ready")
    print("  L = Load Level folder  (BPD + CDB chunks + CPT auto)")
    print("  O = Load BPD   C = Load CPT   D = Load CDB folder   M = Load MSH")
    print("  B = toggle BPD   X = toggle CDB   P = toggle MSH   Z = clear MSH")
    print("  R=reset  T=top  F=front  I=iso  LMB=rotate  RMB=pan  Scroll=zoom")

    while not glfw.window_should_close(win):
        glfw.poll_events()

        if app.pending_load:
            kind, _ = app.pending_load
            app.pending_load = None
            try:
                if kind == 'level':
                    folder = ask_dir("Select Level folder")
                    if folder:
                        bpd_path, cpt_paths, cdb_paths = find_level_files(folder)
                        print(f"Level folder: {folder}")
                        if bpd_path:
                            print(f"  BPD: {os.path.basename(bpd_path)}")
                            v,t,vt,n = load_bpd(bpd_path)
                            app.bpd_verts=v; app.bpd_tris=t
                            app.bpd_vtx_tex=vt; app.bpd_normals=n
                        else:
                            print("  No BPD found")
                        if cdb_paths:
                            print(f"  CDB chunks: {len(cdb_paths)}")
                            cv, ce = load_cdb_dir(folder)
                            if cv is not None:
                                app.cdb_verts=cv; app.cdb_edges=ce
                        if cpt_paths:
                            print(f"  CPT: {len(cpt_paths)} files")
                            app.tex_data = load_cpt_textures(cpt_paths)
                            app.upload_textures()
                            print(f"  {len(app.tex_data)} textures loaded")
                        app.recenter()
                        app._update_info()
                        name = os.path.basename(folder)
                        glfw.set_window_title(win, f"MOHF Viewer  [{name}]")
                        print(f"  Done. {app.info}")

                elif kind == 'bpd':
                    path = ask_file("Open BPD",[("BPD","*.bpd"),("All","*.*")])
                    if path:
                        print(f"Loading {os.path.basename(path)}...")
                        v,t,vt,n = load_bpd(path)
                        app.bpd_verts=v; app.bpd_tris=t
                        app.bpd_vtx_tex=vt; app.bpd_normals=n
                        app.recenter(); app._update_info()
                        glfw.set_window_title(win, f"MOHF Viewer  [{os.path.basename(path)}]")
                        print(f"  {len(v)} verts, {len(t)} tris")

                elif kind == 'cdb':
                    folder = ask_dir("Select folder with CDB chunks")
                    if folder:
                        print(f"Loading CDB from {folder}...")
                        cv, ce = load_cdb_dir(folder)
                        if cv is not None:
                            app.cdb_verts=cv; app.cdb_edges=ce
                            app.recenter(); app._update_info()
                            print(f"  {len(cv)} verts, {len(ce)} edges total")

                elif kind == 'cpt':
                    paths = ask_files("Open CPT",[("CPT","*.cpt"),("All","*.*")])
                    if paths:
                        print(f"Loading {len(paths)} CPT files...")
                        app.tex_data = load_cpt_textures(paths)
                        app.upload_textures()
                        print(f"  {len(app.tex_data)} textures loaded")

                elif kind == 'msh':
                    path = ask_file("Open MSH",[("MSH","*.msh"),("All","*.*")])
                    if path:
                        print(f"Loading {os.path.basename(path)}...")
                        v,t = load_msh(path)
                        if v is not None:
                            n = compute_normals(v,t)
                            app.msh_list.append((v,t,n))
                            app.recenter(); app._update_info()
                            print(f"  {len(v)} verts, {len(t)} tris  (total MSH: {len(app.msh_list)})")

            except Exception as e:
                log.exception(f"Load error ({kind}): {e}")
                print(f"Error: {e}")

        app.draw()
        glfw.swap_buffers(win)

    glfw.terminate()

if __name__=='__main__':
    try:
        main()
    except Exception:
        log.exception("Unhandled exception in main")
        raise
