import datetime as dt
import json

import numpy as np
import pytest

from oldcam_recover import carve, cli, ecc, fat, ftl, statlog

import synth

FILES = {
    "/STATFILE.TXT": b"**REBOOT  POWER ON 2001, 1, 1,12, 0, 0\r\n",
    "/DCIM/100COACH/PICT0001.AVI": bytes(range(256)) * 37 + b"end-of-one",
    "/DCIM/100COACH/PICT0002.AVI": b"\x00\x11" * 5000,
}


@pytest.fixture(scope="module")
def volume():
    return synth.fat16_volume(FILES)


def test_ecc_corrects_single_bit_and_flags_worse():
    rng = np.random.default_rng(7)
    halves = rng.integers(0, 256, (50, 256), dtype=np.uint8)
    stored = ecc.compute(halves)
    bad = halves.copy()
    bad[np.arange(50), rng.integers(0, 256, 50)] ^= (1 << rng.integers(0, 8, 50)).astype(np.uint8)
    status, byte, bit = ecc.check(stored, ecc.compute(bad))
    assert (status == ecc.CORRECTED).all()
    bad[np.arange(50), byte] ^= (1 << bit).astype(np.uint8)
    assert (bad == halves).all()
    two = halves.copy()
    two[:, 0] ^= 1
    two[:, 1] ^= 1
    assert (ecc.check(stored, ecc.compute(two))[0] != ecc.CLEAN).all()
    flipped_ecc = stored.copy()
    flipped_ecc[:, 0] ^= 0x10
    assert (ecc.check(flipped_ecc, stored)[0] == ecc.ECC_BYTE_ERROR).all()


def test_address_field_round_trip_and_parity():
    for lba in (0, 1, 2, 3, 500, 999):
        f = ftl.address_field(lba)
        assert bin(f).count("1") % 2 == 0
        assert ftl.lba_of(f) == lba
        assert ftl.lba_of(f ^ 1) is None  # parity broken
    assert ftl.lba_of(0x1001) == 0 and ftl.lba_of(0x1002) == 1 and ftl.lba_of(0x1007) == 3


@pytest.mark.parametrize("name,with_ecc", [("small", True), ("large", False)])
def test_rebuild_unscrambles_blocks(volume, name, with_ecc):
    g = ftl.GEOMETRIES[name]
    zone_phys, zone_log = 48, 40
    zones = -(-(len(volume) + 64 * 512) // (zone_log * g.block_bytes)) + 1  # room to spare
    blocks = zones * zone_log
    logical = synth.logical_image(volume, blocks, g.block_bytes)
    flip = (3, 1234, 5) if with_ecc else None  # logical block 3 holds FAT data
    raw = synth.nand(logical, g, zone_phys, zone_log, with_ecc=with_ecc, flip=flip)
    assert ftl.detect(raw) is g
    image, report = ftl.rebuild(raw, g, zone_phys, zone_log)
    assert image == logical
    assert report["problems"] == []
    if with_ecc:
        assert report["ecc_sector_halves"]["corrected"] == 1
        assert report["ecc_sector_halves"]["uncorrectable"] == 0
    else:
        assert report["ecc_sector_halves"]["sectors_without_ecc"] > 0


def test_physical_order_alone_is_garbage(volume):
    """Why stripping the spare area is not enough: the volume is not where it should be."""
    g = ftl.GEOMETRIES["small"]
    logical = synth.logical_image(volume, 200, g.block_bytes)
    raw = np.frombuffer(synth.nand(logical, g, 48, 40), np.uint8).reshape(-1, g.page)[:, :g.data].tobytes()
    assert raw[:len(logical)] != logical


def test_fat_extract_deleted_and_carve(volume):
    v = fat.find_volumes(synth.logical_image(volume, 200, 16384))
    assert len(v) == 1
    entries = {e.path: e for e in v[0].walk(include_deleted=True)}
    for path, content in FILES.items():
        assert v[0].read(entries[path]) == content
    assert entries["/DCIM/100COACH/?ICT0099.AVI"].deleted
    found = list(carve.find_avis(v[0]))
    assert len(found) == 1
    _, data, finalised = found[0]
    assert not finalised
    assert data[:len(synth.UNFINALISED)] == synth.UNFINALISED  # including its final 0xff byte
    assert len(data) == -(-len(synth.UNFINALISED) // 512) * 512 and data.startswith(b"RIFF")


LOG = """**REBOOT  POWER ON 2007, 9, 8,21,59,28
REC,PICT0001.AVI,2007, 9, 8,21,59,46,  135,30,   524516, 42
REC,PICT0002.AVI,2007, 9, 8,22,10,00,  268,30,  1580280, 42
**REBOOT  POWER OFF 2007, 9, 8,22,10,30
**REBOOT  POWER ON 2007, 9, 8,22,20,00
DEL,PICT0002.AVI,2007, 9, 8,22,10,00
REC,PICT0002.AVI,2007, 9, 8,22,21,00,   60,30,   250000, 42
**REBOOT  POWER ON 2012, 6,24,20,55, 2
REC,PICT0003.AVI,2012, 6,24,20,55,24,  354,30,  1381968, 44
"""


def test_statlog_parse_and_timeline():
    log = statlog.parse(LOG)
    assert [c.name for c in log.clips] == ["PICT0001.AVI", "PICT0002.AVI", "PICT0003.AVI"]
    assert [d.frames for d in log.deleted] == [268]
    assert log.sessions[-1][1] is True  # power-on without power-off before it
    assert log.clips[0].start == dt.datetime(2007, 9, 8, 21, 59, 41, 500000)  # stop - 135/30 s
    plain = statlog.timeline(log)
    assert plain[2]["start"].startswith("2012-06-24")
    fixed = statlog.timeline(log, -4, "-04:00", follows_previous=["PICT0003.AVI"])
    assert fixed[0]["start"] == "2007-09-08T17:59:41-04:00"
    # PICT0003's session power-on is placed at PICT0002's stop (22:21:00 camera clock, -4 h)
    assert fixed[2]["start"] == "2007-09-08T18:21:10-04:00"


def test_walk_survives_looping_directories(volume):
    """Damaged flash can make a directory point back at itself; the walk must still end."""
    img = bytearray(synth.logical_image(volume, 200, 16384))
    v = fat.find_volumes(bytes(img))[0]
    dcim = next(e for e in v.walk() if e.path == "/DCIM")
    o = v.data_off + (dcim.first_cluster - 2) * v.cluster + 64  # third slot, after . and ..
    img[o:o + 32] = synth._entry("LOOP", 0x10, dcim.first_cluster, 0)
    paths = [e.path for e in fat.Volume(bytes(img), v.offset).walk()]
    assert "/DCIM/LOOP" in paths and len(paths) < 50


def test_names_from_the_dump_cannot_escape_the_output(tmp_path):
    from oldcam_recover.cli import _inside, _safe_part
    assert _safe_part("..") == "_" and _safe_part(".") == "_" and _safe_part("") == "_"
    assert _safe_part("a\\b\x00c/d") == "a_b_c_d"
    assert _safe_part("PICT0001.AVI") == "PICT0001.AVI"
    p = _inside(tmp_path, "..", "..", "etc", "passwd")
    assert p.resolve().is_relative_to(tmp_path.resolve())


def test_mp4_refuses_clip_names_that_are_paths(tmp_path, capsys):
    (tmp_path / "videos").mkdir()
    (tmp_path / "secret.avi").write_bytes(b"not yours")
    tl = tmp_path / "timeline.json"
    bad = ["../secret.avi", str(tmp_path / "secret.avi"), "a/b.avi", "..", "x\\y.avi"]
    tl.write_text(json.dumps([{"file": n, "start": "2007-09-08T18:00:00-04:00"} for n in bad]))
    cli.main(["mp4", str(tl), "--videos", str(tmp_path / "videos"), "--out", str(tmp_path / "mp4")])
    assert capsys.readouterr().out.count("must be plain file names") == len(bad)
    assert not any((tmp_path / "mp4").iterdir())


def test_mp4_refuses_files_that_are_not_avi(tmp_path, capsys):
    """A playlist disguised as a clip must never reach ffmpeg's format auto-detection."""
    (tmp_path / "videos").mkdir()
    (tmp_path / "videos" / "PICT0001.AVI").write_text("#EXTM3U\nhttp://example.invalid/x.ts\n")
    tl = tmp_path / "timeline.json"
    tl.write_text(json.dumps([{"file": "PICT0001.AVI", "start": "2007-09-08T18:00:00-04:00"}]))
    cli.main(["mp4", str(tl), "--videos", str(tmp_path / "videos"), "--out", str(tmp_path / "mp4")])
    assert "is not an AVI file" in capsys.readouterr().out
    assert not any((tmp_path / "mp4").iterdir())


def test_cli_recover_end_to_end(volume, tmp_path):
    g = ftl.GEOMETRIES["small"]
    logical = synth.logical_image(volume, ftl.ZONE_LOG, g.block_bytes)
    dump = tmp_path / "dump.bin"
    dump.write_bytes(synth.nand(logical, g, ftl.ZONE_PHYS, ftl.ZONE_LOG))
    out = tmp_path / "out"
    cli.main(["recover", str(dump), str(out)])
    for path, content in FILES.items():
        assert (out / "files" / path.lstrip("/")).read_bytes() == content
    assert list((out / "carved").glob("*-unfinalised.avi"))
    report = json.loads((out / "report.json").read_text())
    assert report["blocks"]["unreadable_address"] == 0
    with pytest.raises(SystemExit):
        cli.main(["recover", str(dump), str(out)])  # refuses to overwrite
