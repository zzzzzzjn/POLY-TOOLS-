#!/usr/bin/env python3
"""Offline, byte-preserving mono WAV to multichannel WAV converter."""

import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import tempfile
import time

from verify_output import read_output

VERSION = "1.0.0"
BLOCK_FRAMES = 8192
U32 = 0xFFFFFFFF
GUID_TAIL = bytes.fromhex("00001000800000aa00389b71")


class PolyError(Exception):
    pass


def exact(f, size):
    data = f.read(size)
    if len(data) != size:
        raise PolyError("文件截断或读取不足")
    return data


def snapshot(path):
    s = path.stat()
    return (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)


def ensure_unchanged(path, before, f=None):
    if snapshot(path) != before:
        raise PolyError(f"输入文件已改变: {path}")
    if f:
        s = os.fstat(f.fileno())
        if (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns) != before:
            raise PolyError(f"打开的输入文件已改变: {path}")


def parse_input(path):
    path = Path(path)
    before = snapshot(path)
    with path.open("rb") as f:
        header = exact(f, 12)
        kind, declared, wave = struct.unpack("<4sI4s", header)
        if kind not in (b"RIFF", b"RF64") or wave != b"WAVE":
            raise PolyError(f"{path}: 不支持的容器（仅支持小端 RIFF/RF64 WAVE）")
        end = declared + 8
        ds_data = None
        ds_frames = None
        table = {}
        if kind == b"RF64":
            if declared != U32:
                raise PolyError(f"{path}: RF64 缺少大小哨兵")
            cid, n = struct.unpack("<4sI", exact(f, 8))
            if cid != b"ds64" or n < 28 or n > 12 * 100000 + 28:
                raise PolyError(f"{path}: RF64 ds64 无效或过大")
            raw = exact(f, n)
            riff_size, ds_data, ds_frames, count = struct.unpack_from("<QQQI", raw)
            if n != 28 + count * 12:
                raise PolyError(f"{path}: ds64 表长度无效")
            for i in range(count):
                name, length = struct.unpack_from("<4sQ", raw, 28 + 12 * i)
                table.setdefault(name, []).append(length)
            if n & 1:
                exact(f, 1)
            end = riff_size + 8
        if end != before[2] or end < f.tell():
            raise PolyError(f"{path}: RIFF 大小与文件大小不一致")
        fmt = None
        data = None
        time_ref = None
        chunks = []
        while f.tell() < end:
            if end - f.tell() < 8:
                raise PolyError(f"{path}: 不完整 chunk 头")
            cid, size = struct.unpack("<4sI", exact(f, 8))
            if size == U32:
                if kind != b"RF64":
                    raise PolyError(f"{path}: RIFF chunk 大小无效")
                if cid == b"data":
                    size = ds_data
                elif table.get(cid):
                    size = table[cid].pop(0)
                else:
                    raise PolyError(f"{path}: ds64 缺少 chunk 大小")
            pos = f.tell()
            if pos + size + (size & 1) > end:
                raise PolyError(f"{path}: chunk 超出文件范围")
            chunks.append(cid.decode("ascii", "replace"))
            if cid == b"fmt ":
                if fmt is not None or size < 16 or size > 65536:
                    raise PolyError(f"{path}: fmt chunk 重复或无效")
                raw = exact(f, size)
                tag, channels, rate, byte_rate, align, bits = struct.unpack_from("<HHIIHH", raw)
                valid = bits
                if tag == 0xFFFE:
                    if size < 40:
                        raise PolyError(f"{path}: extensible fmt 不完整")
                    cb, valid, mask = struct.unpack_from("<HHI", raw, 16)
                    if cb < 22 or 18 + cb > size:
                        raise PolyError(f"{path}: extensible fmt 扩展长度无效")
                    guid = raw[24:40]
                    if guid[4:] != GUID_TAIL:
                        raise PolyError(f"{path}: 不支持的 subtype GUID")
                    tag = struct.unpack_from("<I", guid)[0]
                elif size != 16:
                    if size < 18 or 18 + struct.unpack_from("<H", raw, 16)[0] > size:
                        raise PolyError(f"{path}: fmt 扩展长度无效")
                if channels != 1:
                    raise PolyError(f"{path}: 输入必须是 mono，实际 {channels} 通道")
                if tag not in (1, 3) or bits not in (16, 24, 32) or (tag == 3 and bits != 32):
                    raise PolyError(f"{path}: 不支持的编码或位深")
                if not 0 < valid <= bits or (tag == 3 and valid != 32):
                    raise PolyError(f"{path}: 有效位数无效")
                if rate == 0 or align != bits // 8 or byte_rate != rate * align:
                    raise PolyError(f"{path}: 采样率、块对齐或字节率无效")
                fmt = {"encoding": "pcm" if tag == 1 else "float", "rate": rate,
                       "bits": bits, "valid_bits": valid, "sample_bytes": align}
            elif cid == b"data":
                if data is not None:
                    raise PolyError(f"{path}: 不支持多个 data chunk；请先完成分段整理")
                data = (pos, size)
            elif cid == b"LIST" and size >= 4:
                if exact(f, 4) == b"wavl":
                    raise PolyError(f"{path}: 不支持 wavl；请先完成分段整理")
            elif cid == b"bext":
                if time_ref is not None or size < 346:
                    raise PolyError(f"{path}: bext 重复或不完整")
                f.seek(pos + 338)
                time_ref = struct.unpack("<Q", exact(f, 8))[0]
            elif cid == b"ds64":
                raise PolyError(f"{path}: 意外或重复的 ds64")
            f.seek(pos + size + (size & 1))
        if fmt is None or data is None:
            raise PolyError(f"{path}: 缺少 fmt 或 data")
        if data[1] % fmt["sample_bytes"]:
            raise PolyError(f"{path}: data 包含不完整采样帧")
        frames = data[1] // fmt["sample_bytes"]
        if kind == b"RF64" and (ds_data != data[1] or ds_frames != frames):
            raise PolyError(f"{path}: ds64 数据大小或帧数不一致")
        ensure_unchanged(path, before, f)
    return dict(fmt, path=path, snapshot=before, data_offset=data[0], data_size=data[1],
                frames=frames, time_reference=time_ref, container=kind.decode(), chunks=chunks)


def candidate(name):
    matches = list(re.finditer(r"(?i)(?<![a-z0-9])ch(?:annel)?[ _-]*0*(\d+)(?!\d)", name))
    if len(matches) != 1:
        return None, None
    m = matches[0]
    segment = re.match(r"[_-](\d+)(?=\.|$)", name[m.end():])
    return int(m.group(1)), int(segment.group(1)) if segment else None


def natural_key(name):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", name)]


def load_manifest(path):
    path = Path(path).resolve()
    with path.open(encoding="utf-8") as f:
        obj = json.load(f)
    if not isinstance(obj, dict) or obj.get("schema_version") != 1:
        raise PolyError("清单 schema_version 必须为 1")
    if obj.get("alignment") != "sample_zero_confirmed":
        raise PolyError("请确认同一段同步录音，并在清单中设置 alignment 为 sample_zero_confirmed")
    channels = obj.get("channels")
    if not isinstance(channels, list) or not 1 <= len(channels) <= 128:
        raise PolyError("清单必须包含 1–128 个通道")
    paths = []
    for i, item in enumerate(channels, 1):
        if not isinstance(item, dict) or type(item.get("output_channel")) is not int or item["output_channel"] != i:
            raise PolyError(f"清单输出通道必须连续并按顺序排列，期望 {i}")
        if not isinstance(item.get("input"), str) or not item["input"].strip():
            raise PolyError(f"通道 {i} 的 input 无效")
        paths.append((path.parent / item["input"]).resolve(strict=True))
    ids = [(p.stat().st_dev, p.stat().st_ino) for p in paths]
    if len(set(ids)) != len(ids):
        raise PolyError("清单包含重复输入文件")
    nums = [candidate(p.name) for p in paths]
    known = [n for n, _ in nums if n is not None]
    if len(known) != len(set(known)):
        raise PolyError("文件名含重复候选通道号或多个分段；请先完成分段整理")
    if len(known) == len(paths) and sorted(known) != list(range(1, len(paths) + 1)):
        raise PolyError("文件名的候选通道号缺号；请先检查并整理映射")
    return path, paths


def preflight(paths):
    inputs = [parse_input(p) for p in paths]
    base = inputs[0]
    fields = ("rate", "encoding", "bits", "valid_bits", "frames")
    errors = []
    for item in inputs[1:]:
        for field in fields:
            if item[field] != base[field]:
                errors.append(f"{item['path']}: {field}={item[field]}，期望 {base[field]}")
    refs = [x["time_reference"] for x in inputs]
    if all(x is not None for x in refs) and len(set(refs)) != 1:
        errors.append("BWF TimeReference 不一致: " + ", ".join(map(str, refs)))
    if errors:
        raise PolyError("预检失败:\n" + "\n".join(errors))
    return inputs


def sizes(info, channels, forced):
    data_size = info["frames"] * info["sample_bytes"] * channels
    fmt_size = 48
    fact_size = 12 if info["encoding"] == "float" else 0
    riff_size = 4 + fmt_size + fact_size + 8 + data_size + (data_size & 1)
    rf64 = forced == "rf64" or riff_size > U32 or data_size > U32
    if info["rate"] * info["sample_bytes"] * channels > U32:
        raise PolyError("输出字节率超过 WAV 字段限制")
    return ("RF64" if rf64 else "RIFF", data_size, riff_size + (36 if rf64 else 0) + 8)


def make_header(info, channels, container, data_size, total_size):
    rate = info["rate"]
    block = info["sample_bytes"] * channels
    tag = 1 if info["encoding"] == "pcm" else 3
    guid = struct.pack("<I", tag) + GUID_TAIL
    fmt = struct.pack("<HHIIHHHHI", 0xFFFE, channels, rate, rate * block,
                      block, info["bits"], 22, info["valid_bits"], 0) + guid
    out = bytearray(struct.pack("<4sI4s", container.encode(),
                                U32 if container == "RF64" else total_size - 8, b"WAVE"))
    if container == "RF64":
        out += struct.pack("<4sIQQQI", b"ds64", 28, total_size - 8, data_size, info["frames"], 0)
    out += struct.pack("<4sI", b"fmt ", len(fmt)) + fmt
    if tag == 3:
        out += struct.pack("<4sII", b"fact", 4, min(info["frames"], U32))
    out += struct.pack("<4sI", b"data", U32 if container == "RF64" else data_size)
    return out


def progress(phase, frames, total, start, last):
    now = time.monotonic()
    if now - last >= 1 or frames == total:
        speed = frames / max(now - start, 0.001)
        pct = 100 if total == 0 else frames * 100 / total
        print(f"\r{phase}: {frames}/{total} 帧 ({pct:.1f}%) {speed:.0f} 帧/秒", end="\n" if frames == total else "", file=sys.stderr, flush=True)
        return now
    return last


def hash_inputs(inputs, handles):
    hashes = [hashlib.sha256() for _ in inputs]
    frames = inputs[0]["frames"]
    b = inputs[0]["sample_bytes"]
    for f, item in zip(handles, inputs):
        f.seek(item["data_offset"])
    for start in range(0, frames, BLOCK_FRAMES):
        n = min(BLOCK_FRAMES, frames - start) * b
        for f, h in zip(handles, hashes):
            h.update(exact(f, n))
    return [h.hexdigest() for h in hashes]


def verify(path, inputs, expected_hashes):
    info = read_output(path)
    base = inputs[0]
    expected = (len(inputs), base["rate"], base["encoding"], base["bits"], base["valid_bits"], base["frames"])
    actual = (info["channels"], info["rate"], info["encoding"], info["bits"], info["valid_bits"], info["frames"])
    if actual != expected:
        raise PolyError(f"输出头校验失败: {actual} != {expected}")
    channels = len(inputs)
    b = base["sample_bytes"]
    hashes = [hashlib.sha256() for _ in inputs]
    start_time = time.monotonic()
    last = 0
    with open(path, "rb") as f:
        f.seek(info["data_offset"])
        for start in range(0, base["frames"], BLOCK_FRAMES):
            count = min(BLOCK_FRAMES, base["frames"] - start)
            block = exact(f, count * channels * b)
            if channels == 1:
                hashes[0].update(block)
            else:
                for ch, h in enumerate(hashes):
                    data = bytearray(count * b)
                    for byte in range(b):
                        data[byte::b] = block[ch * b + byte::channels * b]
                    h.update(data)
            last = progress("校验", start + count, base["frames"], start_time, last)
    if not base["frames"]:
        progress("校验", 0, 0, start_time, 0)
    actual_hashes = [h.hexdigest() for h in hashes]
    if actual_hashes != expected_hashes:
        bad = [str(i + 1) for i, (a, b_) in enumerate(zip(actual_hashes, expected_hashes)) if a != b_]
        raise PolyError("输出逐通道 SHA-256 不匹配: " + ", ".join(bad))
    return info


def publish(temp, final):
    # Hard-link creation is atomic and refuses an existing destination.
    os.link(temp, final)


def merge(manifest, output, force):
    manifest_path, paths = load_manifest(manifest)
    inputs = preflight(paths)
    output = Path(output).absolute()
    if output.suffix.lower() != ".wav":
        raise PolyError("输出扩展名必须为 .wav")
    report_path = output.with_suffix(".json")
    if output == report_path or output.exists() or report_path.exists() or output.is_symlink() or report_path.is_symlink():
        raise PolyError("输出 WAV 或 JSON 已存在，拒绝覆盖")
    if output.resolve() in paths or report_path.resolve() in paths or output.resolve() == manifest_path or report_path.resolve() == manifest_path:
        raise PolyError("输出路径与输入或清单冲突")
    container, data_size, total_size = sizes(inputs[0], len(inputs), force)
    free = shutil.disk_usage(output.parent).free
    if free < total_size + 1024 * 1024:
        raise PolyError(f"磁盘空间不足: 需要至少 {total_size + 1024 * 1024} 字节，可用 {free}")
    try:
        bits = os.pathconf(output.parent, "PC_FILESIZEBITS")
        if bits > 0 and total_size > (1 << (bits - 1)) - 1:
            raise PolyError(f"输出大小超过文件系统报告的 {bits} 位文件大小限制")
    except (ValueError, OSError):
        print("警告: 无法确定文件系统的最大文件大小，写入失败时将清理临时文件", file=sys.stderr)
    print(f"预检: {len(inputs)} 通道，{inputs[0]['frames']} 帧，{container}，预计 {total_size} 字节；输出目录 {output.parent}", file=sys.stderr)
    refs = [x["time_reference"] for x in inputs]
    aligned = "matching_bwf_time_reference_and_sample_zero_confirmed" if all(x is not None for x in refs) else "start_unverified_sample_zero_confirmed"
    if aligned.startswith("start_unverified"):
        print("警告: 起点未验证；依照清单确认按第 0 帧对齐。", file=sys.stderr)
    temp_audio = temp_report = None
    published_audio = False
    try:
        with contextlib.ExitStack() as stack:
            handles = [stack.enter_context(p.open("rb")) for p in paths]
            for p, item, f in zip(paths, inputs, handles):
                ensure_unchanged(p, item["snapshot"], f)
                f.seek(item["data_offset"])
            fd, temp_audio = tempfile.mkstemp(prefix=".polywav-", suffix=".tmp", dir=output.parent)
            out = stack.enter_context(os.fdopen(fd, "wb"))
            out.write(make_header(inputs[0], len(inputs), container, data_size, total_size))
            hashes = [hashlib.sha256() for _ in inputs]
            b = inputs[0]["sample_bytes"]
            frames = inputs[0]["frames"]
            start_time, last = time.monotonic(), 0
            for start in range(0, frames, BLOCK_FRAMES):
                count = min(BLOCK_FRAMES, frames - start)
                if len(inputs) == 1:
                    block = exact(handles[0], count * b)
                    hashes[0].update(block)
                else:
                    block = bytearray(count * len(inputs) * b)
                    for ch, (f, h) in enumerate(zip(handles, hashes)):
                        samples = exact(f, count * b)
                        h.update(samples)
                        for byte in range(b):
                            block[ch * b + byte::len(inputs) * b] = samples[byte::b]
                out.write(block)
                last = progress("写入", start + count, frames, start_time, last)
            if not frames:
                progress("写入", 0, 0, start_time, 0)
            if data_size & 1:
                out.write(b"\0")
            out.flush()
            os.fsync(out.fileno())
            if out.tell() != total_size:
                raise PolyError("输出大小不符合预期")
            for p, item, f in zip(paths, inputs, handles):
                ensure_unchanged(p, item["snapshot"], f)
            hashes = [h.hexdigest() for h in hashes]
            verify(temp_audio, inputs, hashes)
            for p, item, f in zip(paths, inputs, handles):
                ensure_unchanged(p, item["snapshot"], f)
            report = {"version": VERSION, "schema_version": 1, "output": output.name,
                      "container": container, "frames": frames, "format": {k: inputs[0][k] for k in ("rate", "encoding", "bits", "valid_bits")},
                      "alignment": aligned, "embedded_timecode_preserved": False,
                      "metadata": "输入元数据未复制；输出不包含 BWF、iXML、对象坐标或工程数据。",
                      "validation": {"status": "passed", "method": "independent output parser and full per-channel SHA-256",
                                     "external_reader": "未执行；需使用独立读取器和目标软件验证"},
                      "channels": [{"output_channel": i + 1, "input": os.path.relpath(str(p), str(output.parent.resolve())),
                                    "time_reference": item["time_reference"], "sha256": hashes[i]}
                                   for i, (p, item) in enumerate(zip(paths, inputs))]}
            fd, temp_report = tempfile.mkstemp(prefix=".polywav-", suffix=".json.tmp", dir=output.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as report_file:
                json.dump(report, report_file, ensure_ascii=False, indent=2)
                report_file.write("\n")
                report_file.flush()
                os.fsync(report_file.fileno())
            # Report first: the final WAV name is visible only once its verified report exists.
            publish(temp_report, report_path)
            try:
                publish(temp_audio, output)
                published_audio = True
            except BaseException:
                if report_path.exists() and os.path.samefile(temp_report, report_path):
                    report_path.unlink()
                raise
    finally:
        for name in (temp_audio, temp_report):
            if name:
                try:
                    os.unlink(name)
                except FileNotFoundError:
                    pass
    if published_audio:
        print(f"完成并校验通过: {output}\n报告: {report_path}")


def inspect(directory):
    paths = sorted((p for p in Path(directory).iterdir() if p.is_file() and p.suffix.lower() == ".wav"), key=lambda p: natural_key(p.name))
    results = []
    failed = False
    for path in paths:
        try:
            item = parse_input(path)
            n, segment = candidate(path.name)
            results.append({"file": path.name, "candidate_channel": n, "candidate_segment": segment,
                            "rate": item["rate"], "encoding": item["encoding"], "bits": item["bits"],
                            "valid_bits": item["valid_bits"], "frames": item["frames"],
                            "duration_seconds": item["frames"] / item["rate"], "time_reference": item["time_reference"]})
        except (PolyError, OSError) as exc:
            results.append({"file": path.name, "error": str(exc)})
            failed = True
    warnings = []
    numbers = [x["candidate_channel"] for x in results if x.get("candidate_channel") is not None]
    duplicates = sorted({n for n in numbers if numbers.count(n) > 1})
    if duplicates:
        warnings.append(f"重复候选通道号 {duplicates}；可能包含多个 take 或分段，请先完成分段整理。")
    if numbers:
        missing = sorted(set(range(1, max(numbers) + 1)) - set(numbers)) if max(numbers) <= 128 else []
        if missing:
            warnings.append(f"候选通道号缺号: {missing}")
        if max(numbers) > 128:
            warnings.append("候选通道号超过 128，请检查文件命名。")
    if any(x.get("candidate_segment") is not None for x in results):
        warnings.append("文件名含疑似分段编号；请确认没有混入其他 take 或分段。")
    if len(numbers) != len(results):
        warnings.append("部分文件的候选通道号无法唯一确定。")
    print(json.dumps({"files": results, "warnings": warnings,
                      "notice": "候选编号不是通道语义保证；请核对 take、分段与映射。起点未验证。"},
                     ensure_ascii=False, indent=2))
    return 1 if failed else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="离线无损 mono WAV 交织工具")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect_parser = sub.add_parser("inspect", help="扫描当前目录层的 WAV 文件")
    inspect_parser.add_argument("directory")
    for command in ("merge", "verify"):
        p = sub.add_parser(command)
        p.add_argument("--manifest", required=True)
        p.add_argument("--output", required=True)
        if command == "merge":
            p.add_argument("--container", choices=("auto", "rf64"), default="auto")
            p.add_argument("--verify", action="store_true", help="兼容选项；校验始终强制执行")
    args = parser.parse_args(argv)
    try:
        if args.command == "inspect":
            return inspect(args.directory)
        if args.command == "merge":
            merge(args.manifest, args.output, args.container)
        else:
            _, paths = load_manifest(args.manifest)
            inputs = preflight(paths)
            with contextlib.ExitStack() as stack:
                handles = [stack.enter_context(p.open("rb")) for p in paths]
                for p, item, f in zip(paths, inputs, handles):
                    ensure_unchanged(p, item["snapshot"], f)
                hashes = hash_inputs(inputs, handles)
                verify(args.output, inputs, hashes)
                for p, item, f in zip(paths, inputs, handles):
                    ensure_unchanged(p, item["snapshot"], f)
            print("逐通道 SHA-256 校验通过")
        return 0
    except (PolyError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"错误: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n已取消；本次临时文件已清理。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
