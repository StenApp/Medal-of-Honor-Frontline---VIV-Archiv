# MOH Frontline PS2 – VIV Archivformat

Reverse-engineered von der UK-Disc **SLES_506.84**.  
Erstellt auf Basis von Binäranalyse, Hex-Dumps und Extraktor-Entwicklung.

---

## Übersicht

Auf der Disc existieren zwei verschiedene VIV-Formate mit der gleichen Dateiendung `.viv`:

| Pfad | Magic | Format |
|---|---|---|
| `DATA/*/*.viv` | `C0 FB xx xx` | MOH-eigenes PS2-Format (dieses Dokument) |
| `SHELL/*.viv` | `BIGF` | Standard-EA-Format (vivtool kompatibel) |

Drei Archiv-Typen mit C0FB-Magic:

| Dateiname | unk | Einträge | Inhalt |
|---|---|---|---|
| `*_BLOG.VIV` | `0x0000` | 3–4 | SSH-Texturen (Ladebildschirme/Missionsinfo) |
| `COMP.VIV` | `0x0001`–`0x0002` | 8–28 | CPT+CDB (komprimierte Texturen/Daten) |
| `LEVEL.VIV` | `0x0019`–`0x0037` | 268–528 | Alle Level-Assets |

Das `unk`-Feld hat **keinen Einfluss** auf das Parse-Format.

---

## Datei-Header

```
Offset  Größe  Typ        Beschreibung
------  -----  ---------  --------------------------------
0x00    4 B    magic      C0 FB xx xx  (variiert pro Datei)
0x04    2 B    uint16 BE  num_files
0x06    2 B    uint16 BE  unk  (interne ID, für Parser irrelevant)
```

Danach folgen direkt die Einträge (kein weiterer Padding-Header).

---

## Eintrags-Formate

### Eintrag 0 — Alignment-kodierter Offset

```
[1B alignment][3B size BE][name\0]
```

Das erste Byte ist **nicht der Offset selbst**, sondern die **Alignment-Einheit**.
Der echte Offset wird nach dem Parsen aller Einträge berechnet:

```
echter_offset = ceil(header_end / alignment) * alignment
```

Dabei ist `header_end` die Byte-Position direkt nach dem letzten `\0` des
letzten Eintrags. Der echte Offset ist also der erste auf `alignment` ausgerichtete
Byte-Wert nach dem Ende des Headers.

Beobachtete Alignment-Werte:

| Wert | Dezimal | Typisch bei |
|---|---|---|
| `0x40` | 64 | BLOG, kleines COMP |
| `0x80` | 128 | LEVEL, mittleres COMP |
| `0xC0` | 192 | großes COMP |

Beispiele:

```
BLOG 1_1  – header_end=63,  align=0x40 → off=0x000040=64
COMP 1_1  – header_end=300, align=0x40 → off=0x000140=320
COMP 4_3  – header_end=426, align=0xC0 → off=0x000240=576
LEVEL 4_3 – header_end=???, align=0x80 → off=0x000140=320
```

Hex-Beispiel COMP 1_1, Eintrag 0:
```
40  06 30 64  31 5F 31 5F 43 5F 63 30 2E 63 64 62 00
^^  --------  -----------------------------------------
align=0x40    sz=396.1KB    name="1_1_C_c0.cdb"
→ echter off = ceil(300/64)*64 = 0x140
```

### Einträge 1+ — Normalformat

```
[3B offset BE][3B size BE][name\0]
```

Offsets sind absolute Byte-Positionen in der VIV-Datei.
Die Einträge im Header sind **nicht** nach Offset sortiert.
Padding zwischen Dateien: variabel, typisch 32–320 Bytes.

Hex-Beispiel COMP 1_1, Eintrag 1:
```
06 31 C0  13 21 CC  31 5F 31 5F 41 52 54 5F 63 30 2E 63 70 74 00
--------  --------  -------------------------------------------
off=0x0631C0        sz=1224KB    name="1_1_ART_c0.cpt"
```

---

## Eintrag-Status

| Status | Bedingung | Bedeutung |
|---|---|---|
| ✓ valid | `sz > 0` und `off + sz ≤ filesize` | Extrahierbar |
| ⇗ extern | `sz == 0` | Datei liegt außerhalb des Archivs auf der Disc |
| ✗ ungültig | `off + sz > filesize` | Nicht extrahierbar |

Extern-Beispiel:
```
C0 98 00  00 00 00  50 61 75 73 65 2E 73 73 68 00
--------  --------  --------------------------------
off=0xC09880        sz=0    name="Pause.ssh"
→ liegt in E:\DATAUK\PAUSE\
```

---

## Sub-Datei-Header (CDB/CPT)

Extrahierte Dateien beginnen mit einem eigenen 12-Byte-Header:

```
Offset  Größe  Typ        Beschreibung
------  -----  ---------  --------------------------------
0x00    4 B    uint32 LE  Typ  (0x07=CDB, 0x09=CPT)
0x04    4 B    uint32 LE  Dateigröße (= VIV sz-Wert)
0x08    4 B    uint32 LE  Feld 2 (Bedeutung unbekannt)
```

Kein Refpack-Magic beobachtet. SSH-Dateien beginnen mit `SHPS`-Magic.

---

## Dateinamen

- Null-terminierter ASCII-String direkt nach dem numerischen Feld
- Länge variabel, typisch 8–40 Zeichen
- Namen können mit beliebigen ASCII-Zeichen beginnen

---

## Dateitypen

| Extension | Beschreibung |
|---|---|
| `.cpt` | Compressed Texture Package |
| `.cdb` | Compressed Data Block |
| `.ssh` | Shell Texture (SHPS-Magic) |
| `.cbs` | Compiled BrainScript |
| `.sin` | Script Instance |
| `.lfc` | Level File Container |
| `.msh` | Mesh |
| `.dmf` | Direct Model File |
| `.skl` | Skeleton |
| `.mpk` | Model Package |
| `.mvd` | Movie/Video |
| `.abk` | Audio Bank |
| `.ast` | Audio Stream |
| `.som` | Sound Map |
| `.aem` | Audio Event Map |
| `.mpf` | Music Path File |
| `.emt` | Entity Map Table |
| `.cls` | Class |
| `.sfn` | Screen Font |
| `.tpk` | Texture Package |
| `.scr` | Screen Script |
| `.dat` | Daten (Waffen-Bullet-Tabellen etc.) |

---

## BIGF-Format (SHELL/*.viv)

```
[4B magic "BIGF"][4B archive_size LE][4B num_files BE][4B header_size BE]
Pro Eintrag: [4B offset BE][4B size BE][name\0]
```

---

## Parser-Pseudocode

```python
def parse_c0fb(data):
    num_files = u16be(data, 4)
    pos = 8

    for i in range(num_files):
        if i == 0:
            alignment = data[pos]          # 1B: Alignment-Einheit
            size      = u24be(data, pos+1) # 3B: Dateigröße
            name      = cstr(data, pos+4)  # name\0
        else:
            offset = u24be(data, pos)      # 3B: absoluter Offset
            size   = u24be(data, pos+3)    # 3B: Dateigröße
            name   = cstr(data, pos+6)     # name\0
        pos = after_name_null

    # Echter Offset für Eintrag 0:
    true_offset_0 = ceil(pos / alignment) * alignment
```
