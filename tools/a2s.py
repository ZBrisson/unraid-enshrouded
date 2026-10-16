#!/usr/bin/env python3
"""Minimal Steam A2S_INFO client (stdlib only), the same query the server browser sends.

  a2s.py HOST PORT [--wait SECONDS]   prints the reply as JSON; exit 1 if no reply
"""
import argparse
import json
import socket
import struct
import sys
import time

HEADER = b"\xff\xff\xff\xff"
INFO_REQUEST = HEADER + b"TSource Engine Query\x00"


def _cstring(buf, pos):
    end = buf.index(b"\x00", pos)
    return buf[pos:end].decode("utf-8", "replace"), end + 1


def info(host, port, timeout=3.0):
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(INFO_REQUEST, (host, port))
        data, _ = s.recvfrom(4096)
        if data[4:5] == b"A":  # challenge: resend with it appended
            s.sendto(INFO_REQUEST + data[5:9], (host, port))
            data, _ = s.recvfrom(4096)
    if data[:4] != HEADER or data[4:5] != b"I":
        raise ValueError(f"unexpected A2S reply: {data[:8]!r}")
    pos = 6  # header, 'I', protocol byte
    out = {}
    for key in ("name", "map", "folder", "game"):
        out[key], pos = _cstring(data, pos)
    out["app_id"], out["players"], out["max_players"], out["bots"] = struct.unpack_from("<hBBB", data, pos)
    pos += 5
    out["server_type"], out["environment"] = chr(data[pos]), chr(data[pos + 1])
    out["password"], out["vac"] = bool(data[pos + 2]), bool(data[pos + 3])
    out["version"], _ = _cstring(data, pos + 4)
    return out


def wait_for(host, port, seconds, interval=10):
    deadline = time.monotonic() + seconds
    while True:
        try:
            return info(host, port)
        except (OSError, ValueError):
            if time.monotonic() >= deadline:
                return None
            time.sleep(interval)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("port", type=int)
    ap.add_argument("--wait", type=int, default=0, help="keep retrying for this many seconds")
    a = ap.parse_args()
    reply = wait_for(a.host, a.port, a.wait)
    if reply is None:
        print(f"no A2S reply from {a.host}:{a.port}", file=sys.stderr)
        return 1
    print(json.dumps(reply))
    return 0


if __name__ == "__main__":
    sys.exit(main())
