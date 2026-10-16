#!/usr/bin/env python3
"""Read a branch's build id from steamcmd's app_info_print output.

  docker run --rm steamcmd/steamcmd:latest +login anonymous +app_info_update 1 \
      +app_info_print 2278520 +quit | steam_build.py --branch public

Prints the build id and exits 0, or exits 1 if the app or branch is missing.
"""
import argparse
import re
import sys

APP_ID = "2278520"  # Enshrouded Dedicated Server
TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_vdf(text):
    """Parse Valve KeyValues text into nested dicts. Later duplicate keys win."""
    stack = [{}]
    key = None
    for m in TOKEN.finditer(text):
        s, brace = m.groups()
        if brace == "{":
            child = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = s
        else:
            stack[-1][key] = s
            key = None
    return stack[0]


def build_id(text, branch, app_id=APP_ID):
    # steamcmd prints log lines around the KeyValues block; start at the app's own
    # key (followed by '{', unlike the "appid" "2278520" value inside the block).
    starts = [m.start() for m in re.finditer(rf'"{re.escape(app_id)}"\s*\{{', text)]
    if not starts:
        return None
    app = parse_vdf(text[starts[-1]:]).get(app_id, {})
    return app.get("depots", {}).get("branches", {}).get(branch, {}).get("buildid")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--branch", default="public")
    ap.add_argument("--app-id", default=APP_ID)
    a = ap.parse_args()
    bid = build_id(sys.stdin.read(), a.branch, a.app_id)
    if not bid:
        print(f"app {a.app_id} branch {a.branch!r}: no buildid in steamcmd output", file=sys.stderr)
        return 1
    print(bid)
    return 0


if __name__ == "__main__":
    sys.exit(main())
