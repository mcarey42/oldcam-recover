# Fixing dates and importing into Apple Photos or Google Photos

The camera's AVI files (MPEG-4 Part 2 video, MP2 audio) play in VLC and mpv, but
Apple Photos and Google Photos don't import them. And the dates are often wrong.
Two commands deal with both, and neither touches your original files.

## 1. Work out the real times

```sh
oldcam-recover timeline recovered/files/STATFILE.TXT -o timeline.json
```

This prints each clip's start time by the camera's clock. Compare it with what you
remember, then correct it with any of these options, which can be combined:

- **`--clock-offset H`**: hours to add to the camera's clock. One camera we tested
  had its clock set to UTC at the factory, so for US Eastern daylight time it needed
  `--clock-offset=-4`.
- **`--tz=-04:00`**: the time zone to stamp on the result. Write negative offsets with
  `=` as shown, so they aren't mistaken for an option.
- **`--follows-previous PICT0014.AVI`**: the clock jumped before this clip, usually
  after a battery change. Its session gets moved so it starts right after the
  previous clip ended. The diary shows these jumps as a `POWER ON` with no
  `POWER OFF` before it, followed by an odd date. Only you can say whether a jump was
  a reset or a real gap, so nothing moves unless you name it.
- **`--set PICT0025.AVI=2008-06-23T18:00:00`**: give one clip a time you know, for
  example a different event recorded later on the same camera.

Example that fixed one wedding camera:

```sh
oldcam-recover timeline recovered/files/STATFILE.TXT -o timeline.json \
  --clock-offset=-4 --tz=-04:00 --follows-previous PICT0014.AVI \
  --set PICT0025.AVI=2008-06-23T18:00:00
```

`timeline.json` is plain text: you can also edit any `start` by hand.

If the camera's clock was never set (dates like 1934 or 2062), the order and spacing
of the clips are still right, but the date isn't. Use `--set` on one clip you can
place, plus `--clock-offset`, or edit the file.

## 2. Make MP4s with those dates

```sh
oldcam-recover mp4 timeline.json --videos recovered/files/DCIM/100COACH --out mp4/
```

Each clip becomes `YYYY-MM-DD_HHMMSS_PICTnnnn.mp4`: H.264 video at the original
resolution (nothing is upscaled) and AAC audio. The recording time is embedded both
as the standard `creation_time` and as `com.apple.quicktime.creationdate`, the field
Apple Photos reads, and the file's own date is set to match. Import the `mp4/` folder
and the clips land on the right day.

Keep the original AVIs as your archive. The MP4s are copies for viewing and sharing.
