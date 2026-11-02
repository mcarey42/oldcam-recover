"""oldcam-recover: get photos and videos back from raw NAND dumps of Zoran-based
"one-time use" camcorders (Pure Digital "Saturn", sold as CVS / Ritz / Walgreens).

  oldcam-recover recover  DUMP.bin OUTDIR       rebuild, extract, carve, read the log
  oldcam-recover timeline STATFILE.TXT -o timeline.json [--clock-offset H] [--tz +HH:MM]
                                                [--follows-previous CLIP]
  oldcam-recover mp4      timeline.json --videos DIR --out DIR
"""
import argparse
import datetime as dt
import hashlib
import json
import pathlib
import re
import shutil
import subprocess
import sys

from . import carve, fat, ftl, media, statlog


_SAFE = re.compile(r"[^A-Za-z0-9 ._~()+,-]")


def _safe_part(name: str) -> str:
    """One path component from the dump, made safe to write: names come from a possibly
    damaged filesystem, so separators, control characters, '.' and '..' are replaced."""
    name = _SAFE.sub("_", name).strip(" ")
    return "_" if name in ("", ".", "..") else name


def _inside(base: pathlib.Path, *parts: str) -> pathlib.Path:
    """base joined with sanitised parts; refuses anything that resolves outside base."""
    p = base.joinpath(*(_safe_part(x) for x in parts))
    if not p.resolve().is_relative_to(base.resolve()):
        raise ValueError(f"refusing to write outside {base}: {p}")
    return p


def cmd_recover(a):
    out = pathlib.Path(a.outdir)
    if out.exists() and any(out.iterdir()):
        sys.exit(f"{out} is not empty; choose a new directory so nothing is overwritten")
    out.mkdir(parents=True, exist_ok=True)
    dump = pathlib.Path(a.dump)
    raw = dump.read_bytes()
    g = ftl.GEOMETRIES[a.geometry] if a.geometry != "auto" else ftl.detect(raw)
    print(f"dump: {len(raw)} bytes, layout '{g.name}' ({g.data}+{g.spare}-byte pages, e.g. {g.example})")
    image, report = ftl.rebuild(raw, g)
    report["dump_sha256"] = hashlib.sha256(raw).hexdigest()
    (out / "logical.img").write_bytes(image)
    b, e = report["blocks"], report["ecc_sector_halves"]
    print(f"blocks: {b['mapped']} mapped, {b['erased']} erased, {b['bad']} marked bad, "
          f"{b['unreadable_address']} unclear")
    if e["sectors_without_ecc"] and not (e["clean"] + e["corrected"]):
        print("ecc: this controller stores none, so integrity is judged by fsck and playback")
    else:
        print(f"ecc: {e['clean']} clean, {e['corrected']} corrected, {e['uncorrectable']} uncorrectable halves")

    vols = fat.find_volumes(image)
    report["volumes"] = []
    for i, v in enumerate(vols):
        entries = list(v.walk(include_deleted=True))
        media_vol = any(x.path.upper().startswith("/DCIM") for x in entries) or a.all_volumes
        report["volumes"].append({"index": i, "offset": v.offset, "label": v.label, "bytes": v.size,
                                  "fat": 12 if v.fat12 else 16, "extracted": media_vol,
                                  "deleted_entries": [x.path for x in entries if x.deleted]})
        print(f"volume {i}: '{v.label or v.oem}' FAT{12 if v.fat12 else 16}, {v.size // 1024} KiB"
              f"{'  <- media' if media_vol else ''}")
        if not media_vol:
            continue
        files_dir = out / "files"
        dest = _inside(files_dir, f"{i}-{v.label or 'volume'}") if a.all_volumes else files_dir
        for x in entries:
            if x.deleted or x.is_dir:
                continue
            p = _inside(dest, *[c for c in x.path.split("/") if c])
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(v.read(x))
            print(f"  {x.path}  {x.size} bytes")
        for c, data, finalised in carve.find_avis(v):
            cdir = out / "carved"
            cdir.mkdir(exist_ok=True)
            p = cdir / f"volume{i}-cluster{c}{'' if finalised else '-unfinalised'}.avi"
            p.write_bytes(data)
            print(f"  carved from free space: {p.name} ({len(data)} bytes)")
            if not finalised and shutil.which("ffmpeg"):
                try:
                    media.remux(str(p), str(p.with_name(p.stem + "-repaired.avi")))
                    print(f"  repaired copy: {p.stem}-repaired.avi")
                except (ValueError, subprocess.CalledProcessError):
                    print("  ffmpeg could not repair it; the carved bytes are kept as found")
    (out / "report.json").write_text(json.dumps(report, indent=1))

    stat = next((p for p in (out / "files").rglob("STATFILE.TXT")), None) if (out / "files").exists() else None
    if stat:
        log = statlog.parse(stat.read_text(errors="replace"))
        (out / "timeline.json").write_text(json.dumps(statlog.timeline(log), indent=1))
        print(f"camera log: {len(log.clips)} recordings, {len(log.deleted)} deleted in camera, "
              f"{sum(1 for _, lost in log.sessions if lost)} unclean power-offs (battery out or flat) "
              f"-> timeline.json (camera clock)")
        for d in log.deleted:
            print(f"  deleted in camera: {d.name} recorded {d.stop} ({d.frames} frames)")
    print(f"done: {out}")


def cmd_timeline(a):
    log = statlog.parse(pathlib.Path(a.statfile).read_text(errors="replace"))
    entries = statlog.timeline(log, a.clock_offset, a.tz, a.follows_previous)
    by_name = {x["file"].upper(): x for x in entries}
    for item in a.set:
        name, _, when = item.partition("=")
        if name.upper() not in by_name or not when:
            sys.exit(f"--set {item}: expected CLIP=YYYY-MM-DDTHH:MM:SS for a clip in the log")
        t = dt.datetime.fromisoformat(when)
        if t.tzinfo is None:
            t = t.replace(tzinfo=dt.datetime.fromisoformat(by_name[name.upper()]["start"]).tzinfo)
        by_name[name.upper()].update(start=t.isoformat(timespec="seconds"), rule="set by hand")
    pathlib.Path(a.output).write_text(json.dumps(entries, indent=1))
    for x in entries:
        print(x["start"], x["file"], "|", x["rule"])


def cmd_mp4(a):
    entries = json.loads(pathlib.Path(a.timeline).read_text())
    videos, out = pathlib.Path(a.videos), pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    for x in entries:
        name = str(x.get("file", ""))
        if not name or pathlib.PurePath(name).name != name or "\\" in name or name in (".", ".."):
            print(f"skip {name!r}: clip names must be plain file names")
            continue
        src = _inside(videos, name)
        if not src.exists():
            print(f"skip {x['file']}: not in {videos}")
            continue
        start = dt.datetime.fromisoformat(x["start"])
        dst = out / f"{start:%Y-%m-%d_%H%M%S}_{pathlib.Path(x['file']).stem}.mp4"
        try:
            media.to_mp4(str(src), str(dst), start)
        except (ValueError, subprocess.CalledProcessError) as err:
            print(f"skip {name}: {err}")
            continue
        print(f"{dst.name}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="oldcam-recover", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("recover", help="rebuild the image, extract files, carve, read the log")
    r.add_argument("dump"); r.add_argument("outdir")
    r.add_argument("--geometry", choices=["auto", *ftl.GEOMETRIES], default="auto")
    r.add_argument("--all-volumes", action="store_true", help="also extract the camera's resource volumes")
    r.set_defaults(fn=cmd_recover)
    t = sub.add_parser("timeline", help="clip start times from STATFILE.TXT")
    t.add_argument("statfile"); t.add_argument("-o", "--output", default="timeline.json")
    t.add_argument("--clock-offset", type=float, default=0.0, help="hours to add to the camera clock")
    t.add_argument("--tz", default="+00:00", help="UTC offset of local time; write negative ones as --tz=-04:00")
    t.add_argument("--follows-previous", action="append", default=[], metavar="CLIP",
                   help="the clock was reset before CLIP: place it (and what follows) right after the "
                        "previous clip; repeat for several resets")
    t.add_argument("--set", action="append", default=[], metavar="CLIP=TIME",
                   help="give one clip a time you know, e.g. PICT0025.AVI=2008-06-23T18:00:00")
    t.set_defaults(fn=cmd_timeline)
    m = sub.add_parser("mp4", help="convert clips to dated MP4s for photo libraries")
    m.add_argument("timeline"); m.add_argument("--videos", required=True); m.add_argument("--out", required=True)
    m.set_defaults(fn=cmd_mp4)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
