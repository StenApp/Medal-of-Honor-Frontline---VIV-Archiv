# Medal of Honor: Frontline — File Format Research

Reverse engineering and tooling for **Medal of Honor: Frontline** (EA Games, 2002) on PS2 and Xbox.  
Covers archive formats, audio extraction, and format documentation.

---

## Tools in this repo

| Tool | Description |
|------|-------------|
| `moh_viv_gui.py` | VIV archive viewer and extractor (PS2 + Xbox) |
| `mohf_extractor_v2.py` | Batch audio extractor — converts all sounds and music to WAV |

**Requirements:** Python 3.6+, [SX.EXE v3.01.01](https://zenhax.com) (EA Sound eXchange), [vgmstream-cli](https://github.com/vgmstream/vgmstream)

---

## File Formats

### `.viv` — VIV Archive

EA archive format. MOHF uses two distinct variants, both supported by `moh_viv_gui.py`:

| File | Format | Notes |
|------|--------|-------|
| `comp.viv`, `BLOG*.viv`, `level.viv`, `SHELL.viv` | MOH custom (magic `C0 FB`) or Standard BIGF| Use `moh_viv_gui.py` |

The `C0 FB` format differs from BIGF in its header layout and entry structure. See `moh_viv_gui.py` for the full parser.  
`level.viv` contains the MPF music index files (needed for full music extraction).

---

### `.abk` / `.ABK` — AEMS ModuleBank (Audio)

Central audio container of EA's AEMS (Audio Engine / Module System).  
Identified by magic `AB` + version byte: `0x08` = PS2, `0x09` = Xbox.

Two sub-types determined by field `moduleoffset` at header offset `+0x1C`:

**BANK type** (`moduleoffset > 0`, points to embedded `BNKl`):  
Contains RAM-resident sounds. Extract the `BNKl` block and pass to SX.EXE.  
Used by: `GunBnk`, `ImpBnk`, `ScrBnk`, `MovBnk`, `VehBnk`, `Load1`–`Load9`

**STREAM type** (`moduleoffset == 0`):  
No embedded audio. References a companion `.ast` file with the same base name.  
Used by: `StrBnk`, `VocBnk`

---

### `.ast` / `.AST` — Audio Stream

Multi-stream audio file. Contains 6–16 sequential EA SCHl streams (3–8 stereo pairs).  
Each stereo pair = two mono streams (L + R).

Block structure (Little-Endian sizes, both PS2 and Xbox):
```
SCHl (header, ~40 bytes) → SCCl (codec state) → SCDl* (audio data) → SCEl (end)
[null padding] → SCHl (next stream) → ...
```

Streams are separated by null-byte alignment padding — a block-walker must skip these.  
Codec: EA-XA 4-bit ADPCM v1. Converted by SX.EXE or vgmstream.

---

### `.asf` / `.ASF` — Audio Stream File

Same internal structure as AST but contains a single long stream.  
Used for shell/menu music (e.g. `SHELL1.ASF` = 5:31 min, 48000 Hz stereo).  
Decoded directly by vgmstream.

---

### `.mus` / `.MUS` — Music Stream

Sequential SCHl stream file containing all music segments for a level.  
Always named `main.mus` / `MAIN.MUS` per level folder.  
Typically 5 segments (intro + loops + outro), 22050 Hz stereo, EA-XA.

Requires the matching `.mpf` file for full extraction (otherwise only segment 1 is accessible via SX.EXE).

---

### `.mpf` / `.MPF` — Music PlayFile

Adaptive music graph — indexes the segments within the companion `.mus` file.  
Magic: `PFDx` (LE: `78 44 46 50`).

| Platform | Version | vgmstream branch |
|----------|---------|-----------------|
| PS2 | 3.2 | SSX Tricky / Sled Storm |
| Xbox | 3.4 | Harry Potter COS / Shox |

Both versions are supported by vgmstream. PS2 and Xbox MPFs for the same level have identical segment offsets.

**MPF files are stored inside `level.viv`** — extract them with `moh_viv_gui.py` before use.  
Naming: `{Mission}_{Level}M.mpf` → e.g. `1_1M.mpf` belongs to `DATA\1\1_1\main.mus`

---

### `.mpc` — Video

FMV video files. Standard EA MPC format (MPEG video + EA audio).

---

### `.ssh` (PS2) / `.xsh` (Xbox) — Textures / Images

Texture/image archives. Use [EA Graphics Manager](https://www.psx-place.com/resources/ea-graphics-manager.740/) to open.

---

### `.aem` / `.AEM` — Audio Event Map

Binary file mapping game event IDs to sound bank slot indices.  
Does **not** contain plain-text sound names — names are only in EA's internal build data.

---

## Audio Extraction

### Quick start

1. Extract `level.viv` files with `moh_viv_gui.py` to get the `.mpf` files
2. Place all `.mpf` files into a flat folder (e.g. `C:\mpf\`)
3. Run `mohf_extractor_v2.py`
4. Set paths: SX.EXE, vgmstream-cli, DATA folder, MPF folder, output folder
5. Scan → select levels → Extract

### What gets extracted

| Source | Output |
|--------|--------|
| `GunBnk`, `ImpBnk`, `ScrBnk`, `MovBnk`, `VehBnk`, `LoadX` | Individual WAV per sound slot |
| `StrBnk.ast` | 6–16 WAVs (mono L/R pairs) |
| `SHELL1.asf` | 1 WAV (full shell music stream) |
| `main.mus` + MPF | 5 WAVs (individual music segments) |

Output structure: `{output}\{Mission}\{Level}\{BankName}\sound_NNN.wav`

### Notes

- Sound slot names are not recoverable from game data — WAVs are numbered by slot index
- VocBnk may be an empty dummy — the script detects and skips these automatically  
- AST stereo pairs (01+02, 03+04, ...) can be merged with ffmpeg if needed
- PS2 data is inside `BLOG*.viv` — extract first, then point the script at the extracted DATA folder

---

## Platform Differences

| | PS2 | Xbox |
|-|-----|------|
| ABK version byte | `0x08` | `0x09` |
| AST block sizes | Little-Endian | Little-Endian |
| MPF version | 3.2 | 3.4 |
---

## Related

- [vgmstream](https://github.com/vgmstream/vgmstream) — open-source game audio decoder
- [vivtool](https://github.com/Aleksei-Miller/vivtool) — EA BIGF archive tool
- [EA Graphics Manager](https://www.psx-place.com/resources/ea-graphics-manager.740/) — SSH/XSH texture viewer
