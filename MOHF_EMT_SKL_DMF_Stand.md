# MOHF Frontline (Xbox) — Format-Stand EMT / SKL / DMF
## Konsolidierter Bearbeitungsstand (Archiv)

Animationspipeline, vier Ebenen, alle nötig:
- **SKL** — geteilte benannte Bone-Hierarchie (zuerst geladen).
- **DMF** — geskinntes Mesh mit Bone-Weights (mit SKL geladen).
- **EMT** — level-spezifischer, gebackener Animations-Container (nach SKL+DMF).
- **MVD** — getrennt: Kamera-/Fahrzeug-/Objektpfade, Float3-Pos + Quaternion-Keyframes, kein Skelett.

Ladeorder (LFC): `CBS/SIN → MSH/DMF/SKL → BPD+PSP → … → AEM → Audio`.

---

## EMT — Entity Motion Table  (`mof_emt.bt`, v1.0.0)

ID Bytes `00 54 4D 45`, Magic `0x454D5400`, File Mask `*.emt`.

```
Header (32B):
  Magic=0x454D5400  Version=1  Pad  EntityCount  OffA  OffB  OffC  Pad2

Section A Prolog (0x20 … OffC):
  158 variable Records: type(u16) + size(u16) + payload
    type2 (32B): X/Y/Z (fixedpoint /65536) + float Y + sin/cos heading  = WorldPos
    type9 (36B): AI/LOD-Config
    type5/11 (12B): misc/aux

Section C (OffC):
  LevelLoopFrames(u32) + EntityOffsets(u32[EntityCount+1])
  EntityDataBase = OffC + 4 + (EntityCount+1)*4
  Entity[i] @ EntityDataBase + EntityOffsets[i]:
    RuntimePtr(u32) + int16[3][BoneCount] SNORM16 Bone-Quaternionen
    BoneCount = (EntityOffsets[i+1] - EntityOffsets[i] - 4) / 6
    EMT bone[N]  →  SKL BoneNames[N]   (1:1, anonymer Index → Name)

Section B (OffB):
  SkelCount(u32) + BRecordPtrs(u32[SkelCount])
  B-record @ BRecordPtrs[i]:  [+0] RuntimeID(u32)   [+4] LEKSPtr(u32 abs)
  LEKS-Block @ LEKSPtr:
    Tag='LEKS' (0x534B454C)  BoneDataPtr(u32)  NextLEKSPtr(u32)  BoneCount(u32)  Pad(u32)
  BoneData @ BoneDataPtr:  count(u16) + int16[3][count] SNORM16

B-Extra (BRecordPtrs-Ende … OffA):  N × 12B: EntityRef(u32)+TickRef(u32)+Config(u32)

Section A Table (OffA):  Count(u32) + Count × 20B Einträge
```

**Verifiziert:** Header, Section A/B/C-Layout, Entity-BoneCount-Formel, LEKS-Tag/Struktur,
BoneData-Layout. In Level 1_1: 1033 LEKS-Blöcke (= 1033 Animations-Frames),
~1198 LEKS-Records gesamt, 1446 animierte Entities.
**LEKS-Quants:** 55 Bones × 6 B = `int16[3]` SNORM16 (/32767) pro Bone.
**Unverifiziert:** `flags >> 1` als Anim-Namen-Index (Annahme).

---

## SKL — Skeleton  (`mof_skl.bt`)

```
Bind-Pose-Tabelle: stride = 32 Byte
  28 reale Einträge = Bones 0–27 (rechte Seite + Torso): echte T + Q
  Bones 28–54 (linke Seite + Beine): T = (0,0,0), Q = identity   <-- Problemquelle
Node-Struct TypeWord: bits[23:16] = Index in die Bind-Pose-Tabelle (bp_idx)
Sekundärer i16-Block @ 0x052C … 0x0666: stride 6, SNORM16 (/32767), für Bones 28–54
55 Bones gesamt; Namen: spine1, uparm_R, handR, … (FK-Hierarchie parent/child)
```

**Verifiziert:** stride 32, 28 reale Einträge, TypeWord-bp_idx-Mapping für Bones 0–27.
**Unresolved (Kernproblem):** der i16-Block @0x052C ist **nicht eindeutig** Translationen
oder Quaternionen. Die Bein-Bones haben in der SKL `bone_T = 0`, daher kollabiert die
FK-Kette die Beine auf die Hüftposition. Die vollständigen Bein-Translationen liegen an
einer noch nicht korrekt identifizierten Stelle.
→ **Bind-Pose-Translation der Beine: aus den zugänglichen Spieldateien als unlösbar abgelegt.**

PS1-Vorgänger zum Vergleich (`RenderObjectRecursivelyApplyHierachyData`, RenderObject.c):
`LocalTransformMatrix = translate(parent_matrix * bone_T) * rotate(Q)`,
Child1 erbt Local-, Child2 Parent-Matrix — auf Xbox-SKL angewandt scheitert es trotzdem,
weil `bone_T = 0` für Beine.

---

## DMF — Skinned Mesh  (`mof_dmf.bt`)

- Geskinntes Character-Mesh mit Bone-Weights; beschreibt, welche Vertices an welchem Bone hängen.
- Mehrere DMF-Varianten teilen ein SKL (z. B. `BM01.dmf … BM37.dmf` → `Soldier.skl`).
- **Verifiziert:** Vertex-, UV- und Strip-Decode.
- Vertex-Stride-Befund (verwandtes LithTech/Xbox-Schema): 28 B = Pos(12) + BoneIndex(4) + Normal(12).

---

## Skinning-Theorie (Stand)

- NV2A (Xbox) macht Quaternion-Skinning in Hardware: Verts sind **nicht** in T-Pose, sondern
  in **Bone-Local-Space** (nach inv_bind) gespeichert.
- Rendering braucht zwingend Quaternionen:
  `v_world = animate_Q · inv_bind_Q · v_bindpose`  bzw.  `v_world = bind_Q · v_local + bind_T`.
- Bein-Bones haben korrupte/Null-Bind-Pose-Q in der SKL → **LEKS-Q aus der EMT** als
  Rendering-Pose nutzen (vollständige Q für alle 55 Bones), keine T-Pose-Rekonstruktion.

---

## TPK — Texturen (Kontext)

28 Texturen, vollständig verifiziert. GR5 hat rosa Chroma-Key-Hintergrund (erwartet).
BT02 = korrekte Body-Textur für BM33.

---

## Stand der Viewer-Arbeit (BM33 + UHM1_1)

Erkennbarer Soldat-Render erreicht (Arme manuell +0,20 m nach außen) als Baseline.
Offene Render-Probleme: Schulter-Lücken, gebrochene rechte-Knie-Geometrie, Hände,
teils falsches UV-Mapping. Grundursache bleibt die SKL-Bind-Pose der Beine (s. o.).

## Offene Punkte (Zusammenfassung)
1. SKL: vollständige Bein-Translationen / Bedeutung des i16-Blocks @0x052C.
2. EMT: `flags>>1` als Anim-Namen-Index verifizieren.
3. Bind-Pose-Rekonstruktion der Beine aus Spieldateien — bislang unlösbar
   (deshalb der RenderDoc-Capture-Ansatz als Plan B für eine *posierte* Figur).
