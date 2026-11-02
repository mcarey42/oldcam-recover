"""Find AVI recordings left in free space.

When the camera loses power mid-recording, the clip's data is on flash but the file
never gets a directory entry with a size, and its AVI header is never finalised
(RIFF size 0, no index). This looks in free clusters for an AVI header and takes the
contiguous run of written clusters after it. Erased flash reads 0xFF: whole erased
sectors mark where the data ends (single 0xFF bytes can be real data).
"""
from .fat import Volume


def find_avis(vol: Volume):
    """Yield (first_cluster, bytes, finalised) for each AVI found in free space."""
    free = set(vol.free_clusters())
    for c in sorted(free):
        head = vol.cluster_bytes(c)
        if head[:4] != b"RIFF" or head[8:12] != b"AVI ":
            continue
        parts, n = [], c
        while n in free:
            chunk = vol.cluster_bytes(n)
            if not chunk.strip(b"\xff"):
                break
            parts.append(chunk)
            n += 1
        data = b"".join(parts)
        while len(data) >= 512 and not data[-512:].strip(b"\xff"):  # drop whole erased sectors only
            data = data[:-512]
        finalised = int.from_bytes(head[4:8], "little") != 0
        yield c, data, finalised
