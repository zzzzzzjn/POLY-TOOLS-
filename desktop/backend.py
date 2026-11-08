"""Narrow JSON bridge to the existing local audio core."""
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import polywav


def inspect(request):
    paths = request["paths"]
    if not isinstance(paths, list) or not 1 <= len(paths) <= 128:
        raise polywav.PolyError("Choose between 1 and 128 files.")
    records, items, errors, identities = [], [], [], set()
    for path in paths:
        try:
            p = Path(path).resolve(strict=True)
            if p.suffix.lower() != ".wav":
                raise polywav.PolyError("Only WAV files are supported.")
            item = polywav.parse_input(p)
            identity = item["snapshot"][:2]
            if identity in identities:
                raise polywav.PolyError("Duplicate input file.")
            identities.add(identity)
            channel, segment = polywav.candidate(p.name)
            records.append({"id": path, "path": path, "name": p.name,
                            "candidate": channel, "segment": segment,
                            "rate": item["rate"], "bits": item["bits"],
                            "validBits": item["valid_bits"], "encoding": item["encoding"],
                            "frames": str(item["frames"]), "timeReference": (
                                str(item["time_reference"]) if item["time_reference"] is not None else None)})
            items.append(item)
        except (OSError, ValueError, polywav.PolyError) as exc:
            errors.append(f"{path}: {exc}")
    nums = [r["candidate"] for r in records if r["candidate"] is not None]
    if len(set(nums)) != len(nums):
        errors.append("Duplicate candidate channel numbers; separate takes or segments before merging.")
    if len(nums) == len(paths) and sorted(nums) != list(range(1, len(paths) + 1)):
        errors.append("Candidate channel numbers have gaps; check and organize the source files.")
    if len(items) == len(paths):
        try:
            polywav.preflight([Path(p) for p in paths])
        except (OSError, ValueError, polywav.PolyError) as exc:
            errors.append(str(exc))
    warnings = []
    if any(r["candidate"] is None or r["segment"] is not None for r in records):
        warnings.append("Some channel numbers or segments are ambiguous. Check the mapping and take carefully.")
    if items and len(items) == len(paths) and not errors:
        container, _, size = polywav.sizes(items[0], len(items), request.get("container", "auto"))
        estimate = str(size)
    else:
        container, estimate = None, None
    return {"files": records, "errors": errors, "warnings": warnings,
            "size": estimate, "container": container}


def main():
    try:
        request = json.load(sys.stdin)
        result = inspect(request)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError, polywav.PolyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
