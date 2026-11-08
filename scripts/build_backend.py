"""Build the standalone desktop backend and macOS app icon."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def main():
    if sys.platform != "darwin" or os.uname().machine != "arm64":
        raise SystemExit("This packaging target requires an Apple Silicon Mac.")
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        raise SystemExit("PyInstaller is missing. Install requirements-build.txt into the selected Python environment.")
    subprocess.run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--name", "poly-backend", "--onedir", "--paths", str(ROOT),
        "--distpath", str(ROOT / "build/backend"), "--workpath", str(ROOT / "build/pyinstaller"),
        "--specpath", str(ROOT / "build"), str(ROOT / "desktop/entry.py")
    ], check=True, cwd=ROOT)
    with tempfile.TemporaryDirectory(prefix="poly-icon-") as temp:
        icons = Path(temp) / "icon.iconset"
        icons.mkdir()
        for points in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                pixels = points * scale
                suffix = "@2x" if scale == 2 else ""
                subprocess.run(["sips", "-z", str(pixels), str(pixels), str(ROOT / "assets/icon.png"),
                                "--out", str(icons / f"icon_{points}x{points}{suffix}.png")],
                               check=True, stdout=subprocess.DEVNULL)
        subprocess.run(["iconutil", "-c", "icns", str(icons), "-o", str(ROOT / "assets/icon.icns")], check=True)


if __name__ == "__main__":
    main()
