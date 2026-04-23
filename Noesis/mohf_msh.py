# Medal of Honor: Frontline (Xbox/PS2) - MSH Mesh Loader
# Format reverse engineered by h3x3r & MrGravey on ReSHAX (Jan 2026)
# https://reshax.com/topic/18753-ps2-medal-of-honor-frontline/
#
# Usage: place in Noesis plugins folder, open .msh file in Noesis
# Notes: UV's are flipped on export - use "Flip UV's" in export options
#        Textures embedded in MSH as SHPX blocks (Xbox) or palette data (PS2)

from inc_noesis import *
import noesis
import rapi
import os

def registerNoesisTypes():
    handle = noesis.register("Medal of Honor Frontline - Mesh (Xbox/PS2)", ".msh")
    noesis.setHandlerTypeCheck(handle, noepyCheckType)
    noesis.setHandlerLoadModel(handle, noepyLoadModel)
    noesis.logPopup()
    return 1

def noepyCheckType(data):
    if len(data) < 32:
        return 0
    return 1

def noepyLoadModel(data, mdlList):
    bs = NoeBitStream(data)
    baseName = rapi.getExtensionlessName(rapi.getLocalFileName(rapi.getInputName()))
    ctx = rapi.rpgCreateContext()

    matList = []
    texList = []

    # --- Header (LE) ---
    # +0x00: Sign (uint32)
    # +0x04: ResourceSize (uint32)
    # +0x08: MaterialTableOffset (uint32)
    # +0x0C: MaterialIndex (uint32)
    # +0x10: Unknown_2_Offset (uint32)
    # +0x14: Unknown_2_Index (uint32)
    # +0x18: MeshTableOffset (uint32)
    # +0x1C: MeshIndex (uint32)
    # +0x20: Unknown_3_Offset (uint32)
    # +0x24: Unknown_3_Index (uint32)
    # +0x28: TransformOffset (uint32)
    # +0x2C: TransformIndex (uint32)

    bs.seek(0x08, NOESEEK_ABS)
    MatTableOffset  = bs.readUInt()
    MatIndex        = bs.readUInt()
    bs.read(8)
    MeshTableOffset = bs.readUInt()
    MeshIndex_total = bs.readUInt()

    # --- Material Table ---
    # Each material entry = 128 bytes
    # +0x00: Null[5] (20 bytes)
    # +0x14: Unknown_0..2 (12 bytes)
    # +0x20: RawDataOffset
    # +0x24: PaletteDataOffset
    # +0x28: Unknown_3..5 (12 bytes)
    # +0x34: PixelFormat (ubyte), Unknown_7..9 (3 bytes)
    # +0x38: Unknown_10..20 (44 bytes)
    # +0x64: TextureWidth
    # +0x68: TextureHeight (wait, h3x3r: +0x60=Width, +0x64=Height)
    # +0x6C: HeaderOffset (SHPX)
    # +0x7C: ExternalTextureId

    MAT_STRIDE = 128

    materials = []
    for i in range(MatIndex):
        base = MatTableOffset + i * MAT_STRIDE
        bs.seek(base + 0x20, NOESEEK_ABS)
        RawDataOffset   = bs.readUInt()
        PaletteOffset   = bs.readUInt()
        bs.seek(base + 0x34, NOESEEK_ABS)
        PixelFormat     = bs.readUByte()
        bs.seek(base + 0x60, NOESEEK_ABS)
        TextureWidth    = bs.readUInt()
        TextureHeight   = bs.readUInt()
        bs.seek(base + 0x6C, NOESEEK_ABS)
        HeaderOffset    = bs.readUInt()
        bs.seek(base + 0x7C, NOESEEK_ABS)
        ExternalTexId   = bs.readUInt()

        materials.append({
            'RawDataOffset':  RawDataOffset,
            'PaletteOffset':  PaletteOffset,
            'PixelFormat':    PixelFormat,
            'Width':          TextureWidth,
            'Height':         TextureHeight,
            'HeaderOffset':   HeaderOffset,
            'ExternalTexId':  ExternalTexId,
        })

        texName = "tex_{:04d}".format(i)

        if TextureWidth > 0 and TextureHeight > 0 and RawDataOffset > 0:
            try:
                # Palette lesen: 8 Bytes vor PaletteOffset liegt uint32 paletteEntries
                bs.seek(PaletteOffset - 8, NOESEEK_ABS)
                paletteEntries = bs.readUInt()
                paletteSize    = paletteEntries * 4

                bs.seek(PaletteOffset, NOESEEK_ABS)
                rawPal = bs.readBytes(paletteSize)

                bs.seek(RawDataOffset, NOESEEK_ABS)
                pixelData = bs.readBytes(TextureWidth * TextureHeight)
                pixelData = rapi.imageFromMortonOrder(pixelData, TextureWidth, TextureHeight, 1)
                texData   = rapi.imageDecodeRawPal(pixelData, rawPal, TextureWidth, TextureHeight, 8, "b8g8r8a8")
                texFmt    = noesis.NOESISTEX_RGBA32

                tex = NoeTexture(texName, TextureWidth, TextureHeight, texData, texFmt)
                texList.append(tex)
                mat = NoeMaterial(texName, texName)
                matList.append(mat)
            except Exception as e:
                noesis.log("Texture error mat {}: {}".format(i, str(e)))
                mat = NoeMaterial(texName, "")
                matList.append(mat)
        else:
            mat = NoeMaterial(texName, "")
            matList.append(mat)

    # --- Mesh Table ---
    # Each MeshTable entry = 32 bytes
    # +0x00: Unknown_0
    # +0x04: MaterialOffset
    # +0x08: MeshOffset
    # +0x0C: Unknown_1
    # +0x10: Null[4]

    MESHTABLE_STRIDE = 32

    for i in range(MeshIndex_total):
        base = MeshTableOffset + i * MESHTABLE_STRIDE
        bs.seek(base + 0x04, NOESEEK_ABS)
        MaterialOffset = bs.readUInt()
        MeshOffset     = bs.readUInt()
        cPos           = bs.tell()

        # Mesh-Block:
        # +0x00: Null[3] (12 bytes)
        # +0x0C: VertexBufferOffset
        # +0x10: IndexBufferOffset
        # +0x14: VertexCount (uint16)
        # +0x16: IndexCount (uint16)
        bs.seek(MeshOffset + 0x0C, NOESEEK_ABS)
        VertexBufferOffset = bs.readUInt()
        IndexBufferOffset  = bs.readUInt()
        VertexCount        = bs.readUShort()
        IndexCount         = bs.readUShort()

        meshName = "{}_{}".format(baseName, "{:04d}".format(i))

        # Vertex: [XYZ float3 12B][Unknown 8B][UV float2 8B] = 28 bytes
        bs.seek(VertexBufferOffset, NOESEEK_ABS)
        VertexBuffer = bs.readBytes(VertexCount * 28)

        rapi.rpgBindPositionBufferOfs(VertexBuffer, noesis.RPGEODATA_FLOAT, 28, 0)
        rapi.rpgBindUV1BufferOfs(VertexBuffer, noesis.RPGEODATA_FLOAT, 28, 20)
        rapi.rpgSetName(meshName)

        # Material zuweisen
        texName = "tex_{:04d}".format(i % max(MatIndex, 1))
        if matList:
            rapi.rpgSetMaterial(texName)

        bs.seek(IndexBufferOffset, NOESEEK_ABS)
        IndexBuffer = bs.readBytes(IndexCount * 2)
        rapi.rpgCommitTriangles(IndexBuffer, noesis.RPGEODATA_USHORT, IndexCount, noesis.RPGEO_TRIANGLE_STRIP)

        bs.seek(cPos, NOESEEK_ABS)

    mdl = rapi.rpgConstructModel()
    mdl.setModelMaterials(NoeModelMaterials(texList, matList))
    mdlList.append(mdl)
    return 1
