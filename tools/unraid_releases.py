#!/usr/bin/env python3
"""Check .github/unraid.json against Unraid's published release notes.

  unraid_releases.py check .github/unraid.json

The policy (see the file's "policy"): test the newest stable release of the two newest Unraid
major versions, each on the Docker Engine it ships. Release notes come from the unraid/docs
repo (docs/unraid-os/release-notes/<version>.md). A release's Docker version is the last
"docker: version ..." change in the notes of that major series up to and including it, because
the notes only mention Docker when it changes.

Exit 1, with a ::error line per problem, when a target is missing, outdated, or names a Docker
version the notes contradict. Set GITHUB_TOKEN (or GH_TOKEN) to avoid API rate limits.
"""
import argparse
import json
import os
import re
import sys
import urllib.request

LIST_URL = "https://api.github.com/repos/unraid/docs/contents/docs/unraid-os/release-notes"
RAW_URL = "https://raw.githubusercontent.com/unraid/docs/main/docs/unraid-os/release-notes/{}"
STABLE_FILE = re.compile(r"^(\d+)\.(\d+)\.(\d+)\.mdx?$")
DOCKER_LINES = [
    # "- docker: version 24.0.9", "* docker: version 29.5.3-1_LT -> 29.8.2-1_LT"
    re.compile(r"^\s*[-*]\s*docker:\s*version\s+(?:\S+\s*->\s*)?v?(\d+\.\d+\.\d+)", re.I | re.M),
    # 6.12.2: "We reverted **docker** from v23.0.6, ... to v20.10.24, ..."
    re.compile(r"reverted \*\*docker\*\* from v[\d.]+.*?\bto v(\d+\.\d+\.\d+)", re.I | re.S),
]


def vkey(version):
    return tuple(int(p) for p in version.split("."))


def docker_version(notes):
    """The Docker version a release-notes page changes to, or None if it doesn't mention one."""
    hits = [(m.start(), m.group(1)) for rx in DOCKER_LINES for m in rx.finditer(notes)]
    return max(hits)[1] if hits else None


def expected_targets(files, fetch):
    """[(unraid version, docker version or None)] for the two newest majors, newest major last."""
    versions = sorted(((".".join(m.groups()), f) for f in files if (m := STABLE_FILE.match(f))),
                      key=lambda vf: vkey(vf[0]))
    by_major = {}
    for v, f in versions:
        by_major.setdefault(vkey(v)[0], []).append((v, f))
    out = []
    for major in sorted(by_major)[-2:]:
        docker = None
        for _, f in by_major[major]:
            docker = docker_version(fetch(f)) or docker
        out.append((by_major[major][-1][0], docker))
    return out


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "unraid-template-ci"})
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def check(path):
    targets = json.loads(open(path, encoding="utf-8").read())["targets"]
    files = [e["name"] for e in json.loads(_get(LIST_URL))]
    want = expected_targets(files, lambda f: _get(RAW_URL.format(f)))
    have = {t["unraid"].split(".")[0]: t for t in targets}
    errors = []
    for unraid, docker in want:
        shown = docker or "not stated in the notes; read it on a host with docker version"
        print(f"Unraid {unraid}: Docker {shown}")
        t = have.pop(unraid.split(".")[0], None)
        if t is None:
            errors.append(f"no target for Unraid {unraid.split('.')[0]}.x; add {unraid} (Docker {shown})")
        elif t["unraid"] != unraid:
            errors.append(f"{t['name']} tests Unraid {t['unraid']}, newest is {unraid} (Docker {shown})")
        elif docker and t["docker"] != docker:
            errors.append(f"{t['name']}: Unraid {unraid}'s notes say Docker {docker}, not {t['docker']}")
    for t in have.values():
        errors.append(f"{t['name']} (Unraid {t['unraid']}) is outside the two newest majors; drop it")
    for e in errors:
        print(f"::error::{e}  ({path})")
    return 1 if errors else 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").add_argument("file")
    a = ap.parse_args()
    return check(a.file)


if __name__ == "__main__":
    sys.exit(main())
