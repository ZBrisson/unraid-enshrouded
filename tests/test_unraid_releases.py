"""Offline checks for tools/unraid_releases.py, using lines quoted from Unraid's release notes."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import unraid_releases as ur  # noqa: E402


@pytest.mark.parametrize("notes, want", [
    ("- docker: version 19.03.14\n", "19.03.14"),                                  # 6.9.0
    ("* docker: version 29.4.3-1_LT -> 29.5.1-1_LT\n", "29.5.1"),                  # 7.3.1
    ("* docker: version 29.5.2 -> 29.5.3\n", "29.5.3"),                            # 7.3.2
    ("- We reverted **docker** from v23.0.6, introduced during Unraid OS 6.12 development,"
     " to v20.10.24, which is the latest patch release", "20.10.24"),               # 6.12.2
    ("* Improvement: Docker engine updated to 29.5.1.\n", None),                    # prose only
    ("Docker containers support multiple custom networks.\n", None),               # 7.4.0
])
def test_docker_version(notes, want):
    assert ur.docker_version(notes) == want


NOTES = {
    "6.12.0.md": "- docker: version 23.0.6\n",
    "6.12.2.md": "- We reverted **docker** from v23.0.6, blah, to v20.10.24, which is",
    "6.12.8.md": "- docker: version 24.0.9\n",
    "6.12.15.md": "nothing about the engine\n",
    "7.3.3.md": "* docker: version 29.5.3-1_LT -> 29.8.2-1_LT\n",
    "7.4.1.md": "no engine change\n",
    "7.10.0.md": "no engine change\n",
    "5.0.6.md": "- docker: version 1.0.0\n",
    "index.md": "",
    "7.4.0-rc.1.md": "* docker: version 99.0.0\n",
}


def test_expected_targets_carries_docker_forward_and_sorts_numerically():
    got = ur.expected_targets(list(NOTES), NOTES.__getitem__)
    assert got == [("6.12.15", "24.0.9"), ("7.10.0", "29.8.2")]


def test_repo_targets_cover_two_majors():
    targets = json.loads((ROOT / ".github/unraid.json").read_text())["targets"]
    majors = [t["unraid"].split(".")[0] for t in targets]
    assert len(set(majors)) == len(majors) == 2
    for t in targets:
        assert set(t) == {"name", "unraid", "docker"}
        assert all(p.isdigit() for p in t["unraid"].split(".") + t["docker"].split("."))
