"""Minimal read-only FAT12/FAT16 reader for logical camera images.

The cameras put several small FAT volumes in one image without a partition table
(boot area, resource volumes, the media volume), so volumes are found by scanning
for boot sectors. Nothing here writes.
"""
import struct
from dataclasses import dataclass


@dataclass
class Entry:
    path: str
    is_dir: bool
    size: int
    first_cluster: int
    deleted: bool = False


class Volume:
    def __init__(self, image: bytes, offset: int):
        b = image[offset:offset + 512]
        (self.bps, self.spc, self.reserved, self.nfats, self.root_entries,
         tot16, _media, self.spf) = struct.unpack_from("<HBHBHHBH", b, 11)
        self.total = tot16 or struct.unpack_from("<I", b, 32)[0]
        self.offset = offset
        self.label = b[43:54].decode("latin1").strip()
        self.oem = b[3:11].decode("latin1").strip()
        self.cluster = self.bps * self.spc
        self.fat_off = offset + self.reserved * self.bps
        self.root_off = self.fat_off + self.nfats * self.spf * self.bps
        self.data_off = self.root_off + self.root_entries * 32
        self.clusters = (self.total * self.bps - (self.data_off - offset)) // self.cluster
        self.fat12 = self.clusters < 4085
        self.img = image

    @property
    def size(self) -> int:
        return self.total * self.bps

    def next_cluster(self, c: int) -> int:
        if self.fat12:
            v = struct.unpack_from("<H", self.img, self.fat_off + c * 3 // 2)[0]
            v = v >> 4 if c & 1 else v & 0xFFF
            return v if v < 0xFF8 else -1
        v = struct.unpack_from("<H", self.img, self.fat_off + c * 2)[0]
        return v if v < 0xFFF8 else -1

    def is_free(self, c: int) -> bool:
        if self.fat12:
            v = struct.unpack_from("<H", self.img, self.fat_off + c * 3 // 2)[0]
            return (v >> 4 if c & 1 else v & 0xFFF) == 0
        return struct.unpack_from("<H", self.img, self.fat_off + c * 2)[0] == 0

    def cluster_bytes(self, c: int) -> bytes:
        o = self.data_off + (c - 2) * self.cluster
        return self.img[o:o + self.cluster]

    def chain(self, c: int):
        seen = set()
        while 2 <= c < self.clusters + 2 and c not in seen:
            seen.add(c)
            yield c
            c = self.next_cluster(c)

    def read(self, e: Entry) -> bytes:
        data = b"".join(self.cluster_bytes(c) for c in self.chain(e.first_cluster))
        return data if e.is_dir else data[:e.size]

    def _entries(self, raw: bytes, parent: str):
        lfn = {}
        for i in range(0, len(raw) - 31, 32):
            e = raw[i:i + 32]
            if e[0] == 0:
                break
            if e[11] == 0x0F:  # long-name fragment
                if e[0] != 0xE5:
                    lfn[e[0] & 0x1F] = (e[1:11] + e[14:26] + e[28:32]).decode("utf-16le", "ignore")
                continue
            deleted = e[0] == 0xE5
            if e[11] & 0x08:  # volume label
                lfn = {}
                continue
            base = e[0:8].decode("latin1").rstrip()
            ext = e[8:11].decode("latin1").rstrip()
            short = ("?" + base[1:] if deleted else base) + ("." + ext if ext else "")
            name = "".join(lfn[k] for k in sorted(lfn)).split("\x00")[0] if lfn and not deleted else short
            lfn = {}
            if short in (".", "..") or not short.strip("?"):
                continue
            yield Entry(f"{parent}/{name}", bool(e[11] & 0x10), struct.unpack_from("<I", e, 28)[0],
                        struct.unpack_from("<H", e, 26)[0], deleted)

    def walk(self, include_deleted: bool = False):
        """Yield every entry, depth first, starting at the root directory. A directory is
        entered at most once, so a damaged image with looping directories still ends."""
        stack, visited = [("", self.img[self.root_off:self.data_off])], set()
        while stack:
            parent, raw = stack.pop()
            for e in self._entries(raw, parent):
                if e.deleted and not include_deleted:
                    continue
                yield e
                if e.is_dir and not e.deleted and e.first_cluster >= 2 and e.first_cluster not in visited:
                    visited.add(e.first_cluster)
                    stack.append((e.path, self.read(e)))

    def free_clusters(self):
        return [c for c in range(2, self.clusters + 2) if self.is_free(c)]


def find_volumes(image: bytes):
    """Every plausible FAT12/16 boot sector on a 512-byte boundary."""
    vols = []
    for off in range(0, len(image) - 512, 512):
        b = image[off:off + 512]
        if b[510:512] != b"\x55\xaa" or b[0] not in (0xEB, 0xE9):
            continue
        bps, spc, res, nfats = struct.unpack_from("<HBHB", b, 11)
        if bps != 512 or spc not in (1, 2, 4, 8, 16, 32, 64, 128) or res == 0 or nfats not in (1, 2):
            continue
        try:
            v = Volume(image, off)
        except struct.error:
            continue
        if v.total and off + v.size <= len(image) + 512 and v.clusters > 0:
            vols.append(v)
    return vols
