# oldcam-recover

Get your videos back from an old "one-time use" digital camcorder by reading its
flash chip directly.

Between about 2005 and 2008, drugstores sold disposable digital camcorders (Pure
Digital "Saturn", sold as CVS, Ritz and Walgreens one-time-use camcorders). You were
meant to return them to the store to get a DVD. Many never went back, and the
footage is still sitting on the camera's flash chip. The camera won't hand it over
without a store's unlock key, but the chip itself can be read, and this project
turns that raw chip dump back into playable videos.

## Why this exists

> In 2007, two of these cameras recorded our wedding reception. For years I couldn't
> get the footage off them. In 2024 I pulled the flash chip from one and got a dump,
> but the files inside looked like garbage. In 2026 we worked out why. The camera
> scatters the video across the chip in its own order and keeps a map in hidden
> bytes. Using that map, every clip came back, including our first dance and a
> recording that had been cut off when the battery died. If you have one of these
> cameras in a drawer with memories you thought were gone, I hope this helps you get
> them back too. — Mark

## What you need

- The camera, and a hot-air rework station (or someone who has one) to remove the
  flash chip. See **[docs/hardware.md](docs/hardware.md)**: it is written for a careful
  first-timer, and reading the chip never changes what is stored on it.
- A NAND programmer with a TSOP48 socket (for example XGecu T48/T56 or similar) that
  can read the **full page including the spare area**.
- Python 3.10 or newer. ffmpeg is optional, for repairing cut-off clips and making
  MP4s for Apple Photos or Google Photos.

## Quick start

```sh
pip install git+https://github.com/mcarey42/oldcam-recover
oldcam-recover recover my-camera-dump.bin recovered/
```

The output looks like this:

```
dump: 138412032 bytes, layout 'small' (512+16-byte pages, e.g. Hynix HY27UA081G1M (128 MiB))
blocks: 6561 mapped, 1629 erased, 2 marked bad, 0 unclear
ecc: 418304 clean, 0 corrected, 0 uncorrectable halves
volume 1: 'NO_NAME' FAT16, 125647 KiB  <- media
  /DCIM/100COACH/PICT0001.AVI  3711498 bytes
  ...
  carved from free space: volume1-cluster6338-unfinalised.avi (1645568 bytes)
  repaired copy: volume1-cluster6338-unfinalised-repaired.avi
camera log: 9 recordings, 1 deleted in camera, ... -> timeline.json (camera clock)
```

In `recovered/` you get:

| Path | What it is |
| --- | --- |
| `files/DCIM/100COACH/*.AVI` | your videos (MPEG-4, play in VLC or mpv) |
| `files/STATFILE.TXT` | the camera's own diary of every recording, delete and power cycle |
| `carved/` | recordings found in free space, e.g. ones cut off by a dead battery |
| `timeline.json` | when each clip was recorded, by the camera's clock |
| `report.json` | the full technical report: blocks, ECC, volumes, deleted entries |
| `logical.img` | the rebuilt disk image, if you want to explore it with other tools |

Your dump is only ever read. The tool refuses to write into a folder that isn't empty.

## Dates and photo libraries

The camera's clock is often wrong: never set, set to the wrong time zone, or reset
when the battery ran out. `oldcam-recover timeline` fixes the dates using what you
remember, and `oldcam-recover mp4` makes H.264/AAC copies with those dates embedded,
so they land on the right day in Apple Photos or Google Photos (neither imports the
original AVIs). See **[docs/photo-libraries.md](docs/photo-libraries.md)**.

## How it works

The short version: the camera's controller writes each block of the disk to
whichever physical block on the chip is free, and records in that block's hidden
"spare" bytes which disk block it really is (with a parity bit and error-correcting
codes). A chip dump is in physical order; the files only make sense in logical
order. Stripping the spare bytes, which is what most guides suggest, throws away
the map. **[docs/how-it-works.md](docs/how-it-works.md)** has the details.

## Known to work

| Camera | Flash chip | Layout | Notes |
| --- | --- | --- | --- |
| Pure Digital Saturn (CVS one-time-use camcorder), 320x240 video | Hynix HY27UA081G1M | 512+16-byte pages | ECC stored and checked |
| Pure Digital Saturn, later revision, 384x288 video | Samsung K9F1G08U0A | 2048+64-byte pages | no ECC stored |

Other cameras built on the Zoran COACH chip with a SmartMedia-style flash layout
should work. If yours doesn't, please open an issue with the chip's markings, the
dump's size, and the first lines of the tool's output. Please don't attach the dump
itself: it holds your private footage.

## License

MIT. See [LICENSE](LICENSE).
