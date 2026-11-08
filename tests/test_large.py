"""Opt-in real >4 GiB RF64 integration test; writes over 8 GiB temporarily."""

import contextlib
import io
import json
import os
from pathlib import Path
import resource
import tempfile
import time
import unittest

import polywav
from verify_output import read_output


@unittest.skipUnless(os.environ.get("POLYWAV_LARGE_TEST") == "1", "set POLYWAV_LARGE_TEST=1")
class LargeRF64Test(unittest.TestCase):
    def test_real_non_sparse_rf64(self):
        started = time.monotonic()
        with tempfile.TemporaryDirectory(prefix="polywav-large-") as directory:
            root = Path(directory)
            baseline_source = root / "baseline.wav"
            baseline_frames = 16 * 1024 * 1024
            baseline_info = {"rate": 48000, "sample_bytes": 4, "encoding": "pcm",
                             "bits": 32, "valid_bits": 32, "frames": baseline_frames}
            container, length, total = polywav.sizes(baseline_info, 1, "auto")
            data = bytes(range(256)) * 16384
            with baseline_source.open("wb") as f:
                f.write(polywav.make_header(baseline_info, 1, container, length, total))
                for _ in range(length // len(data)):
                    f.write(data)
            manifest = root / "channels.json"
            manifest.write_text(json.dumps({"schema_version": 1, "alignment": "sample_zero_confirmed",
                                            "channels": [{"output_channel": 1, "input": "baseline.wav"}]}))
            baseline_output = root / "baseline-out.wav"
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                polywav.merge(manifest, baseline_output, "auto")
            baseline_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            baseline_source.unlink()
            baseline_output.unlink()
            baseline_output.with_suffix(".json").unlink()
            source = root / "source.wav"
            frames = (1 << 30) + 1
            info = {"rate": 48000, "sample_bytes": 4, "encoding": "pcm",
                    "bits": 32, "valid_bits": 32, "frames": frames}
            container, data_size, total_size = polywav.sizes(info, 1, "rf64")
            with source.open("wb") as f:
                f.write(polywav.make_header(info, 1, container, data_size, total_size))
                remaining = data_size
                while remaining:
                    size = min(remaining, len(data))
                    f.write(data[:size])
                    remaining -= size
                f.flush()
                os.fsync(f.fileno())
            st = source.stat()
            self.assertGreaterEqual(st.st_blocks * 512, data_size, "source must not be sparse")
            manifest.write_text(json.dumps({"schema_version": 1, "alignment": "sample_zero_confirmed",
                                            "channels": [{"output_channel": 1, "input": "source.wav"}]}))
            output = root / "out.wav"
            with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
                polywav.merge(manifest, output, "auto")
            self.assertGreaterEqual(output.stat().st_blocks * 512, data_size, "output must not be sparse")
            result = read_output(output)
            self.assertEqual((result["container"], result["frames"], result["data_size"]), ("RF64", frames, data_size))
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            limit = 64 * 1024 * 1024 if os.uname().sysname == "Darwin" else 64 * 1024
            self.assertLess(peak - baseline_rss, limit, "peak memory grew excessively with duration")
            print(f"Large RF64: data={data_size} bytes, elapsed={time.monotonic() - started:.1f}s, "
                  f"baseline_rss={baseline_rss}, peak_rss={peak} "
                  f"({'bytes' if os.uname().sysname == 'Darwin' else 'KiB'})")
