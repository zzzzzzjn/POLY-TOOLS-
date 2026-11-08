"""Independent strict parser for PolyWAV output, separate from the input parser."""

import os
import struct


def read_output(path):
    def read(f, n):
        value = f.read(n)
        if len(value) != n:
            raise ValueError("输出文件截断")
        return value

    with open(path, "rb") as f:
        magic, size, wave = struct.unpack("<4sI4s", read(f, 12))
        if magic not in (b"RIFF", b"RF64") or wave != b"WAVE":
            raise ValueError("输出不是 RIFF/RF64 WAVE")
        data64 = frames64 = None
        if magic == b"RF64":
            if size != 0xFFFFFFFF or read(f, 8) != b"ds64\x1c\x00\x00\x00":
                raise ValueError("输出 RF64 ds64 无效")
            size, data64, frames64, table = struct.unpack("<QQQI", read(f, 28))
            if table:
                raise ValueError("不支持输出 ds64 表")
        end = size + 8
        if end != os.fstat(f.fileno()).st_size:
            raise ValueError("输出容器大小不匹配")
        fmt = data = fact = None
        while f.tell() < end:
            if end - f.tell() < 8:
                raise ValueError("输出 chunk 头不完整")
            name, length = struct.unpack("<4sI", read(f, 8))
            if name == b"data" and magic == b"RF64":
                if length != 0xFFFFFFFF:
                    raise ValueError("输出 RF64 data 缺少哨兵")
                length = data64
            pos = f.tell()
            if pos + length + (length & 1) > end:
                raise ValueError("输出 chunk 超出文件边界")
            if name == b"fmt ":
                if fmt is not None or length != 40:
                    raise ValueError("输出 fmt 长度或数量无效")
                raw = read(f, 40)
                tag, channels, rate, byte_rate, align, bits, cb, valid, mask = struct.unpack_from("<HHIIHHHHI", raw)
                subtype = raw[24:40]
                pcm = bytes.fromhex("0100000000001000800000aa00389b71")
                floating = bytes.fromhex("0300000000001000800000aa00389b71")
                if tag != 0xFFFE or cb != 22 or mask != 0 or subtype not in (pcm, floating):
                    raise ValueError("输出 extensible fmt 无效")
                if not 1 <= channels <= 128 or bits not in (16, 24, 32) or not 0 < valid <= bits:
                    raise ValueError("输出通道数或位数无效")
                if subtype == floating and (bits != 32 or valid != 32):
                    raise ValueError("输出 float 格式无效")
                if not rate or align != channels * (bits // 8) or byte_rate != rate * align:
                    raise ValueError("输出块对齐或字节率无效")
                fmt = (channels, rate, "pcm" if subtype == pcm else "float", bits, valid, align)
            elif name == b"fact":
                if fact is not None or length != 4:
                    raise ValueError("输出 fact 无效")
                fact = struct.unpack("<I", read(f, 4))[0]
            elif name == b"data":
                if data is not None:
                    raise ValueError("输出含多个 data chunk")
                data = (pos, length)
            f.seek(pos + length + (length & 1))
        if fmt is None or data is None or data[1] % fmt[5]:
            raise ValueError("输出缺少有效的 fmt 或 data")
        frames = data[1] // fmt[5]
        if magic == b"RF64" and frames64 != frames:
            raise ValueError("输出 ds64 帧数不符")
        if fmt[2] == "float" and fact != min(frames, 0xFFFFFFFF):
            raise ValueError("输出 float fact 帧数不符")
        return {"container": magic.decode(), "channels": fmt[0], "rate": fmt[1],
                "encoding": fmt[2], "bits": fmt[3], "valid_bits": fmt[4],
                "frames": frames, "data_offset": data[0], "data_size": data[1]}
