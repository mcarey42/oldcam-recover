"""Build synthetic camera dumps for tests: a FAT16 media volume inside a logical image,
scattered over NAND blocks the way the camera's controller does it."""
import random
import struct

import numpy as np

from oldcam_recover import ecc, ftl

UNFINALISED = b"RIFF\x00\x00\x00\x00AVI LIST" + bytes(range(256)) * 8  # header never finalised


def _entry(name: str, attr: int, cluster: int, size: int, deleted=False) -> bytes:
    if name in (".", ".."):
        raw = name.ljust(11)
    else:
        base, _, ext = name.partition(".")
        raw = base.upper().ljust(8)[:8] + ext.upper().ljust(3)[:3]
    e = bytearray(32)
    e[0:11] = raw.encode()
    if deleted:
        e[0] = 0xE5
    e[11] = attr
    struct.pack_into("<HI", e, 26, cluster, size)
    return bytes(e)


def fat16_volume(files: dict, data_clusters: int = 4200) -> bytes:
    """FAT16, 512-byte clusters. `files` maps '/DCIM/100COACH/PICT0001.AVI' -> bytes.
    Adds a deleted entry and leaves an unfinalised AVI in free space."""
    bps, root_entries = 512, 256
    spf = -(-((data_clusters + 2) * 2) // bps)
    total = 1 + spf + root_entries * 32 // bps + data_clusters
    fat = [0xFFF8, 0xFFFF] + [0] * data_clusters
    data = bytearray(b"\xff" * (data_clusters * bps))  # erased flash reads 0xff
    nxt = [2]

    def alloc(payload: bytes) -> int:
        n = max(1, -(-len(payload) // bps))
        first = nxt[0]
        for i in range(n):
            fat[first + i] = first + i + 1 if i < n - 1 else 0xFFFF
        o = (first - 2) * bps
        data[o:o + len(payload)] = payload
        nxt[0] += n
        return first

    tree: dict = {}
    for path, content in files.items():
        node = tree
        *dirs, leaf = path.strip("/").split("/")
        for d in dirs:
            node = node.setdefault(d, {})
        node[leaf] = content

    def build(node: dict, self_cluster: int, parent_cluster: int) -> bytes:
        out = b""
        if self_cluster:
            out += _entry(".", 0x10, self_cluster, 0) + _entry("..", 0x10, parent_cluster, 0)
        for name, val in node.items():
            if isinstance(val, dict):
                c = alloc(b"\x00" * bps)  # reserve, filled below
                body = build(val, c, self_cluster)
                data[(c - 2) * bps:(c - 2) * bps + bps] = body.ljust(bps, b"\x00")
                out += _entry(name, 0x10, c, 0)
            else:
                out += _entry(name, 0x20, alloc(val), len(val))
        if self_cluster and node is not tree:
            out += _entry("PICT0099.AVI", 0x20, 0, 0, deleted=True)
        return out

    root = build(tree, 0, 0).ljust(root_entries * 32, b"\x00")
    # an unfinalised recording in free space, two clusters past the last file
    o = (nxt[0] + 2 - 2) * bps
    data[o:o + len(UNFINALISED)] = UNFINALISED

    boot = bytearray(bps)
    boot[0:3] = b"\xe9\x00\x00"
    boot[3:11] = b"ZoranLtd"
    struct.pack_into("<HBHBHHBH", boot, 11, bps, 1, 1, 1, root_entries, total, 0xF8, spf)
    boot[43:54] = b"NO_NAME    "
    boot[54:62] = b"FAT16   "
    boot[510:512] = b"\x55\xaa"
    fat_bytes = struct.pack(f"<{len(fat)}H", *fat).ljust(spf * bps, b"\x00")
    return bytes(boot) + fat_bytes + root + bytes(data)


def logical_image(volume: bytes, blocks: int, block_bytes: int, at_sector: int = 64) -> bytes:
    img = bytearray(b"\xff" * (blocks * block_bytes))
    if at_sector * 512 + len(volume) > len(img):
        raise ValueError("volume does not fit in the logical image")
    img[0:16] = b"BOOTAREA-NOT-FAT"
    img[at_sector * 512:at_sector * 512 + len(volume)] = volume
    return bytes(img)


def nand(logical: bytes, g: ftl.Geometry, zone_phys: int, zone_log: int, with_ecc=True, seed=1,
         flip=None) -> bytes:
    """Scatter logical blocks over physical blocks (random order within each zone), with
    SmartMedia spare records. flip=(logical_block, byte_offset, bit) corrupts one stored bit."""
    rng = random.Random(seed)
    nlog = len(logical) // g.block_bytes
    zones = -(-nlog // zone_log)
    raw = np.full((zones * zone_phys, g.pages_per_block, g.page), 0xFF, dtype=np.uint8)
    secs = g.data // 512
    for z in range(zones):
        phys = list(range(zone_phys))
        rng.shuffle(phys)
        for lba in range(zone_log):
            lb = z * zone_log + lba
            if lb >= nlog:
                break
            blk = np.frombuffer(logical, np.uint8, g.block_bytes, lb * g.block_bytes).reshape(g.pages_per_block, g.data)
            if (blk == 0xFF).all():
                continue  # never written: the controller leaves it unmapped
            b = z * zone_phys + phys[lba]
            raw[b, :, :g.data] = blk
            field = ftl.address_field(lba).to_bytes(2, "big")
            for k in range(secs):
                o = g.data + 16 * k
                raw[b, :, o + 6:o + 8] = np.frombuffer(field, np.uint8)
                raw[b, :, o + 11:o + 13] = np.frombuffer(field, np.uint8)
                if with_ecc:
                    sec = blk[:, k * 512:(k + 1) * 512]
                    raw[b, :, o + 13:o + 16] = ecc.compute(np.ascontiguousarray(sec[:, :256]))
                    raw[b, :, o + 8:o + 11] = ecc.compute(np.ascontiguousarray(sec[:, 256:]))
            if flip and flip[0] == lb:
                page, off = divmod(flip[1], g.data)
                raw[b, page, off] ^= 1 << flip[2]
    return raw.tobytes()
