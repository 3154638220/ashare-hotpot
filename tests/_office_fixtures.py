"""Offline fixtures for legacy Word ``.doc`` extraction tests.

Real exchange ``.doc`` attachments cannot be sanitised in place, so the tests
build a minimal but structurally valid Word 97-2003 compound file with a FIB,
a compressed (single-byte ANSI) piece and a UTF-16 (uncompressed) piece.  Per
MS-DOC a compressed piece stores one byte per character, so callers must pass
ASCII-only ``ansi_text``; Chinese content lives in the UTF-16 piece, matching
how real Chinese investor-relations documents are stored.  The CFB layout
here is the smallest subset accepted by ``olefile``: one FAT sector, one
directory sector and one or more stream sectors chained in the FAT.
"""

from __future__ import annotations

import struct


_SECTOR = 512
_FAT_END = 0xFFFFFFFE
_FAT_FAT = 0xFFFFFFFD
_NOSTREAM = 0xFFFFFFFF


def _directory_entry(name: str, entry_type: int, start: int, size: int) -> bytes:
    raw = name.encode("utf-16-le") + b"\x00\x00"
    if len(raw) > 64:
        raise ValueError("directory entry name too long")
    entry = bytearray(128)
    entry[0 : len(raw)] = raw
    struct.pack_into("<H", entry, 64, len(raw))
    entry[66] = entry_type
    struct.pack_into("<I", entry, 68, _NOSTREAM)  # left sibling
    struct.pack_into("<I", entry, 72, _NOSTREAM)  # right sibling
    struct.pack_into("<I", entry, 76, _NOSTREAM)  # child
    struct.pack_into("<I", entry, 116, start)
    struct.pack_into("<I", entry, 120, size)
    struct.pack_into("<I", entry, 124, 0)
    return bytes(entry)


def build_cfb(streams: dict[str, bytes]) -> bytes:
    """Build a minimal version-3 compound file containing ``streams``."""

    sectors: list[bytes | None] = [None, None]  # 0: FAT, 1: directory
    stream_sectors: dict[str, tuple[int, int]] = {}
    for name, data in streams.items():
        count = (len(data) + _SECTOR - 1) // _SECTOR
        start = len(sectors)
        for index in range(count):
            sectors.append(data[index * _SECTOR : (index + 1) * _SECTOR])
        stream_sectors[name] = (start, count)

    names = ["Root Entry", *streams]
    entries: list[bytes] = []
    for index, name in enumerate(names):
        if index == 0:
            root = bytearray(_directory_entry(name, 5, _NOSTREAM, 0))
            struct.pack_into("<I", root, 76, 1)  # child -> WordDocument entry
            entries.append(bytes(root))
        else:
            start, _count = stream_sectors[name]
            entries.append(_directory_entry(name, 2, start, len(streams[name])))
    # Link WordDocument and the table stream as right siblings of the root.
    second = bytearray(entries[1])
    struct.pack_into("<I", second, 72, 2)
    entries[1] = bytes(second)
    while len(entries) < 4:
        entries.append(_directory_entry("", 0, 0, 0))
    sectors[1] = b"".join(entries)

    fat = [_FAT_FAT if index == 0 else _FAT_END for index in range(128)]
    fat[1] = _FAT_END  # directory chain
    for _name, (start, count) in stream_sectors.items():
        for index in range(count):
            fat[start + index] = start + index + 1 if index + 1 < count else _FAT_END

    header = bytearray(_SECTOR)
    header[0:8] = bytes.fromhex("d0cf11e0a1b11ae1")
    struct.pack_into("<HH", header, 24, 0x003E, 0x0003)
    struct.pack_into("<H", header, 28, 0xFFFE)
    struct.pack_into("<H", header, 30, 9)
    struct.pack_into("<H", header, 32, 6)
    struct.pack_into("<I", header, 44, 1)  # number of FAT sectors
    struct.pack_into("<I", header, 48, 1)  # first directory sector
    struct.pack_into("<I", header, 56, 4096)  # mini stream cutoff
    struct.pack_into("<I", header, 60, _NOSTREAM)  # no mini FAT
    struct.pack_into("<I", header, 68, _NOSTREAM)  # no DIFAT sectors
    struct.pack_into("<I", header, 76, 0)  # DIFAT[0] -> FAT sector 0
    for index in range(1, 109):
        struct.pack_into("<I", header, 76 + 4 * index, _NOSTREAM)

    sectors[0] = struct.pack("<128I", *fat)
    out = bytes(header)
    for sector in sectors:
        assert sector is not None
        out += sector.ljust(_SECTOR, b"\x00")
    return out


def build_legacy_doc(utf16_text: str, ansi_text: str) -> bytes:
    """Compose a tiny Word 97-2003 ``.doc`` with one UTF-16 and one ANSI piece.

    The ANSI piece exercises the compressed path (fc bit 0x40000000) and is
    stored as single-byte characters (ASCII only); the UTF-16 piece exercises
    the uncompressed path.  Returns the full compound-file bytes.
    """

    utf16_bytes = utf16_text.encode("utf-16-le")
    ansi_bytes = ansi_text.encode("gbk")
    utf16_chars = len(utf16_text)
    ansi_chars = len(ansi_text)
    ccp_text = utf16_chars + ansi_chars
    text_offset = _SECTOR

    fib = bytearray(_SECTOR)
    struct.pack_into("<H", fib, 0, 0xA5EC)
    struct.pack_into("<H", fib, 2, 0x00D9)
    struct.pack_into("<I", fib, 0x4C, ccp_text)

    cps = [0, utf16_chars, ccp_text]
    pcds = struct.pack("<HIH", 0, text_offset, 0)
    pcds += struct.pack(
        "<HIH", 0, 0x40000000 | (text_offset + len(utf16_bytes)), 0
    )
    plcpcd = struct.pack(f"<{len(cps)}I", *cps) + pcds
    clx = (
        b"\x01" + b"\x00" * 8
        + b"\x02" + struct.pack("<I", len(plcpcd)) + plcpcd
    )
    struct.pack_into("<I", fib, 0x1A2, 0)  # fcClx
    struct.pack_into("<I", fib, 0x1A6, len(clx))  # lcbClx

    word = (bytes(fib) + utf16_bytes + ansi_bytes).ljust(4096, b"\x00")
    table = clx.ljust(4096, b"\x00")
    return build_cfb({"WordDocument": word, "0Table": table})
