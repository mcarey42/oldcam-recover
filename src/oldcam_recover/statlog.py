"""Parse the camera's own event log (STATFILE.TXT) and build a clip timeline.

Lines look like:
    **REBOOT  POWER ON 2007, 9, 8,21,59,28
    REC,PICT0001.AVI,2007, 9, 8,21,59,46,  135,30,   524516, 42
    DEL,PICT0003.AVI,2062, 8,17,21,40,24

A REC line is written when recording STOPS; its fields after the time are frames,
frames per second, file size and an unknown counter. A DEL line records an in-camera
delete. A POWER ON with no POWER OFF before it means the camera lost power, and the
clock often comes back wrong after that.
"""
import datetime as dt
import re
from dataclasses import dataclass, field

_NUM = re.compile(r"-?\d+")


@dataclass
class Clip:
    name: str
    stop: dt.datetime  # camera clock
    frames: int
    fps: int
    size: int
    session: int

    @property
    def start(self) -> dt.datetime:
        return self.stop - dt.timedelta(seconds=self.frames / max(self.fps, 1))


@dataclass
class Log:
    clips: list = field(default_factory=list)  # final REC per file name, in log order
    deleted: list = field(default_factory=list)  # REC lines later deleted in camera
    sessions: list = field(default_factory=list)  # (power_on_time, after_power_loss)


def _time(nums):
    try:
        return dt.datetime(*nums[:6])
    except ValueError:
        return None


def parse(text: str) -> Log:
    log, recs, last_power = Log(), {}, "OFF"
    for line in text.splitlines():
        if "POWER ON" in line:
            log.sessions.append((_time([int(x) for x in _NUM.findall(line)]), last_power == "ON"))
            last_power = "ON"
        elif "POWER OFF" in line:
            last_power = "OFF"
        elif line.startswith("REC,"):
            f = [x.strip() for x in line.split(",")]
            nums = [int(x) for x in f[2:]]
            stop = _time(nums)
            if stop is None:
                continue
            recs[f[1]] = Clip(f[1], stop, nums[6], nums[7], nums[8], max(len(log.sessions) - 1, 0))
        elif line.startswith("DEL,"):
            name = line.split(",")[1].strip()
            if name in recs:
                log.deleted.append(recs.pop(name))
    log.clips = list(recs.values())
    return log


def timeline(log: Log, clock_offset_hours: float = 0.0, tz: str = "+00:00", follows_previous=()):
    """Start time for each clip.

    clock_offset_hours converts the camera clock to local time (e.g. -4 for a clock
    running on UTC, shown in EDT). follows_previous names clips where the clock jumped
    (typically after a power loss): from that clip on, times are moved so its session's
    power-on comes straight after the previous clip ended. Only a person who remembers
    the day can say which jumps were resets, so nothing is moved unless named."""
    sign = -1 if tz.startswith("-") else 1
    hh, mm = (int(x) for x in tz.lstrip("+-").split(":"))
    zone = dt.timezone(sign * dt.timedelta(hours=hh, minutes=mm))
    offset = dt.timedelta(hours=clock_offset_hours)
    follows_previous = {n.upper() for n in follows_previous}
    shift = dt.timedelta(0)  # accumulated correction for clock resets
    prev_stop, entries = None, []  # prev_stop is already corrected
    for c in log.clips:
        on = log.sessions[c.session][0] if c.session < len(log.sessions) else None
        if c.name.upper() in follows_previous and prev_stop:
            shift = prev_stop - (on if on and on <= c.start else c.start)
        rule = "camera clock" if not shift else "moved to follow the clip before a clock reset"
        start = c.start + shift + offset
        entries.append({"file": c.name, "start": start.replace(tzinfo=zone).isoformat(timespec="seconds"),
                        "camera_clock_stop": c.stop.isoformat(), "frames": c.frames, "fps": c.fps, "rule": rule})
        prev_stop = c.stop + shift
    return entries
