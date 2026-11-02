# How it works

## Pages, spare bytes and the map

NAND flash is read in **pages**. Each page has a data area and a small **spare
area** that the camera's controller uses for bookkeeping:

| Chip | Page | Pages per block | Block |
| --- | --- | --- | --- |
| Hynix HY27UA081G1M ("small") | 512 data + 16 spare | 32 | 16 KiB |
| Samsung K9F1G08U0A ("large") | 2048 data + 64 spare | 64 | 128 KiB |

These cameras use the same bookkeeping as SmartMedia cards. Every 512 bytes of data
gets a 16-byte record in the spare area:

| Bytes | Meaning |
| --- | --- |
| 5 | block status: 0xFF means good; anything else marks a factory-bad block |
| 6-7 | which **logical** block this physical block holds |
| 8-10 | error-correcting code (ECC) for data bytes 256-511 |
| 11-12 | the logical block number again |
| 13-15 | ECC for data bytes 0-255 |

The block number is stored as `0x1000 | number << 1 | parity`, big-endian, where the
parity bit makes the whole 16-bit field have an even number of 1 bits. So `0x1001`
is block 0, `0x1002` is block 1 and `0x1007` is block 3.

The chip is split into **zones** of 1024 physical blocks. Each zone holds 1000
logical blocks and keeps 24 spares for wear levelling and bad blocks.

## Why the files looked like garbage

When the camera writes, it doesn't overwrite a block in place. It writes the new data
to a free physical block, records the logical block number in the spare bytes, and
erases the old one. After a few recordings the disk's blocks are scattered across the
chip in no particular order.

A chip dump is in **physical** order. A common first step is to strip the spare
bytes so the dump is the "right" size, but that throws away the only record of the
order. `oldcam-recover` reads every block's number from the spare area and puts the
blocks back in **logical** order. The result is an ordinary disk image.

## Error correction

On the small-page chip, each 256 bytes has a 3-byte Hamming code (the SmartMedia
scheme). The tool checks every sector. A single flipped bit is corrected and
reported; anything worse is reported as uncorrectable with its location, and nothing
is guessed. On the dumps tested so far, not a single bit needed correcting.

The large-page revision stores no ECC at all. There, the evidence that the data is
intact comes from the filesystem check and from decoding every frame of every video
without errors.

## The disk inside

The rebuilt image has no partition table. It holds a boot area and firmware, then
several FAT volumes, each starting with a boot sector that names "Zoran", the maker
of the camera's processor chip:

- `ResourcesA` / `ResourcesB` / `ResourcesC`: small FAT12 volumes with the camera's
  settings, sounds and on-screen graphics;
- the **media volume** (FAT16, 16 KiB clusters) holding `DCIM/100COACH/PICTnnnn.AVI`
  and `STATFILE.TXT`.

The tool includes its own small read-only FAT reader, so nothing needs mounting.

## The camera's diary: STATFILE.TXT

The camera logs its own events:

```
**REBOOT  POWER ON 2007, 9, 8,21,59,28
REC,PICT0001.AVI,2007, 9, 8,21,59,46,  135,30,   524516, 42
DEL,PICT0003.AVI,2062, 8,17,21,40,24
```

- `REC` is written when a recording **stops**. The fields are the stop time, the
  number of frames, frames per second and file size. The start time is the stop time
  minus frames / fps.
- `DEL` records a clip deleted on the camera. Its file number is then reused by the
  next recording.
- A `POWER ON` with no `POWER OFF` before it means the battery was pulled or died.
  The clock often comes back wrong after that, sometimes years off.

## Recordings cut off by a dead battery

If the power dies mid-recording, the video data is already on the flash, but the file
never gets a proper directory entry, and its AVI header still says "size 0". The tool
looks for AVI headers in the filesystem's free space, takes the run of written
clusters that follows, and stops at erased flash (whole sectors of 0xFF). With ffmpeg
installed it also writes a repaired copy with a rebuilt index, which most players need.

## What can't come back

- Clips deleted on the camera, once their space was reused or the controller erased
  it. The diary still tells you they existed, when they were recorded and how long
  they were.
- Anything in blocks marked bad at the factory, which normally hold nothing.
