"""Rebuild the logical image from a raw NAND dump that uses a SmartMedia-style FTL.

The camera's controller never writes the filesystem to NAND in order. It writes each
logical block to whichever physical block is free and records, in that block's spare
area, which logical block it holds. Reading the chip gives you the physical order;
the filesystem only makes sense in logical order.

Each page is `data` bytes followed by `spare` bytes. The spare holds one 16-byte
SmartMedia record per 512-byte sector:

    byte 5        block status (0xff = good)
    bytes 6-7     logical block address, big-endian: 0x1000 | lba << 1 | parity
    bytes 8-10    ECC of sector bytes 256-511
    bytes 11-12   logical block address again
    bytes 13-15   ECC of sector bytes 0-255

`parity` makes the 16-bit address field even parity. Physical blocks are grouped in
zones of 1024; each zone maps 1000 logical blocks, keeping the rest as spares.
"""
from dataclasses import dataclass

import numpy as np

from . import ecc


@dataclass(frozen=True)
class Geometry:
    name: str
    data: int  # data bytes per page
    spare: int  # spare bytes per page
    pages_per_block: int
    example: str

    @property
    def page(self) -> int:
        return self.data + self.spare

    @property
    def block_bytes(self) -> int:
        return self.data * self.pages_per_block


GEOMETRIES = {
    "small": Geometry("small", 512, 16, 32, "Hynix HY27UA081G1M (128 MiB)"),
    "large": Geometry("large", 2048, 64, 64, "Samsung K9F1G08U0A (128 MiB)"),
}
ZONE_PHYS, ZONE_LOG = 1024, 1000


def lba_of(field: int):
    """Decode a 16-bit address field; None unless it is a valid even-parity address."""
    if (field & 0xF800) != 0x1000 or bin(field).count("1") & 1:
        return None
    return (field & 0x7FE) >> 1


def address_field(lba: int) -> int:
    """Encode a logical block address the way the controller writes it."""
    f = 0x1000 | (lba << 1)
    return f | (bin(f).count("1") & 1)


def detect(raw: bytes):
    """Pick the geometry under which the most written blocks carry valid addresses."""
    best, best_score = None, 0.0
    for g in GEOMETRIES.values():
        block = g.page * g.pages_per_block
        if len(raw) % block:
            continue
        a = np.frombuffer(raw, dtype=np.uint8).reshape(-1, g.pages_per_block, g.page)
        first = a[:, 0, :]
        written = ~(first == 0xFF).all(axis=1)
        if not written.any():
            continue
        field = first[written, g.data + 6].astype(np.uint16) << 8 | first[written, g.data + 7]
        score = np.mean([lba_of(int(f)) is not None for f in field])
        if score > best_score:
            best, best_score = g, score
    if best is None or best_score < 0.5:
        raise ValueError("could not recognise the dump: size or spare layout matches no known geometry")
    return best


def rebuild(raw: bytes, geometry: Geometry, zone_phys: int = ZONE_PHYS, zone_log: int = ZONE_LOG):
    """Return (logical image as bytes, report dict). Unmapped logical blocks read 0xFF.
    Single-bit errors are corrected where ECC is stored; nothing else is guessed."""
    g = geometry
    secs = g.data // 512
    a = np.frombuffer(raw, dtype=np.uint8).reshape(-1, g.pages_per_block, g.page)
    nphys = a.shape[0]
    if nphys % zone_phys:
        raise ValueError(f"{nphys} physical blocks is not a whole number of {zone_phys}-block zones")
    nzones = nphys // zone_phys
    data = a[:, :, :g.data].copy()
    spare = a[:, :, g.data:].reshape(nphys, g.pages_per_block, secs, 16)

    # ECC, per 512-byte sector half. Blocks marked bad hold no data and are skipped.
    marked_bad = (spare[:, 0, 0, 5] != 0xFF) & ~(a == 0xFF).all(axis=(1, 2))
    sec = data.reshape(-1, 512)
    sp = spare.reshape(-1, 16)
    written = ~(sec == 0xFF).all(axis=1) & ~np.repeat(marked_bad, g.pages_per_block * secs)
    has_ecc = ~((sp[:, 8:11] == 0xFF).all(axis=1) & (sp[:, 13:16] == 0xFF).all(axis=1))
    counts = {"clean": 0, "corrected": 0, "ecc_byte_error": 0, "uncorrectable": 0,
              "sectors_without_ecc": int((written & ~has_ecc).sum())}
    names = ["clean", "corrected", "ecc_byte_error", "uncorrectable"]
    bad = set()
    w = np.flatnonzero(written & has_ecc)
    for lo, eo in ((0, 13), (256, 8)):
        status, byte, bit = ecc.check(sp[w, eo:eo + 3], ecc.compute(sec[w, lo:lo + 256]))
        for s, n in enumerate(names):
            counts[n] += int((status == s).sum())
        fix = np.flatnonzero(status == ecc.CORRECTED)
        sec[w[fix], lo + byte[fix]] ^= (1 << bit[fix]).astype(np.uint8)
        bad.update(int(x) for x in w[status == ecc.UNCORRECTABLE])

    # Map physical blocks to logical blocks.
    mapping, problems = {}, []
    blocks = {"mapped": 0, "erased": 0, "bad": 0, "unreadable_address": 0}
    for b in range(nphys):
        if (a[b] == 0xFF).all():
            blocks["erased"] += 1
            continue
        if spare[b, 0, 0, 5] != 0xFF:
            blocks["bad"] += 1
            problems.append({"physical_block": b, "issue": "marked bad"})
            continue
        fields = np.concatenate([spare[b, :, :, 6:8].reshape(-1, 2), spare[b, :, :, 11:13].reshape(-1, 2)])
        cands = {lba_of(int(v)) for v in np.unique(fields[:, 0].astype(np.uint16) << 8 | fields[:, 1])}
        cands = {c for c in cands if c is not None and c < zone_log}
        if len(cands) != 1:
            blocks["unreadable_address"] += 1
            problems.append({"physical_block": b, "issue": "address unclear", "candidates": sorted(cands)})
            continue
        logical = (b // zone_phys) * zone_log + cands.pop()
        if logical in mapping:
            problems.append({"physical_block": b, "issue": "duplicate logical block",
                             "logical_block": logical, "also_in": mapping[logical]})
            continue
        mapping[logical] = b
        blocks["mapped"] += 1

    out = np.full((nzones * zone_log, g.block_bytes), 0xFF, dtype=np.uint8)
    for logical, b in mapping.items():
        out[logical] = data[b].reshape(-1)

    owner = {b: l for l, b in mapping.items()}
    spb = g.pages_per_block * secs
    report = {
        "geometry": g.name, "chip_example": g.example, "zones": nzones,
        "blocks": blocks, "ecc_sector_halves": counts,
        "uncorrectable_sectors": [{"physical_block": s // spb, "logical_block": owner.get(s // spb),
                                   "sector_in_block": s % spb} for s in sorted(bad)],
        "problems": problems,
    }
    return out.tobytes(), report
