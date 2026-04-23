# Medal of Honor: Frontline (Xbox) - XSH Texture Loader
# Format reverse engineered by h3x3r on ReSHAX (Jan 2026)
# https://reshax.com/topic/18753-ps2-medal-of-honor-frontline/
#
# Usage: place in Noesis plugins folder, open .xsh file in Noesis
#
# Supported pixel formats:
#   96  = DXT1 (no alpha)
#   97  = DXT3 (with alpha)
#   123 = Palettised 8bpp BGRA8888 (Morton order)
#   125 = BGRA8888 raw (optionally Morton order)

from inc_noesis import *
import noesis
import rapi
import os

def registerNoesisTypes():
    handle = noesis.register("Medal of Honor Frontline - Xbox Texture", ".xsh")
    noesis.setHandlerTypeCheck(handle, noepyCheckType)
    noesis.setHandlerLoadRGBA(handle, noepyLoadRGBA)
    noesis.logPopup()
    return 1

def noepyCheckType(data):
    if len(data) < 20:
        return 0
    return 1

def noepyLoadRGBA(data, texList):
    bs = NoeBitStream(data)
    baseName = rapi.getExtensionlessName(rapi.getLocalFileName(rapi.getInputName()))

    # XSH Header:
    # +0x00: Magic "SHPX" (4 bytes)
    # +0x04: BlockSize (uint32 LE) - includes header
    # +0x08: Unknown (uint32) - usually 0x00000001
    # +0x0C: Name (null-terminated ASCII, max ~16 bytes)
    # +0x70: PixelFormat (ubyte)
    # +0x71: Unknown (3 bytes)
    # +0x74: TextureWidth (uint16)
    # +0x76: TextureHeight (uint16)
    # +0x78: Unknown (4 bytes)
    # +0x7C: MortonFlag (uint32) - non-zero = Morton order

    bs.seek(0x70, NOESEEK_ABS)
    PixelFormat   = bs.readUByte()
    bs.read(3)
    TextureWidth  = bs.readUShort()
    TextureHeight = bs.readUShort()
    bs.read(4)
    MortonFlag    = bs.readUInt()

    noesis.log("XSH: fmt={} size={}x{} morton={}".format(
        PixelFormat, TextureWidth, TextureHeight, MortonFlag))

    if PixelFormat == 96:
        # DXT1 - no alpha
        RawDataBufferSize = TextureWidth * TextureHeight // 2
        RawDataBuffer = bs.read(RawDataBufferSize)
        imgData = rapi.imageDecodeDXT(RawDataBuffer, TextureWidth, TextureHeight, noesis.NOESISTEX_DXT1)
        texFmt  = noesis.NOESISTEX_RGBA32

    elif PixelFormat == 97:
        # DXT3 - with alpha
        RawDataBufferSize = TextureWidth * TextureHeight
        RawDataBuffer = bs.read(RawDataBufferSize)
        imgData = rapi.imageDecodeDXT(RawDataBuffer, TextureWidth, TextureHeight, noesis.NOESISTEX_DXT3)
        texFmt  = noesis.NOESISTEX_RGBA32

    elif PixelFormat == 123:
        # Palettised 8bpp BGRA8888 + Morton order
        RawDataBufferSize = TextureWidth * TextureHeight
        RawDataBuffer = bs.read(RawDataBufferSize)

        # Palette: find marker \x00\x00\x00\x00\x2A, then +12 = paletteEntries
        marker = data.find(b'\x00\x00\x00\x00\x2A', bs.tell())
        bs.seek(marker + 12, NOESEEK_ABS)
        PaletteBufferSize = bs.readUInt() * 4
        bs.read(4)
        PaletteBuffer = bs.read(PaletteBufferSize)

        imgData = rapi.imageDecodeRawPal(RawDataBuffer, PaletteBuffer, TextureWidth, TextureHeight, 8, "b8 g8 r8 a8")
        imgData = rapi.imageFromMortonOrder(imgData, TextureWidth, TextureHeight, 4)
        texFmt  = noesis.NOESISTEX_RGBA32

    elif PixelFormat == 125:
        # BGRA8888 raw
        RawDataBufferSize = TextureWidth * TextureHeight * 4
        RawDataBuffer = bs.read(RawDataBufferSize)
        imgData = rapi.imageDecodeRaw(RawDataBuffer, TextureWidth, TextureHeight, "b8 g8 r8 a8")
        if MortonFlag != 0:
            imgData = rapi.imageFromMortonOrder(imgData, TextureWidth, TextureHeight, 4)
        texFmt = noesis.NOESISTEX_RGBA32

    else:
        noesis.log("XSH: Unknown pixel format {}".format(PixelFormat))
        return 0

    texList.append(NoeTexture(baseName, TextureWidth, TextureHeight, imgData, texFmt))
    return 1
