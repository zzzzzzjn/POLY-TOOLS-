"""Entrypoint used by the frozen, self-contained desktop backend."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import polywav
from desktop import backend


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "inspect":
        sys.exit(backend.main())
    if len(sys.argv) > 1 and sys.argv[1] == "merge":
        sys.exit(polywav.main(sys.argv[1:]))
    print("Expected inspect or merge command", file=sys.stderr)
    sys.exit(2)
