#!/usr/bin/env python3
"""Check the enshrouded_server.json the image generated against the template's variables.

  check_config.py my-enshrouded.xml enshrouded_server.json [TARGET=VALUE ...]

The TARGET=VALUE pairs are the same overrides the smoke test passed to `template.py render`.
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import template as tpl  # noqa: E402

ROLE_KEYS = {"NAME": "name", "PASSWORD": "password", "CAN_KICK_BAN": "canKickBan",
             "CAN_ACCESS_INVENTORIES": "canAccessInventories", "CAN_EDIT_WORLD": "canEditWorld",
             "CAN_EDIT_BASE": "canEditBase", "CAN_EXTEND_BASE": "canExtendBase",
             "RESERVED_SLOTS": "reservedSlots"}
TOP_KEYS = {"SERVER_NAME": "name", "SERVER_SLOT_COUNT": "slotCount", "SERVER_QUERYPORT": "queryPort",
            "SERVER_ENABLE_VOICE_CHAT": "enableVoiceChat", "SERVER_VOICE_CHAT_MODE": "voiceChatMode",
            "SERVER_ENABLE_TEXT_CHAT": "enableTextChat", "SERVER_GS_PRESET": "gameSettingsPreset"}


def typed(value):
    if value in ("true", "false"):
        return value == "true"
    return int(value) if value.isdigit() else value


def main():
    template, config_path, *sets = sys.argv[1:]
    env = tpl.Template(template).variables()
    env.update(s.split("=", 1) for s in sets)
    config = json.loads(Path(config_path).read_text())  # an empty file (jq failure) fails here
    problems = []
    for var, key in TOP_KEYS.items():
        if var in env and config.get(key) != typed(env[var]):
            problems.append(f"{key}: {config.get(key)!r} != {var}={env[var]!r}")
    groups = config.get("userGroups", [])
    for var, value in env.items():
        m = re.fullmatch(r"SERVER_ROLE_(\d+)_(\w+)", var)
        if not m:
            continue
        idx, key = int(m.group(1)), ROLE_KEYS[m.group(2)]
        got = groups[idx].get(key) if idx < len(groups) else None
        if got != typed(value):
            shown = "***" if key == "password" else repr(got)
            problems.append(f"userGroups[{idx}].{key}: {shown} != {var}")
    for p in problems:
        print(f"config mismatch: {p}", file=sys.stderr)
    print(f"check_config: {'FAIL' if problems else 'ok'} ({len(groups)} roles)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
