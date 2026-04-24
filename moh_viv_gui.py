#!/usr/bin/env python3
"""
MOH Frontline PS2/Xbox VIV Extraktor - GUI
Unterstuetzt:
  - C0 FB xx xx (MOH custom format, PS2 und Xbox)
  - BIGF / BIG4 (Standard EA)

Benoetigt: Python 3.6+, tkinter (Standard)
"""

import struct
import os
import sys
import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# ─── VIV Parser ─────────────────────────────────────────────────────────────

def read_u24be(data, off):
    return struct.unpack('>I', b'\x00' + data[off:off+3])[0]

def fmt_size(n):
    if n >= 1024*1024:
        return f"{n/1024/1024:.2f} MB"
    if n >= 1024:
        return f"{n/1024:.1f} KB"
    return f"{n} B"

def parse_viv_c0fb(data):
    """C0 FB custom format (PS2 und Xbox, identisches Layout).

    HEADER (8 Byte):
      00  2B  magic       C0 FB
      02  2B  header_end  BE; Byte-Offset wo der Header-Bereich endet
                          (= Beginn des ersten Datenblocks vor Alignment)
      04  2B  num_files   BE; Anzahl der Eintraege
      06  2B  redundant   = header_end >> 8 (High-Byte von header_end)

    EINTRAG 0 (kein expliziter Offset):
      00  1B  alignment   Ausrichtungseinheit fuer Datendaten-Start:
                            0x40 -> align 64
                            0x80 -> align 128
                            0x00 -> align 256
                            sonst -> Fallback 64
      01  3B  size        BE
      04  ..  name        null-terminiert

    EINTRAEGE 1..n:
      00  3B  offset      BE; absoluter Byte-Offset in der Archivdatei
      03  3B  size        BE
      06  ..  name        null-terminiert

    Nach dem letzten Eintrag: Nullbyte-Padding bis header_end.
    Ab header_end: Dateidaten, alignment-ausgerichtet.

    Beobachtungen:
      - BLOG-Archive: num_files=3 oder 4, unk1=0x0000
      - LEVEL-Archive: num_files=268..528
      - COMP-Archive:  num_files=8..28
      - PS2 und Xbox strukturell identisch; nur Dateigroessen unterscheiden sich
    """
    header_end = struct.unpack('>H', data[2:4])[0]
    num_files  = struct.unpack('>H', data[4:6])[0]
    meta = {
        'format':     'MOH C0FB',
        'magic':      data[0:4].hex().upper(),
        'header_end': f"0x{header_end:04X}",
        'num_files':  num_files,
        'file_size':  len(data),
    }

    import math

    raw = []
    pos = 8
    for i in range(num_files):
        if pos + 7 > len(data):
            raise ValueError(f"Header-EOF bei Eintrag {i} (pos={pos})")
        if i == 0:
            # Erster Eintrag: [1B alignment][3B size][name\0]
            alignment = data[pos]
            size_raw  = read_u24be(data, pos + 1)
            ns        = pos + 4
        else:
            # Alle weiteren Eintraege: [3B offset][3B size][name\0]
            alignment = None
            size_raw  = read_u24be(data, pos + 3)
            ns        = pos + 6
        try:
            name_end = data.index(0x00, ns)
        except ValueError:
            raise ValueError(f"Kein Null-Terminator bei Eintrag {i}")
        name = data[ns:name_end].decode('ascii', errors='replace')
        offset = read_u24be(data, pos)
        raw.append({'name': name, 'offset': offset, 'size_raw': size_raw,
                    'alignment': alignment if i == 0 else None})
        pos = name_end + 1

    # Echten Offset fuer Eintrag 0 berechnen (nur wenn alignment-Byte gesetzt war).
    # data[2:4] ist die vorberechnete Header-Endposition (vom Spielpacker eingetragen).
    # Das alignment-Byte gibt die Ausrichtungseinheit vor:
    #   0x40 → align 64  (PS2-Dateien mit kleinem abyte)
    #   0x80 → align 128 (Xbox-Dateien und groessere PS2-Dateien)
    #   sonst → Fallback auf 0x40 (z.B. abyte=0xC0)
    if raw and raw[0]['alignment'] is not None:
        abyte       = raw[0]['alignment']
        align       = {0x00: 0x100, 0x40: 0x40, 0x80: 0x80}.get(abyte, 0x40)
        base = header_end if header_end > pos - align else pos
        raw[0]['offset'] = math.ceil(base / align) * align

    # Zweiter Pass: Status setzen
    entries = []
    for i, e in enumerate(raw):
        size   = e['size_raw']
        extern = size == 0
        valid  = not extern and size > 0 and (e['offset'] + size) <= len(data)
        entries.append({'name': e['name'], 'offset': e['offset'],
                        'size': size, 'valid': valid, 'extern': extern})

    return meta, entries

def parse_bigf(data):
    """BIGF/BIG4 format: [4B magic][4B archive_size LE/BE][4B num_files BE][4B header_size BE]
       Eintraege: [4B offset BE][4B size BE][name\0]
    """
    magic = data[0:4]
    is_big4 = (magic == b'BIG4')
    # archive_size: BIGF=BE, BIG4=LE
    if is_big4:
        arch_size = struct.unpack('<I', data[4:8])[0]
    else:
        arch_size = struct.unpack('>I', data[4:8])[0]
    num_files   = struct.unpack('>I', data[8:12])[0]
    header_size = struct.unpack('>I', data[12:16])[0]
    meta = {
        'format':      'BIG4' if is_big4 else 'BIGF',
        'magic':       magic.decode('ascii'),
        'num_files':   num_files,
        'archive_size': fmt_size(arch_size),
        'header_size': f"0x{header_size:X}",
        'file_size':   len(data),
    }
    entries = []
    pos = 16
    for i in range(num_files):
        if pos + 9 > len(data):
            raise ValueError(f"Header-EOF bei Eintrag {i}")
        offset = struct.unpack('>I', data[pos:pos+4])[0]
        size   = struct.unpack('>I', data[pos+4:pos+8])[0]
        pos += 8
        try:
            name_end = data.index(0x00, pos)
        except ValueError:
            raise ValueError(f"Kein Null-Terminator bei Eintrag {i}")
        name = data[pos:name_end].decode('ascii', errors='replace')
        entries.append({
            'name':    name,
            'offset':  offset,
            'size':    size,
            'valid':   (offset + size) <= len(data),
            'extern':  False,
        })
        pos = name_end + 1
    return meta, entries

def detect_and_parse(data):
    magic4 = data[0:4]
    if magic4[:2] == b'\xC0\xFB':
        return parse_viv_c0fb(data)
    elif magic4 in (b'BIGF', b'BIG4'):
        return parse_bigf(data)
    elif magic4[:3] == b'BIG':
        return parse_bigf(data)
    else:
        raise ValueError(f"Unbekanntes Format: Magic = {magic4.hex().upper()}")


# ─── GUI ────────────────────────────────────────────────────────────────────

class VivExtractorApp(tk.Tk):

    EXT_COLORS = {
        # Audio
        'abk': '#8B5CF6', 'ast': '#8B5CF6', 'asf': '#8B5CF6',
        'som': '#8B5CF6', 'aem': '#8B5CF6', 'mpf': '#8B5CF6',
        # Grafik/Textur
        'ssh': '#EC4899', 'dtx': '#EC4899', 'tpk': '#EC4899',
        # Skript
        'cbs': '#3B82F6', 'sin': '#3B82F6',
        # Daten/komprimiert
        'cdb': '#10B981', 'cpt': '#10B981',
        # Geometrie
        'msh': '#F59E0B', 'dmf': '#F59E0B', 'skl': '#F59E0B', 'mpk': '#F59E0B',
        # Level
        'lfc': '#14B8A6',
        # Video
        'mvd': '#F97316',
    }

    def __init__(self):
        super().__init__()
        self.title("MOH VIV Extraktor")
        self.geometry("900x600")
        self.minsize(700, 450)
        self.configure(bg='#1a1a1a')

        self._viv_data = None
        self._viv_path = None
        self._all_entries = []
        self._meta = {}
        self._sort_col = 'name'
        self._sort_rev = False

        self._build_ui()
        self._apply_style()

        # Drag-and-Drop (Windows/Linux via TkDnD optional, fallback: nichts)
        try:
            self.drop_target_register('DND_Files')
            self.dnd_bind('<<Drop>>', self._on_drop)
        except Exception:
            pass

    def _apply_style(self):
        style = ttk.Style(self)
        style.theme_use('clam')
        bg, fg, sel = '#1a1a1a', '#e5e5e5', '#2a2a2a'
        hdr = '#252525'
        style.configure('Treeview',
            background=bg, foreground=fg, fieldbackground=bg,
            rowheight=22, font=('Consolas', 10))
        style.configure('Treeview.Heading',
            background=hdr, foreground='#888', font=('Consolas', 10, 'bold'),
            relief='flat')
        style.map('Treeview',
            background=[('selected', '#2d4a7a')],
            foreground=[('selected', '#ffffff')])
        style.configure('TScrollbar', background='#2a2a2a', troughcolor='#111',
            arrowcolor='#555', bordercolor='#111')
        style.configure('TButton', background='#2a2a2a', foreground=fg,
            relief='flat', padding=(10, 4), font=('Segoe UI', 9))
        style.map('TButton',
            background=[('active', '#3a3a3a'), ('pressed', '#1a1a1a')])
        style.configure('Accent.TButton', background='#1d4ed8', foreground='white',
            font=('Segoe UI', 9, 'bold'), padding=(12, 5))
        style.map('Accent.TButton',
            background=[('active', '#2563eb'), ('pressed', '#1e40af')])
        style.configure('TEntry', fieldbackground='#252525', foreground=fg,
            insertcolor=fg, relief='flat')
        style.configure('TLabel', background='#1a1a1a', foreground=fg)
        style.configure('TFrame', background='#1a1a1a')
        style.configure('TSeparator', background='#333')

    def _build_ui(self):
        # ── Toolbar ──
        toolbar = tk.Frame(self, bg='#111', pady=6, padx=10)
        toolbar.pack(fill='x', side='top')

        btn_open = ttk.Button(toolbar, text="📂  Öffnen", command=self._open_file)
        btn_open.pack(side='left', padx=(0, 6))

        self._btn_extract_sel = ttk.Button(toolbar, text="⬇  Auswahl exportieren",
            command=lambda: self._extract(selected_only=True),
            style='Accent.TButton', state='disabled')
        self._btn_extract_sel.pack(side='left', padx=(0, 4))

        self._btn_extract_all = ttk.Button(toolbar, text="⬇  Alle exportieren",
            command=lambda: self._extract(selected_only=False),
            state='disabled')
        self._btn_extract_all.pack(side='left', padx=(0, 10))

        # Filter
        tk.Label(toolbar, text="Filter:", bg='#111', fg='#888',
            font=('Segoe UI', 9)).pack(side='left', padx=(6, 4))
        self._filter_var = tk.StringVar()
        self._filter_var.trace_add('write', lambda *_: self._refresh_list())
        filter_entry = ttk.Entry(toolbar, textvariable=self._filter_var, width=18)
        filter_entry.pack(side='left', padx=(0, 8))

        # Ext-Filter Dropdown
        tk.Label(toolbar, text="Typ:", bg='#111', fg='#888',
            font=('Segoe UI', 9)).pack(side='left', padx=(0, 4))
        self._ext_var = tk.StringVar(value='alle')
        self._ext_combo = ttk.Combobox(toolbar, textvariable=self._ext_var,
            values=['alle'], state='readonly', width=8)
        self._ext_combo.pack(side='left')
        self._ext_combo.bind('<<ComboboxSelected>>', lambda _: self._refresh_list())

        # ── Info-Leiste ──
        self._info_frame = tk.Frame(self, bg='#111', padx=10, pady=4)
        self._info_frame.pack(fill='x', side='top')
        self._info_label = tk.Label(self._info_frame, text="Keine Datei geladen",
            bg='#111', fg='#555', font=('Consolas', 9), anchor='w')
        self._info_label.pack(fill='x')

        # ── Hauptbereich: Treeview ──
        main = tk.Frame(self, bg='#1a1a1a')
        main.pack(fill='both', expand=True, padx=10, pady=(6, 0))

        cols = ('name', 'ext', 'size', 'offset', 'status')
        self._tree = ttk.Treeview(main, columns=cols, show='headings',
            selectmode='extended')

        self._tree.heading('name',   text='Dateiname',    command=lambda: self._sort('name'))
        self._tree.heading('ext',    text='Typ',          command=lambda: self._sort('ext'))
        self._tree.heading('size',   text='Größe',        command=lambda: self._sort('size_raw'))
        self._tree.heading('offset', text='Offset',       command=lambda: self._sort('offset_raw'))
        self._tree.heading('status', text='Status',       command=lambda: self._sort('valid'))

        self._tree.column('name',   width=380, anchor='w', stretch=True)
        self._tree.column('ext',    width=60,  anchor='center', stretch=False)
        self._tree.column('size',   width=90,  anchor='e', stretch=False)
        self._tree.column('offset', width=100, anchor='e', stretch=False)
        self._tree.column('status', width=60,  anchor='center', stretch=False)

        vsb = ttk.Scrollbar(main, orient='vertical', command=self._tree.yview)
        self._tree.configure(yscrollcommand=vsb.set)
        self._tree.pack(side='left', fill='both', expand=True)
        vsb.pack(side='right', fill='y')

        self._tree.bind('<<TreeviewSelect>>', self._on_select)

        # Ext-Tag-Farben
        for ext, color in self.EXT_COLORS.items():
            self._tree.tag_configure(f'ext_{ext}', foreground=color)
        self._tree.tag_configure('invalid', foreground='#ef4444')
        self._tree.tag_configure('extern',  foreground='#f59e0b')  # gelb = extern

        # ── Statusleiste ──
        statusbar = tk.Frame(self, bg='#111', padx=10, pady=4)
        statusbar.pack(fill='x', side='bottom')
        self._status_label = tk.Label(statusbar, text="",
            bg='#111', fg='#555', font=('Consolas', 9), anchor='w')
        self._status_label.pack(fill='x')

    # ── Datei laden ──────────────────────────────────────────────────────────

    def _open_file(self):
        path = filedialog.askopenfilename(
            title="VIV-Datei öffnen",
            filetypes=[("VIV Archive", "*.viv *.VIV"), ("Alle Dateien", "*.*")])
        if path:
            self._load(path)

    def _on_drop(self, event):
        path = event.data.strip('{}')
        self._load(path)

    def _load(self, path):
        try:
            with open(path, 'rb') as f:
                data = f.read()
            meta, entries = detect_and_parse(data)
        except Exception as e:
            messagebox.showerror("Fehler", str(e))
            return

        self._viv_data  = data
        self._viv_path  = path
        # _idx als stabiler eindeutiger Schluessel fuer Treeview-iid
        for i, e in enumerate(entries):
            e['_idx'] = i
        self._all_entries = entries
        self._meta       = meta

        # Ext-Liste für Dropdown
        exts = sorted(set(
            os.path.splitext(e['name'])[1].lstrip('.').lower()
            for e in entries if '.' in e['name']
        ))
        self._ext_combo['values'] = ['alle'] + exts
        self._ext_var.set('alle')
        self._filter_var.set('')

        # Info-Leiste
        n_invalid = sum(1 for e in entries if not e['valid'] and not e.get('extern'))
        n_extern  = sum(1 for e in entries if e.get('extern'))
        fmt = meta.get('format', '?')
        info = (f"{os.path.basename(path)}  │  {fmt}  │  "
                f"{len(entries)} Dateien  │  "
                f"{fmt_size(len(data))}"
                + (f"  │  ⚠ {n_invalid} ungültige Einträge" if n_invalid else "")
                + (f"  │  ⇗ {n_extern} extern" if n_extern else ""))
        self._info_label.config(text=info, fg='#aaa')

        self._btn_extract_all.config(state='normal')
        self._refresh_list()

    # ── Treeview befüllen ────────────────────────────────────────────────────

    def _refresh_list(self):
        q   = self._filter_var.get().lower()
        ext = self._ext_var.get()

        filtered = []
        for e in self._all_entries:
            name = e['name']
            e_ext = os.path.splitext(name)[1].lstrip('.').lower()
            if q and q not in name.lower():
                continue
            if ext != 'alle' and e_ext != ext:
                continue
            filtered.append(e)

        # Sortierung
        rev = self._sort_rev
        col = self._sort_col
        if col in ('size_raw', 'offset_raw'):
            key = 'size' if col == 'size_raw' else 'offset'
            filtered.sort(key=lambda e: e[key], reverse=rev)
        elif col == 'valid':
            filtered.sort(key=lambda e: e['valid'], reverse=not rev)
        elif col == 'ext':
            filtered.sort(key=lambda e: os.path.splitext(e['name'])[1].lower(), reverse=rev)
        else:
            filtered.sort(key=lambda e: e['name'].lower(), reverse=rev)

        # Treeview leeren und neu befüllen
        for item in self._tree.get_children():
            self._tree.delete(item)

        for idx, e in enumerate(filtered):
            ext_tag = os.path.splitext(e['name'])[1].lstrip('.').lower()
            if e.get('extern'):
                tag    = 'extern'
                status = '⇗'
            elif not e['valid']:
                tag    = 'invalid'
                status = '✗'
            else:
                tag    = f'ext_{ext_tag}' if ext_tag in self.EXT_COLORS else ''
                status = '✓'
            self._tree.insert('', 'end',
                iid=str(e['_idx']),
                values=(
                    e['name'],
                    ext_tag.upper() if ext_tag else '—',
                    fmt_size(e['size']),
                    f"0x{e['offset']:06X}",
                    status,
                ),
                tags=(tag,))

        self._update_status(len(filtered))

    def _sort(self, col):
        if self._sort_col == col:
            self._sort_rev = not self._sort_rev
        else:
            self._sort_col = col
            self._sort_rev = False
        self._refresh_list()

    def _on_select(self, _event=None):
        sel = self._tree.selection()
        self._btn_extract_sel.config(state='normal' if sel else 'disabled')
        self._update_status()

    def _update_status(self, visible=None):
        if visible is None:
            visible = len(self._tree.get_children())
        sel = self._tree.selection()  # iids = str(_idx)
        idx_map = {str(e['_idx']): e for e in self._all_entries}
        sel_size = sum(idx_map[iid]['size'] for iid in sel if iid in idx_map)
        msg = f"{visible} Einträge sichtbar"
        if sel:
            msg += f"  │  {len(sel)} ausgewählt ({fmt_size(sel_size)})"
        self._status_label.config(text=msg)

    # ── Extrahieren ──────────────────────────────────────────────────────────

    def _extract(self, selected_only=False):
        if not self._viv_data:
            return

        out_dir = filedialog.askdirectory(
            title="Ausgabeordner wählen",
            initialdir=os.path.dirname(self._viv_path))
        if not out_dir:
            return

        if selected_only:
            sel_iids = set(self._tree.selection())
            entries  = [e for e in self._all_entries if str(e['_idx']) in sel_iids]
        else:
            entries = self._all_entries

        ok = errors = skipped = 0
        log = []
        for e in entries:
            name   = e['name']
            offset = e['offset']
            size   = e['size']
            if e.get('extern'):
                log.append(f"EXTERN  {name}  (liegt in E:\\DATAUK\\PAUSE oder ähnlichem Pfad)")
                skipped += 1
                continue
            if not e['valid']:
                log.append(f"FEHLER  {name}  (offset+size ausserhalb der Datei)")
                errors += 1
                continue
            dest = os.path.join(out_dir, name)
            parent = os.path.dirname(dest)
            if parent:
                os.makedirs(parent, exist_ok=True)
            try:
                with open(dest, 'wb') as f:
                    f.write(self._viv_data[offset:offset+size])
                ok += 1
            except Exception as ex:
                log.append(f"FEHLER  {name}: {ex}")
                errors += 1

        summary = f"Fertig: {ok} exportiert"
        if skipped:  summary += f", {skipped} extern (nicht in Datei)"
        if errors:   summary += f", {errors} Fehler"
        summary += f"\n→ {out_dir}"
        if log:
            summary += "\n\nFehler-Details:\n" + "\n".join(log[:20])
            if len(log) > 20:
                summary += f"\n... ({len(log)-20} weitere)"

        messagebox.showinfo("Export abgeschlossen", summary)


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    app = VivExtractorApp()
    # Datei per Argument übergeben
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        app.after(100, lambda: app._load(sys.argv[1]))
    app.mainloop()
