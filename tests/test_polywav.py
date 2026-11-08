import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import polywav
from verify_output import read_output


def chunk(name, data):
    return name + struct.pack("<I", len(data)) + data + (b"\0" if len(data) % 2 else b"")


def fixture(path, payload, bits=16, tag=1, rate=48000, valid=None, ref=None,
            rf64=False, extra=b"", duplicate=False):
    b = bits // 8
    valid = bits if valid is None else valid
    if valid != bits:
        fmt = struct.pack("<HHIIHHHHI", 0xFFFE, 1, rate, rate * b, b, bits, 22, valid, 0)
        fmt += struct.pack("<I", tag) + polywav.GUID_TAIL
    else:
        fmt = struct.pack("<HHIIHH", tag, 1, rate, rate * b, b, bits)
    bext = bytearray(346)
    if ref is not None:
        struct.pack_into("<Q", bext, 338, ref)
    chunks = chunk(b"fmt ", fmt) + extra + (chunk(b"bext", bext) if ref is not None else b"")
    if rf64:
        data = b"data" + struct.pack("<I", 0xffffffff) + payload + (b"\0" if len(payload) % 2 else b"")
        rest = chunks + data
        size = 4 + 36 + len(rest)
        body = b"WAVE" + struct.pack("<4sIQQQI", b"ds64", 28, size, len(payload), len(payload) // b, 0) + rest
        path.write_bytes(b"RF64" + struct.pack("<I", 0xffffffff) + body)
    else:
        data = chunk(b"data", payload)
        body = b"WAVE" + chunks + data + (data if duplicate else b"")
        path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


class PolyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def manifest(self, paths, name="channels.json"):
        p = self.root / name
        p.write_text(json.dumps({"schema_version": 1, "alignment": "sample_zero_confirmed",
                                 "channels": [{"output_channel": i + 1, "input": str(path)} for i, path in enumerate(paths)]}))
        return p

    def run_merge(self, paths, force="auto"):
        out = self.root / "out.wav"
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            polywav.merge(self.manifest(paths), out, force)
        return out

    def test_channel_counts_order_and_chunk_boundaries(self):
        for channels in (1, 2, 12, 64, 128):
            with self.subTest(channels=channels):
                paths = []
                frames = 8201 if channels == 2 else 17
                for ch in range(channels):
                    p = self.root / f"CH{ch + 1}.wav"
                    payload = b"".join(struct.pack("<h", (ch * 113 + i) % 32768) for i in range(frames))
                    fixture(p, payload)
                    paths.append(p)
                paths.reverse()
                out = self.run_merge(paths)
                parsed = read_output(out)
                self.assertEqual((parsed["channels"], parsed["frames"]), (channels, frames))
                with out.open("rb") as f:
                    f.seek(parsed["data_offset"])
                    result = f.read()
                expected = b"".join(struct.pack("<h", (ch * 113 + i) % 32768)
                                    for i in range(frames) for ch in reversed(range(channels)))
                self.assertEqual(result, expected)
                out.unlink()
                out.with_suffix(".json").unlink()

    def test_formats_bit_patterns_and_rf64(self):
        for bits, tag, payload in ((16, 1, bytes.fromhex("0080ff7f0000ffff")),
                                   (24, 1, bytes.fromhex("000080ffff7f000000ffffff")),
                                   (32, 1, bytes.fromhex("00000080ffffff7f00000000ffffffff")),
                                   (32, 3, bytes.fromhex("000000800000c07f0100807f000080ff"))):
            for container in ("auto", "rf64"):
                with self.subTest(bits=bits, tag=tag, container=container):
                    p = self.root / "a.wav"
                    fixture(p, payload, bits, tag, extra=chunk(b"JUNK", b"odd"))
                    out = self.run_merge([p], container)
                    info = read_output(out)
                    self.assertEqual(info["container"], "RF64" if container == "rf64" else "RIFF")
                    with out.open("rb") as f:
                        f.seek(info["data_offset"])
                        self.assertEqual(f.read(len(payload)), payload)
                    self.assertEqual(json.loads(out.with_suffix(".json").read_text())["validation"]["status"], "passed")
                    out.unlink()
                    out.with_suffix(".json").unlink()

    def test_zero_and_rf64_extensible_input(self):
        p = self.root / "a.wav"
        fixture(p, b"", bits=24, valid=20, rf64=True, ref=42)
        out = self.run_merge([p])
        self.assertEqual(read_output(out)["frames"], 0)
        self.assertEqual(read_output(out)["valid_bits"], 20)
        self.assertFalse(json.loads(out.with_suffix(".json").read_text())["embedded_timecode_preserved"])

    def test_preflight_mismatches(self):
        a, b = self.root / "a.wav", self.root / "b.wav"
        for kwargs in ({"rate": 44100}, {"bits": 32, "tag": 3}, {"bits": 16, "valid": 15},
                       {"ref": 2}, {"payload": b"\x00\x00"}):
            with self.subTest(kwargs=kwargs):
                fixture(a, b"\x00" * 4, ref=1)
                kw = dict(kwargs)
                payload = kw.pop("payload", b"\x00" * (8 if kw.get("bits") == 32 else 4))
                fixture(b, payload, **kw)
                with self.assertRaises(polywav.PolyError):
                    self.run_merge([a, b])
                self.assertFalse((self.root / "out.wav").exists())

    def test_malformed_and_unsupported(self):
        p = self.root / "a.wav"
        fixture(p, b"\x00\x00", duplicate=True)
        with self.assertRaises(polywav.PolyError):
            polywav.parse_input(p)
        fixture(p, b"\x00", bits=16)
        with self.assertRaises(polywav.PolyError):
            polywav.parse_input(p)
        fixture(p, b"\0\0", extra=chunk(b"LIST", b"wavl"))
        with self.assertRaises(polywav.PolyError):
            polywav.parse_input(p)
        fixture(p, b"\0\0")
        p.write_bytes(p.read_bytes()[:-1])
        with self.assertRaises(polywav.PolyError):
            polywav.parse_input(p)
        p.write_bytes(b"RIFX" + b"\0" * 8)
        with self.assertRaises(polywav.PolyError):
            polywav.parse_input(p)

    def test_mapping_rejections_and_candidates(self):
        self.assertEqual([polywav.candidate(x)[0] for x in ("CH1.wav", "CH2.wav", "Take2_CH10_001.wav")], [1, 2, 10])
        self.assertEqual(polywav.candidate("Take2_CH10_001.wav")[1], 1)
        self.assertEqual(polywav.candidate("take123.wav"), (None, None))
        paths = [self.root / x for x in ("CH1.wav", "CH3.wav", "Take2_CH1_001.wav")]
        for p in paths:
            fixture(p, b"\0\0")
        for files in ([paths[0], paths[1]], [paths[0], paths[0]], [paths[0], paths[2]]):
            with self.assertRaises(polywav.PolyError):
                polywav.load_manifest(self.manifest(files))

    def test_no_overwrite_cancel_change_and_failure_cleanup(self):
        p = self.root / "a.wav"
        fixture(p, b"\0\0" * 10)
        manifest = self.manifest([p])
        out = self.root / "out.wav"
        out.write_bytes(b"existing")
        with self.assertRaises(polywav.PolyError):
            polywav.merge(manifest, out, "auto")
        self.assertEqual(out.read_bytes(), b"existing")
        out.unlink()
        for exception in (KeyboardInterrupt(), OSError("disk full")):
            with self.subTest(exception=exception), mock.patch("polywav.verify", side_effect=exception):
                with self.assertRaises(type(exception)), contextlib.redirect_stderr(io.StringIO()):
                    polywav.merge(manifest, out, "auto")
                self.assertFalse(out.exists())
                self.assertFalse(out.with_suffix(".json").exists())
                self.assertEqual(list(self.root.glob(".polywav-*")), [])
        before = polywav.parse_input(p)
        p.write_bytes(p.read_bytes() + b"x")
        with self.assertRaises(polywav.PolyError):
            polywav.ensure_unchanged(p, before["snapshot"])

    def test_publication_race_and_source_conflict(self):
        p = self.root / "a.wav"
        fixture(p, b"\0\0")
        original = p.read_bytes()
        manifest = self.manifest([p])
        with self.assertRaises(polywav.PolyError):
            polywav.merge(manifest, p, "auto")
        out = self.root / "out.wav"
        real_link = os.link
        def link(source, target):
            if target == out:
                out.write_bytes(b"racer")
            return real_link(source, target)
        with mock.patch("polywav.os.link", side_effect=link), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(FileExistsError):
                polywav.merge(manifest, out, "auto")
        self.assertEqual(out.read_bytes(), b"racer")
        self.assertEqual(p.read_bytes(), original)
        self.assertFalse(out.with_suffix(".json").exists())
        self.assertEqual(list(self.root.glob(".polywav-*")), [])

    def test_verify_detects_payload_corruption(self):
        p = self.root / "a.wav"
        fixture(p, b"\x01\x02" * 4)
        out = self.run_merge([p])
        info = read_output(out)
        with out.open("r+b") as f:
            f.seek(info["data_offset"])
            f.write(b"x")
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(polywav.main(["verify", "--manifest", str(self.root / "channels.json"), "--output", str(out)]), 1)

    def test_change_during_verification_and_insufficient_space(self):
        source = self.root / "a.wav"
        fixture(source, b"\x01\x02" * 10)
        manifest = self.manifest([source])
        output = self.root / "out.wav"
        with mock.patch("polywav.shutil.disk_usage", return_value=mock.Mock(free=0)):
            with self.assertRaisesRegex(polywav.PolyError, "磁盘空间不足"):
                polywav.merge(manifest, output, "auto")
        real_verify = polywav.verify
        def mutate(*args):
            result = real_verify(*args)
            with source.open("ab") as f:
                f.write(b"x")
            return result
        with mock.patch("polywav.verify", side_effect=mutate), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(polywav.PolyError, "输入文件已改变"):
                polywav.merge(manifest, output, "auto")
        self.assertFalse(output.exists())
        self.assertFalse(output.with_suffix(".json").exists())
        self.assertEqual(list(self.root.glob(".polywav-*")), [])

    def test_cli_inspect_merge_and_verify(self):
        source = self.root / "CH1.wav"
        fixture(source, b"\x01\x02" * 10)
        manifest = self.manifest([source])
        output = self.root / "out.wav"
        script = str(Path(polywav.__file__))
        def run(*args):
            return subprocess.run([sys.executable, script, *map(str, args)], text=True,
                                  capture_output=True, check=True)
        inspected = json.loads(run("inspect", self.root).stdout)
        self.assertEqual(inspected["files"][0]["candidate_channel"], 1)
        run("merge", "--manifest", manifest, "--output", output, "--verify")
        self.assertTrue(output.exists())
        self.assertIn("校验通过", run("verify", "--manifest", manifest, "--output", output).stdout)

    @unittest.skipUnless(shutil.which("afinfo"), "macOS afinfo unavailable")
    def test_external_afinfo_reader(self):
        paths = []
        for ch in range(2):
            p = self.root / f"CH{ch + 1}.wav"
            fixture(p, bytes.fromhex("000080ffff7f"), bits=24)
            paths.append(p)
        out = self.run_merge(paths)
        result = subprocess.run(["afinfo", str(out)], text=True, capture_output=True, check=True).stdout
        self.assertRegex(result, r"2 ch,\s+48000 Hz, lpcm .*24-bit")
        self.assertRegex(result, r"audio packets:\s+2\b")


if __name__ == "__main__":
    unittest.main()
