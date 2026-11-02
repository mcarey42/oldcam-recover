"""SmartMedia (SSFDC) Hamming ECC over 256-byte halves, vectorised with numpy.

Each 256-byte half has 22 parity bits: 16 line parities (rp0..rp15) and 6 column
parities (cp0..cp5). They are stored inverted in 3 bytes; the two low bits of the
third byte are stored as 1. A single flipped data bit produces a syndrome in which
exactly one bit of every parity pair is set, and the odd bits of that syndrome spell
out the byte index and bit index of the error.
"""
import numpy as np

_bits = np.unpackbits(np.arange(256, dtype=np.uint8)[:, None], axis=1, bitorder="little")
_PARITY = (_bits.sum(axis=1) & 1).astype(np.uint8)
_COLUMN_SETS = [[0, 2, 4, 6], [1, 3, 5, 7], [0, 1, 4, 5], [2, 3, 6, 7], [0, 1, 2, 3], [4, 5, 6, 7]]
_COLUMN = np.zeros(256, dtype=np.uint8)
for _k, _s in enumerate(_COLUMN_SETS):
    _COLUMN |= ((_bits[:, _s].sum(axis=1) & 1) << _k).astype(np.uint8)

CLEAN, CORRECTED, ECC_BYTE_ERROR, UNCORRECTABLE = range(4)


def compute(halves: np.ndarray) -> np.ndarray:
    """(N, 256) uint8 data halves -> (N, 3) uint8 ECC bytes as SmartMedia stores them."""
    odd = _PARITY[halves].astype(np.uint16)
    lp = np.bitwise_xor.reduce(odd * np.arange(256, dtype=np.uint16), axis=1)
    total = np.bitwise_xor.reduce(odd, axis=1)
    rp = np.zeros(len(halves), dtype=np.uint16)
    for k in range(8):
        one = (lp >> k) & 1
        rp |= ((total ^ one) & 1) << (2 * k)
        rp |= one << (2 * k + 1)
    cp = np.bitwise_xor.reduce(_COLUMN[halves], axis=1)
    b0 = ~(rp & 0xFF).astype(np.uint8)
    b1 = ~(rp >> 8).astype(np.uint8)
    b2 = (~(cp << 2).astype(np.uint8)) | 0x03
    return np.stack([b0, b1, b2], axis=1)


def check(stored: np.ndarray, computed: np.ndarray):
    """Classify stored vs computed ECC. Returns (status, byte, bit) arrays; byte/bit
    locate the flipped data bit where status == CORRECTED."""
    s = (stored ^ computed).astype(np.uint32)
    syn = s[:, 0] | (s[:, 1] << 8) | ((s[:, 2] >> 2) << 16)
    pop = np.zeros(len(syn), dtype=np.uint32)
    for k in range(22):
        pop += (syn >> k) & 1
    pairs_ok = np.ones(len(syn), dtype=bool)
    for k in range(11):
        pairs_ok &= (((syn >> (2 * k)) ^ (syn >> (2 * k + 1))) & 1).astype(bool)
    status = np.full(len(syn), UNCORRECTABLE, dtype=np.uint8)
    status[syn == 0] = CLEAN
    status[(pop == 11) & pairs_ok] = CORRECTED
    status[pop == 1] = ECC_BYTE_ERROR
    byte = np.zeros(len(syn), dtype=np.uint16)
    bit = np.zeros(len(syn), dtype=np.uint8)
    for k in range(8):
        byte |= (((syn >> (2 * k + 1)) & 1) << k).astype(np.uint16)
    for k in range(3):
        bit |= (((syn >> (16 + 2 * k + 1)) & 1) << k).astype(np.uint8)
    return status, byte, bit
