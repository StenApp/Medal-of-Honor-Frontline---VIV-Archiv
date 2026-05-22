#!/usr/bin/env python3
"""
MOHF Audio Extractor v2
Medal of Honor: Frontline — Xbox & PS2
Benötigt: SX.EXE (EA Sound eXchange v3.01.01) + vgmstream-cli.exe
"""

import os, struct, shutil, subprocess, threading, tempfile
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ── Konstanten ────────────────────────────────────────────────────────────────

AUDIO_EXTS = {'.abk', '.ast', '.asf', '.mus', '.mpf'}
BG   = '#1a1a2e'
CARD = '#16213e'
DARK = '#0f0f1a'
ACC  = '#e94560'
FG   = '#eaeaea'
DIM  = '#8892a0'
GRN  = '#4ade80'
MONO = ('Consolas', 9)

# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def fmt_size(n):
    if n >= 1024*1024: return f"{n/1024/1024:.1f} MB"
    if n >= 1024:      return f"{n/1024:.0f} KB"
    return f"{n} B"

def extract_bnkl(abk_path, out_bnk_path):
    with open(abk_path, 'rb') as f:
        data = f.read()
    if data[0:2] != b'AB' or data[2] not in (0x08, 0x09) or data[3] != 0x00:
        return False, "Kein AEMS-Format"
    modoff = struct.unpack_from('<I', data, 0x1C)[0]
    if modoff == 0 or modoff + 4 >= len(data):
        return False, "Stream-Typ (kein BNKl)"
    if data[modoff:modoff+4] != b'BNKl':
        return False, "Kein BNKl"
    with open(out_bnk_path, 'wb') as f:
        f.write(data[modoff:])
    return True, ""

def is_stream_abk(abk_path):
    try:
        with open(abk_path, 'rb') as f:
            data = f.read(0x40)
        if data[0:2] != b'AB' or data[2] not in (0x08, 0x09):
            return False
        return struct.unpack_from('<I', data, 0x1C)[0] == 0
    except Exception:
        return False

def get_companion(abk_path, exts):
    base = os.path.splitext(abk_path)[0]
    for ext in exts:
        for e in (ext, ext.upper()):
            p = base + e
            if os.path.exists(p):
                return p
    return None

def run_sx(sx_exe, input_path, output_dir, log):
    fname = os.path.basename(input_path)
    base  = os.path.splitext(fname)[0]
    tmp   = os.path.join(output_dir, fname)
    copied = os.path.abspath(input_path) != os.path.abspath(tmp)
    if copied:
        shutil.copy2(input_path, tmp)
    before = set(os.listdir(output_dir))
    try:
        r = subprocess.run([sx_exe, '-wave', '-onetomany', fname],
                           capture_output=True, text=True, timeout=120, cwd=output_dir)
    except Exception as e:
        log(f"    SX FEHLER: {e}")
        if copied:
            try: os.remove(tmp)
            except Exception: pass
        return
    after = set(os.listdir(output_dir))
    renamed = 0
    for nf in sorted(after - before):
        if nf.lower().endswith('.wav'):
            # Leere WAVs löschen (< 100 Bytes = nur WAV-Header, kein Audio)
            p = os.path.join(output_dir, nf)
            if os.path.getsize(p) < 100:
                try: os.remove(p)
                except Exception: pass
                continue
            renamed += 1; continue
        src = os.path.join(output_dir, nf)
        parts = nf.rsplit('.', 1)
        dst = os.path.join(output_dir,
              f"{base}_{int(parts[1]):03d}.wav"
              if len(parts)==2 and parts[1].isdigit() else nf+'.wav')
        try:
            os.rename(src, dst)
            # Leere WAV nach Umbenennung prüfen
            if os.path.getsize(dst) < 100:
                os.remove(dst)
            else:
                renamed += 1
        except Exception: pass
    if renamed: log(f"    → {renamed} WAV(s)")
    if copied:
        try: os.remove(tmp)
        except Exception: pass

def run_vgmstream(vgm_exe, input_path, output_dir, log):
    fname = os.path.basename(input_path)
    base  = os.path.splitext(fname)[0]
    r = subprocess.run([vgm_exe, '-m', input_path],
                       capture_output=True, text=True, timeout=30)
    num = 1
    for line in r.stdout.splitlines():
        if 'stream count:' in line:
            try: num = int(line.split(':')[1].strip())
            except Exception: pass
    log(f"    {fname}: {num} Stream(s)")
    for i in range(1, num+1):
        out = os.path.join(output_dir, f"{base}_{i:03d}.wav")
        subprocess.run([vgm_exe, '-s', str(i), '-o', out, input_path],
                       capture_output=True, timeout=120)
    log(f"    → {num} WAV(s)")

def get_abk_version(abk_path):
    try:
        with open(abk_path, 'rb') as f:
            hdr = f.read(4)
        if hdr[0:2] == b'AB' and hdr[2] in (0x08, 0x09):
            return hdr[2]
    except Exception:
        pass
    return 0


def scan_schl_offsets(ast_path):
    """Scannt AST nach SCHl-Positionen. Überspringt Null-Padding zwischen Streams."""
    SCHL = 0x5343486C
    offsets = []
    try:
        with open(ast_path, 'rb') as f:
            data = f.read()
        pos  = 0
        size = len(data)
        while pos + 8 <= size:
            block_id   = struct.unpack_from('>I', data, pos)[0]
            block_size = struct.unpack_from('<I', data, pos+4)[0]

            # Null-Padding überspringen (Alignment zwischen Streams)
            if block_id == 0 and block_size == 0:
                # Suche nächste Nicht-Null-Position (16-Byte-Alignment)
                skip = pos + 8
                while skip + 4 <= size and struct.unpack_from('>I', data, skip)[0] == 0:
                    skip += 4
                pos = skip
                continue

            if block_size == 0 or block_size > size:
                break
            if block_id == SCHL:
                offsets.append(pos)
            pos += block_size
    except Exception:
        pass
    return offsets


def split_and_convert_ast(sx_exe, vgm_exe, abk_path, ast_path, output_dir, log):
    """Teilt eine multi-stream AST auf und konvertiert jeden Stream zu WAV."""
    offsets = scan_schl_offsets(ast_path)

    if not offsets:
        log(f"    Keine SCHl-Blöcke — verarbeite als einzelnen Stream")
        if vgm_exe:
            run_vgmstream(vgm_exe, ast_path, output_dir, log)
        else:
            run_sx(sx_exe, ast_path, output_dir, log)
        return

    ast_size = os.path.getsize(ast_path)
    base     = os.path.splitext(os.path.basename(ast_path))[0]
    log(f"    {len(offsets)} Streams: {[hex(o) for o in offsets]}")

    with open(ast_path, 'rb') as f:
        ast_data = f.read()

    converted = 0
    with tempfile.TemporaryDirectory() as tmp_dir:
        for i, off in enumerate(offsets):
            end   = offsets[i+1] if i+1 < len(offsets) else ast_size
            chunk = ast_data[off:end]
            if chunk[:4] != b'SCHl':
                continue
            tmp_schl = os.path.join(tmp_dir, f"{base}_{i+1:02d}.schl")
            with open(tmp_schl, 'wb') as f:
                f.write(chunk)
            out_wav = os.path.join(output_dir, f"{base}_{i+1:02d}.wav")
            fname   = os.path.basename(tmp_schl)
            if vgm_exe:
                r = subprocess.run([vgm_exe, '-o', out_wav, tmp_schl],
                                   capture_output=True, timeout=60)
                if r.returncode == 0:
                    converted += 1; continue
            before = set(os.listdir(tmp_dir))
            subprocess.run([sx_exe, '-wave', '-onetomany', fname],
                           capture_output=True, timeout=60, cwd=tmp_dir)
            for nf in sorted(set(os.listdir(tmp_dir)) - before):
                try:
                    shutil.copy2(os.path.join(tmp_dir, nf), out_wav)
                    converted += 1; break
                except Exception:
                    pass

    pairs = len(offsets) // 2
    log(f"    → {converted} WAV(s) ({pairs} Stereo-Paare L+R)")


def run_vgmstream_mpf(vgm_exe, mpf_path, mus_path, output_dir, log):
    mpf_name = os.path.basename(mpf_path)
    base     = os.path.splitext(mpf_name)[0]
    mus_name = base + '.mus'
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_mpf = os.path.join(tmp_dir, mpf_name)
        tmp_mus = os.path.join(tmp_dir, mus_name)
        shutil.copy2(mpf_path, tmp_mpf)
        try:
            os.link(mus_path, tmp_mus)
        except Exception:
            shutil.copy2(mus_path, tmp_mus)
        r = subprocess.run([vgm_exe, '-m', tmp_mpf],
                           capture_output=True, text=True, timeout=30)
        num = 1
        for line in r.stdout.splitlines():
            if 'stream count:' in line:
                try: num = int(line.split(':')[1].strip())
                except Exception: pass
        log(f"    {mpf_name}: {num} Segment(s)")
        for i in range(1, num+1):
            out = os.path.join(output_dir, f"{base}_seg{i:02d}.wav")
            subprocess.run([vgm_exe, '-s', str(i), '-o', out, tmp_mpf],
                           capture_output=True, timeout=120)
        log(f"    → {num} WAV(s)")

def level_from_mpf_name(mpf_name):
    """'1_1M.mpf' → ('1', '1_1') oder None."""
    base = os.path.splitext(mpf_name)[0]
    if len(base) >= 4 and base[-1].upper() == 'M' and '_' in base:
        core = base[:-1]   # '1_1'
        parts = core.split('_')
        if len(parts) == 2:
            try:
                int(parts[0]); int(parts[1])
                return parts[0], core
            except Exception:
                pass
    return None

def scan_audio_dirs(root):
    result = []
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        if any(os.path.splitext(f)[1].lower() in AUDIO_EXTS for f in files):
            result.append(dirpath)
    return result

# ── Prozessierung ─────────────────────────────────────────────────────────────

def cleanup_empty_dirs(base_dir):
    """Löscht leere Unterordner rekursiv (nach oben)."""
    for dirpath, dirs, files in os.walk(base_dir, topdown=False):
        if dirpath == base_dir:
            continue
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
        except Exception:
            pass


def process_dir(sx_exe, vgm_exe, src_dir, mpf_dir, data_root, out_base, log, prog):
    files = os.listdir(src_dir)
    abks  = sorted(f for f in files if f.lower().endswith('.abk'))
    asts  = sorted(f for f in files if f.lower().endswith(('.ast', '.asf')))
    muss  = sorted(f for f in files if f.lower().endswith('.mus'))
    mpfs  = sorted(f for f in files if f.lower().endswith('.mpf'))
    rel   = os.path.relpath(src_dir, data_root) if data_root else os.path.basename(src_dir)
    total = max(len(abks) + len(asts) + len(mpfs), 1)
    idx   = 0

    # ABK
    for abk_file in abks:
        abk_path  = os.path.join(src_dir, abk_file)
        base_name = os.path.splitext(abk_file)[0]
        idx += 1
        prog(f"{rel}/{abk_file}", idx, total)
        out_dir = os.path.join(out_base, rel, base_name)
        os.makedirs(out_dir, exist_ok=True)

        if is_stream_abk(abk_path):
            companion = get_companion(abk_path, ['.ast', '.asf'])
            if companion:
                ext = os.path.splitext(companion)[1].lower()
                log(f"  [STREAM] {abk_file} → {os.path.basename(companion)}")
                if ext == '.asf' and vgm_exe:
                    run_vgmstream(vgm_exe, companion, out_dir, log)
                elif ext == '.ast':
                    split_and_convert_ast(sx_exe, vgm_exe,
                                          abk_path, companion, out_dir, log)
                else:
                    run_sx(sx_exe, companion, out_dir, log)
            else:
                log(f"  [SKIP]   {abk_file} — kein AST/ASF")
        else:
            tmp_bnk = os.path.join(out_dir, base_name + '.bnk')
            ok, err = extract_bnkl(abk_path, tmp_bnk)
            if ok:
                log(f"  [BANK]   {abk_file}")
                run_sx(sx_exe, tmp_bnk, out_dir, log)
                try: os.remove(tmp_bnk)
                except Exception: pass
            else:
                log(f"  [SKIP]   {abk_file} — {err}")

    # Standalone ASF/AST
    abk_bases = {os.path.splitext(f)[0].lower() for f in abks}
    for af in asts:
        if os.path.splitext(af)[0].lower() in abk_bases:
            continue
        a_path  = os.path.join(src_dir, af)
        a_base  = os.path.splitext(af)[0]
        ext     = os.path.splitext(af)[1].lower()
        idx += 1
        prog(f"{rel}/{af}", idx, total)
        out_dir = os.path.join(out_base, rel, a_base)
        os.makedirs(out_dir, exist_ok=True)
        log(f"  [{'ASF' if ext=='.asf' else 'AST'}]    {af}")
        if vgm_exe:
            run_vgmstream(vgm_exe, a_path, out_dir, log)
        else:
            run_sx(sx_exe, a_path, out_dir, log)

    # MPF-Suche: im Ordner selbst + optionaler MPF-Ordner
    all_mpfs = [(os.path.join(src_dir, m), m) for m in mpfs]
    if mpf_dir and os.path.isdir(mpf_dir):
        for f in sorted(os.listdir(mpf_dir)):
            if not f.lower().endswith('.mpf'): continue
            info = level_from_mpf_name(f)
            if not info: continue
            mission, level = info
            lvl_rel = os.path.join(mission, level)
            if rel == lvl_rel or rel.replace('\\','/') == lvl_rel:
                all_mpfs.append((os.path.join(mpf_dir, f), f))

    def find_mus(mpf_name):
        info = level_from_mpf_name(mpf_name)
        if info and data_root:
            mission, level = info
            for fname in ('main.mus', 'MAIN.MUS', 'main.MUS'):
                p = os.path.join(data_root, mission, level, fname)
                if os.path.exists(p): return p
        for mf in muss:
            return os.path.join(src_dir, mf)
        return None

    for mpf_path, mpf_file in all_mpfs:
        idx += 1
        prog(f"{rel}/{mpf_file}", idx, total)
        mus_path = find_mus(mpf_file)
        if not mus_path:
            log(f"  [HINWEIS] {mpf_file} — keine main.mus (MPF aus level.viv extrahieren)")
            continue
        if not vgm_exe:
            log(f"  [SKIP]   {mpf_file} — vgmstream-cli fehlt")
            continue
        out_dir = os.path.join(out_base, rel, 'Musik', os.path.splitext(mpf_file)[0])
        os.makedirs(out_dir, exist_ok=True)
        log(f"  [MPF]    {mpf_file} → {os.path.basename(mus_path)}")
        run_vgmstream_mpf(vgm_exe, mpf_path, mus_path, out_dir, log)

    # MUS ohne MPF
    if muss and not all_mpfs:
        for mf in muss:
            mus_path = os.path.join(src_dir, mf)
            out_dir  = os.path.join(out_base, rel, 'Musik', os.path.splitext(mf)[0])
            os.makedirs(out_dir, exist_ok=True)
            log(f"  [MUS]    {mf} (nur 1 Segment — MPF fehlt)")
            run_sx(sx_exe, mus_path, out_dir, log)


# ── GUI ───────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MOHF Audio Extractor v2")
        self.minsize(780, 600)
        self.configure(bg=BG)
        self._sx_path    = tk.StringVar()
        self._vgm_path   = tk.StringVar()
        self._data_root  = tk.StringVar()
        self._mpf_dir    = tk.StringVar()
        self._output_dir = tk.StringVar()
        self._running    = False
        self._dir_nodes  = {}
        self._checked    = set()
        self._build_ui()

    def _build_ui(self):
        hdr = tk.Frame(self, bg=BG)
        hdr.pack(fill='x', padx=18, pady=(14,2))
        tk.Label(hdr, text="MOHF", font=('Courier New',20,'bold'), fg=ACC, bg=BG).pack(side='left')
        tk.Label(hdr, text=" Audio Extractor", font=('Courier New',20), fg=FG, bg=BG).pack(side='left')
        tk.Label(hdr, text="v2 · Xbox · PS2", font=('Courier New',9), fg=DIM, bg=BG).pack(side='right')
        tk.Frame(self, height=1, bg=ACC).pack(fill='x', padx=18, pady=(2,10))

        # Pfad-Felder
        pf = tk.Frame(self, bg=BG)
        pf.pack(fill='x', padx=18)
        rows = [
            ("SX.EXE",        self._sx_path,    self._browse_sx),
            ("vgmstream-cli", self._vgm_path,   self._browse_vgm),
            ("DATA-Ordner",   self._data_root,  self._browse_data),
            ("MPF-Ordner",    self._mpf_dir,    self._browse_mpf),
            ("Ausgabe",       self._output_dir, self._browse_output),
        ]
        for i, (lbl, var, cmd) in enumerate(rows):
            tk.Label(pf, text=f"{lbl}:", font=MONO, fg=DIM, bg=BG,
                width=15, anchor='w').grid(row=i, column=0, sticky='w', pady=2)
            tk.Entry(pf, textvariable=var, font=MONO, bg='#0f3460', fg=FG,
                insertbackground=FG, relief='flat', bd=5
                ).grid(row=i, column=1, sticky='ew', padx=4, pady=2)
            tk.Button(pf, text='…', font=MONO, bg=ACC, fg='white',
                activebackground='#c73652', relief='flat', bd=0,
                padx=7, pady=3, cursor='hand2', command=cmd
                ).grid(row=i, column=2, pady=2)
        pf.columnconfigure(1, weight=1)

        # Scan
        sr = tk.Frame(self, bg=BG)
        sr.pack(fill='x', padx=18, pady=(8,4))
        tk.Button(sr, text="🔍  Scannen", font=('Courier New',9,'bold'),
            bg='#0f3460', fg=FG, activebackground='#1a4a80',
            relief='flat', bd=0, padx=14, pady=5, cursor='hand2',
            command=self._scan).pack(side='left')
        tk.Label(sr, text="  Ordner wählen die verarbeitet werden sollen",
            font=MONO, fg=DIM, bg=BG).pack(side='left', padx=8)

        # Treeview
        style = ttk.Style(self)
        style.theme_use('default')
        style.configure('Dark.Treeview', background=DARK, foreground=FG,
            fieldbackground=DARK, rowheight=20, font=MONO, borderwidth=0)
        style.configure('Dark.Treeview.Heading', background=CARD,
            foreground=DIM, font=('Consolas',9,'bold'), relief='flat')
        style.map('Dark.Treeview', background=[('selected',DARK)],
            foreground=[('selected',FG)])
        style.configure('Red.Horizontal.TProgressbar',
            troughcolor=CARD, background=ACC, borderwidth=0)

        tf = tk.Frame(self, bg=DARK)
        tf.pack(fill='both', expand=True, padx=18, pady=(0,4))
        self._tree = ttk.Treeview(tf, style='Dark.Treeview',
            columns=('types','size'), show='tree headings', selectmode='none')
        self._tree.heading('#0',    text='Ordner')
        self._tree.heading('types', text='Dateitypen')
        self._tree.heading('size',  text='Gesamt')
        self._tree.column('#0',    width=300, stretch=True)
        self._tree.column('types', width=220, anchor='w')
        self._tree.column('size',  width=80,  anchor='e', stretch=False)
        vsb = ttk.Scrollbar(tf, orient='vertical', command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')
        self._tree.bind('<Button-1>', self._on_click)

        # Auswahl-Buttons
        br = tk.Frame(self, bg=BG)
        br.pack(fill='x', padx=18, pady=(0,4))
        for txt, cmd in [("☑ Alle", self._check_all), ("☐ Keine", self._check_none)]:
            tk.Button(br, text=txt, font=MONO, bg=CARD, fg=DIM,
                relief='flat', bd=0, padx=10, pady=3, cursor='hand2',
                command=cmd).pack(side='left', padx=(0,4))
        self._sel_lbl = tk.Label(br, text="", font=MONO, fg=DIM, bg=BG)
        self._sel_lbl.pack(side='left', padx=8)

        # Fortschritt
        prf = tk.Frame(self, bg=BG)
        prf.pack(fill='x', padx=18, pady=(0,4))
        self._prog_lbl = tk.Label(prf, text="", font=MONO, fg=DIM, bg=BG, anchor='w')
        self._prog_lbl.pack(fill='x')
        self._progress = ttk.Progressbar(prf, style='Red.Horizontal.TProgressbar', mode='determinate')
        self._progress.pack(fill='x', pady=(2,0))

        # Log
        lf = tk.Frame(self, bg=CARD)
        lf.pack(fill='x', padx=18, pady=(0,6))
        self._log_txt = tk.Text(lf, font=MONO, bg=CARD, fg=FG, height=5,
            relief='flat', bd=6, state='disabled', wrap='none')
        lsb = ttk.Scrollbar(lf, command=self._log_txt.yview)
        self._log_txt.configure(yscrollcommand=lsb.set)
        lsb.pack(side='right', fill='y')
        self._log_txt.pack(fill='both')

        # Start
        brf = tk.Frame(self, bg=BG)
        brf.pack(fill='x', padx=18, pady=(0,14))
        self._btn = tk.Button(brf, text="▶  EXTRAHIEREN",
            font=('Courier New',11,'bold'), bg=ACC, fg='white',
            activebackground='#c73652', relief='flat', bd=0,
            padx=22, pady=9, cursor='hand2', command=self._start)
        self._btn.pack(side='left')
        self._status = tk.Label(brf, text="", font=MONO, fg=GRN, bg=BG)
        self._status.pack(side='left', padx=12)

    # ── Browse ────────────────────────────────────────────────────────────────

    def _browse_sx(self):
        p = filedialog.askopenfilename(title="SX.EXE",
            filetypes=[("SX.EXE","sx.exe SX.EXE"),("Alle","*.*")])
        if p: self._sx_path.set(p)

    def _browse_vgm(self):
        p = filedialog.askopenfilename(title="vgmstream-cli.exe",
            filetypes=[("EXE","*.exe"),("Alle","*.*")])
        if p: self._vgm_path.set(p)

    def _browse_data(self):
        p = filedialog.askdirectory(title="DATA-Ordner (Spiel-Root)")
        if p: self._data_root.set(p); self._scan()

    def _browse_mpf(self):
        p = filedialog.askdirectory(title="MPF-Ordner (optional, alle MPFs flach)")
        if p: self._mpf_dir.set(p)

    def _browse_output(self):
        p = filedialog.askdirectory(title="Ausgabe-Ordner")
        if p: self._output_dir.set(p)

    # ── Tree ──────────────────────────────────────────────────────────────────

    def _scan(self):
        root = self._data_root.get().strip()
        if not root or not os.path.isdir(root): return
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._dir_nodes.clear()
        self._checked.clear()

        dirs = scan_audio_dirs(root)
        for d in dirs:
            rel   = os.path.relpath(d, root)
            parts = rel.replace('\\', '/').split('/')
            parent_iid = ''
            for depth, part in enumerate(parts):
                key = '/'.join(parts[:depth+1])
                if key not in self._dir_nodes:
                    is_leaf = (depth == len(parts)-1)
                    if is_leaf:
                        fs = [f for f in os.listdir(os.path.join(root, key))
                              if os.path.splitext(f)[1].lower() in AUDIO_EXTS]
                        types = ', '.join(sorted(set(
                            os.path.splitext(f)[1].lstrip('.').upper() for f in fs)))
                        size  = sum(os.path.getsize(os.path.join(root, key, f)) for f in fs)
                        sz_str = fmt_size(size)
                    else:
                        types, sz_str = '', ''
                    iid = self._tree.insert(parent_iid, 'end',
                        text=f"☐  {part}",
                        values=(types, sz_str),
                        open=True)
                    self._dir_nodes[key] = iid
                parent_iid = self._dir_nodes['/'.join(parts[:depth+1])]

        self._log_msg(f"Gefunden: {len(dirs)} Ordner mit Audio-Dateien")
        self._update_sel()

    def _on_click(self, event):
        iid = self._tree.identify_row(event.y)
        if not iid or self._tree.identify_column(event.x) != '#0': return
        txt = self._tree.item(iid, 'text')
        check = txt.startswith('☐')
        self._set_node(iid, check)
        self._propagate(iid, check)
        self._update_sel()

    def _set_node(self, iid, check):
        txt = self._tree.item(iid, 'text')
        sym = '☑' if check else '☐'
        self._tree.item(iid, text=sym + txt[1:])
        if check: self._checked.add(iid)
        else:     self._checked.discard(iid)

    def _propagate(self, iid, check):
        for child in self._tree.get_children(iid):
            self._set_node(child, check)
            self._propagate(child, check)

    def _check_all(self):
        for iid in self._dir_nodes.values():
            self._set_node(iid, True)
        self._update_sel()

    def _check_none(self):
        for iid in self._dir_nodes.values():
            self._set_node(iid, False)
        self._update_sel()

    def _update_sel(self):
        # Nur Blatt-Ordner zählen (die mit Dateitypen-Anzeige)
        leaf_checked = sum(1 for k, iid in self._dir_nodes.items()
                           if iid in self._checked and
                           self._tree.item(iid, 'values')[0])
        self._sel_lbl.config(text=f"{leaf_checked} Ordner ausgewählt")

    # ── Log / Fortschritt ────────────────────────────────────────────────────

    def _log_msg(self, msg):
        def _do():
            self._log_txt.configure(state='normal')
            self._log_txt.insert('end', msg + '\n')
            self._log_txt.see('end')
            self._log_txt.configure(state='disabled')
        self.after(0, _do)

    def _set_prog(self, label, cur, total):
        def _do():
            pct = int(cur/total*100) if total else 0
            self._progress['value'] = pct
            self._prog_lbl.config(text=f"{label}  ({cur}/{total})")
        self.after(0, _do)

    # ── Start ────────────────────────────────────────────────────────────────

    def _start(self):
        if self._running: return
        sx   = self._sx_path.get().strip()
        vgm  = self._vgm_path.get().strip()
        data = self._data_root.get().strip()
        mpf  = self._mpf_dir.get().strip()
        out  = self._output_dir.get().strip()

        if not sx or not os.path.isfile(sx):
            messagebox.showerror("Fehler", "SX.EXE nicht gefunden"); return
        if not data or not os.path.isdir(data):
            messagebox.showerror("Fehler", "DATA-Ordner nicht gefunden"); return
        if not out:
            messagebox.showerror("Fehler", "Ausgabe-Ordner fehlt"); return

        # Ausgewählte Leaf-Ordner
        iid_to_path = {v: os.path.join(data, k.replace('/', os.sep))
                       for k, v in self._dir_nodes.items()}
        selected = sorted(p for iid, p in iid_to_path.items()
                          if iid in self._checked and os.path.isdir(p)
                          and self._tree.item(iid,'values')[0])  # nur Blätter

        if not selected:
            messagebox.showwarning("Hinweis", "Keine Ordner ausgewählt"); return

        vgm_exe = vgm if vgm and os.path.isfile(vgm) else None
        mpf_dir = mpf if mpf and os.path.isdir(mpf) else None
        if not vgm_exe:
            self._log_msg("Hinweis: vgmstream-cli fehlt — ASF/MPF mit SX verarbeitet")

        os.makedirs(out, exist_ok=True)
        self._running = True
        self._btn.config(state='disabled', text='⏳ Läuft…')
        self._status.config(text='')
        self._progress['value'] = 0

        threading.Thread(target=self._run,
            args=(sx, vgm_exe, data, mpf_dir, out, selected), daemon=True).start()

    def _run(self, sx, vgm, data_root, mpf_dir, out_base, dirs):
        try:
            total = len(dirs)
            for idx, d in enumerate(dirs):
                rel = os.path.relpath(d, data_root)
                self._log_msg(f"── {rel}")
                process_dir(
                    sx, vgm, d, mpf_dir, data_root, out_base,
                    self._log_msg,
                    lambda lbl, c, t, i=idx, td=total:
                        self._set_prog(lbl, i*100+c, td*100)
                )
            self._log_msg("\n✓ Fertig!")
            cleanup_empty_dirs(out_base)
            self.after(0, lambda: self._status.config(text="✓ Fertig!"))
        except Exception as e:
            self._log_msg(f"\nFEHLER: {e}")
        finally:
            self._running = False
            self.after(0, lambda: self._btn.config(state='normal', text='▶  EXTRAHIEREN'))
            self.after(0, lambda: self._progress.config(value=100))


if __name__ == '__main__':
    App().mainloop()
