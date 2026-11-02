"""ffmpeg helpers: repair unfinalised AVIs and make photo-library friendly MP4s."""
import datetime as dt
import os
import shutil
import subprocess


def ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise SystemExit("ffmpeg is needed for this step: install it from https://ffmpeg.org or your package manager")
    return path


def _file(path: str) -> str:
    """An explicit local-file URL, so ffmpeg never reads a name as a protocol or option."""
    return "file:" + os.path.abspath(path)


def _avi_input(src: str) -> list:
    """Input arguments for an AVI from a dump. The bytes are untrusted, so the demuxer is
    forced (a disguised playlist must not be auto-detected) and only local files may be
    opened."""
    with open(src, "rb") as f:
        head = f.read(12)
    if head[:4] != b"RIFF" or head[8:12] != b"AVI ":
        raise ValueError(f"{src} is not an AVI file")
    return ["-protocol_whitelist", "file", "-f", "avi", "-i", _file(src)]


def remux(src: str, dst: str) -> None:
    """Rewrite an AVI with a fresh index (no re-encoding)."""
    subprocess.run([ffmpeg(), "-nostdin", "-v", "error", "-fflags", "+genpts", *_avi_input(src), "-c", "copy",
                    _file(dst)], check=True)


def to_mp4(src: str, dst: str, start: dt.datetime) -> None:
    """H.264/AAC MP4 at the original resolution, with the recording time embedded where
    Apple Photos, Google Photos and most players look for it."""
    utc = start.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000000Z")
    local = start.strftime("%Y-%m-%dT%H:%M:%S%z")
    subprocess.run([ffmpeg(), "-nostdin", "-v", "error", "-y", *_avi_input(src), "-map", "0:v:0", "-map", "0:a:0?",
                    "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart+use_metadata_tags",
                    "-metadata", f"creation_time={utc}", "-metadata", f"com.apple.quicktime.creationdate={local}",
                    _file(dst)], check=True)
    os.utime(dst, (start.timestamp(), start.timestamp()))
