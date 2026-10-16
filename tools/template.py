#!/usr/bin/env python3
"""Validate an Unraid Docker template and render it the way Unraid's dockerMan does.

  template.py validate my-enshrouded.xml
  template.py render   my-enshrouded.xml [--set TARGET=VALUE ...] [--format shell|lines]
  template.py envcheck my-enshrouded.xml <dir with the image's scripts>

Rendering mirrors xmlToCommand() in unraid/webgui
(emhttp/plugins/dynamix.docker.manager/include/Helpers.php):
  - a Config's value is its text, or its Default when the text is empty;
  - every Variable is passed, even when empty (-e 'X'='');
  - Paths with an empty host side are skipped;
  - TZ, HOST_OS, HOST_HOSTNAME and HOST_CONTAINERNAME are always added;
  - --pids-limit 2048 is added unless ExtraParams sets one.
"""
import argparse
import re
import shlex
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

CONFIG_TYPES = {"Path", "Port", "Variable", "Label", "Device"}
DISPLAYS = {"always", "always-hide", "advanced", "advanced-hide"}
PATH_MODES = {"rw", "ro", "rw,slave", "ro,slave", "rw,shared", "ro,shared"}
REQUIRED_FIELDS = ["Name", "Repository", "Overview", "Category", "Icon", "Project", "Support", "TemplateURL"]
SECRET_NAME = re.compile(r"PASSWORD|SECRET|TOKEN|API_KEY", re.I)
# Flags that belong in their own template fields, or that hand the container the host.
FORBIDDEN_EXTRA = re.compile(r"^--(privileged|net|network|pid=host|ipc=host)\b|docker\.sock")
DEFAULT_PIDS_LIMIT = "2048"


class Template:
    def __init__(self, path):
        self.path = Path(path)
        self.root = ET.parse(self.path).getroot()
        self.fields = {c.tag: (c.text or "").strip() for c in self.root if c.tag not in ("Config", "Branch")}
        self.branches = [(b.findtext("Tag") or "").strip() for b in self.root.findall("Branch")]
        self.configs = []
        for c in self.root.findall("Config"):
            cfg = dict(c.attrib)
            cfg["Value"] = c.text or ""
            self.configs.append(cfg)

    def by_type(self, kind):
        return [c for c in self.configs if c.get("Type") == kind]

    def variables(self):
        """Target -> value exactly as dockerMan would pass it."""
        return {c["Target"]: effective(c) for c in self.by_type("Variable")}

    def extra_params(self):
        return shlex.split(self.fields.get("ExtraParams", ""))


def effective(cfg):
    # PHP strlen(): whitespace counts as a value.
    return cfg["Value"] if len(cfg["Value"]) else cfg.get("Default", "")


def options(cfg):
    default = cfg.get("Default", "")
    return default.split("|") if "|" in default else None


def validate(t):
    """Return a list of problems; empty means the template is good."""
    errors = []
    err = errors.append

    if t.root.tag != "Container" or t.root.get("version") != "2":
        err('root element must be <Container version="2">')
    for f in REQUIRED_FIELDS:
        if not t.fields.get(f):
            err(f"<{f}> is missing or empty")

    name = t.fields.get("Name", "")
    if name and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]+", name):
        err(f"<Name> {name!r} is not a valid container name")

    repo = t.fields.get("Repository", "")
    tag = repo.rsplit(":", 1)[1] if ":" in repo.rsplit("/", 1)[-1] else ""
    if not tag or tag == "latest":
        err(f"<Repository> {repo!r} must name an explicit tag other than latest")
    if t.branches and tag not in t.branches:
        err(f"<Repository> tag {tag!r} is not one of the <Branch> tags {t.branches}")
    if any(not b for b in t.branches):
        err("<Branch> without a <Tag>")

    if t.fields.get("Privileged", "false").lower() != "false":
        err("<Privileged> must be false")
    for f in ("Icon", "Project", "Support", "TemplateURL", "Registry"):
        v = t.fields.get(f, "")
        if v and not v.startswith("https://"):
            err(f"<{f}> must be an https URL")
    url = t.fields.get("TemplateURL", "")
    if url and not url.endswith("/" + t.path.name):
        err(f"<TemplateURL> must end with the template's file name {t.path.name!r}")
    icon = t.fields.get("Icon", "")
    if icon.startswith("https://raw.githubusercontent.com/") and not (t.path.parent / icon.rsplit("/", 1)[-1]).exists():
        err(f"<Icon> points at {icon.rsplit('/', 1)[-1]!r}, which is not in the repo")
    for cat in t.fields.get("Category", "").split():
        if not re.fullmatch(r"[A-Za-z]+:([A-Za-z]+)?", cat):
            err(f"<Category> token {cat!r} must look like 'GameServers:' or 'Tools:Utilities'")

    for p in t.extra_params():
        if FORBIDDEN_EXTRA.search(p):
            err(f"<ExtraParams> must not contain {p!r}")

    seen = set()
    for c in t.configs:
        label = c.get("Name") or c.get("Target") or "?"
        kind = c.get("Type")
        if kind not in CONFIG_TYPES:
            err(f"{label}: Type {kind!r} is not one of {sorted(CONFIG_TYPES)}")
            continue
        if c.get("Display") not in DISPLAYS:
            err(f"{label}: Display {c.get('Display')!r} is not one of {sorted(DISPLAYS)}")
        for flag in ("Required", "Mask"):
            if c.get(flag) not in ("true", "false"):
                err(f"{label}: {flag} must be 'true' or 'false'")
        if not c.get("Name"):
            err(f"{c.get('Target')}: Name is empty")
        target, value = c.get("Target", ""), effective(c)
        if kind != "Device" and not target:
            err(f"{label}: Target is empty")
        key = (kind, target, c.get("Mode") if kind == "Port" else "")
        if key in seen:
            err(f"{label}: duplicate {kind} target {target!r}")
        seen.add(key)

        if kind == "Port":
            if c.get("Mode") not in ("tcp", "udp"):
                err(f"{label}: port Mode must be tcp or udp")
            for v in (target, value):
                if not (v.isdigit() and 1 <= int(v) <= 65535):
                    err(f"{label}: {v!r} is not a port number")
        elif kind == "Path":
            if c.get("Mode") not in PATH_MODES:
                err(f"{label}: path Mode {c.get('Mode')!r} is not one of {sorted(PATH_MODES)}")
            if not target.startswith("/"):
                err(f"{label}: container path must be absolute")
            if value and not value.startswith("/mnt/user/"):
                err(f"{label}: host path {value!r} should be under /mnt/user/ (let the share decide the pool)")
        elif kind == "Device":
            if not value.startswith("/dev/"):
                err(f"{label}: device must be a /dev path (an empty one breaks docker create)")

        opts = options(c)
        if opts is not None and c["Value"] not in opts:
            # dockerMan would otherwise pass the literal 'a|b|c' string.
            err(f"{label}: value {c['Value']!r} is not one of the dropdown options {opts}")
        if SECRET_NAME.search(target) and c.get("Mask") != "true":
            err(f"{label}: looks like a secret but Mask is not true")
        if c.get("Required") == "true" and not value and c.get("Mask") != "true":
            err(f"{label}: Required but has no value or Default")
        if c.get("Mask") == "true" and c["Value"]:
            err(f"{label}: masked value must be empty in the published template")
    return errors


def render(t, overrides=None, hostname="Tower", tz="Etc/UTC"):
    """docker run arguments (without 'docker run -d'), in dockerMan's order."""
    overrides = dict(overrides or {})
    args = [f"--name={t.fields['Name']}"]
    network = t.fields.get("Network", "bridge")
    if not any(p.startswith(("--net", "--network")) for p in t.extra_params()):
        args.append(f"--net={network}")
    if t.fields.get("CPUset"):
        args.append(f"--cpuset-cpus={t.fields['CPUset']}")
    if t.fields.get("Privileged", "false").lower() == "true":
        args.append("--privileged=true")
    env = [f"TZ={tz}", "HOST_OS=Unraid", f"HOST_HOSTNAME={hostname}", f"HOST_CONTAINERNAME={t.fields['Name']}"]
    labels = ["net.unraid.docker.managed=dockerman"]
    if t.fields.get("WebUI"):
        labels.append(f"net.unraid.docker.webui={t.fields['WebUI']}")
    if t.fields.get("Icon"):
        labels.append(f"net.unraid.docker.icon={t.fields['Icon']}")
    ports, volumes, devices = [], [], []
    for c in t.configs:
        kind, target = c.get("Type"), c.get("Target", "")
        value = overrides.pop(target, effective(c))
        if kind == "Path":
            if value.strip() and target.strip():
                volumes.append(f"{value}:{target}:{c.get('Mode')}")
        elif kind == "Port":
            # dockerMan also does this for macvlan/ipvlan networks; only bridge and host matter here.
            if network == "host":
                env.append(f"{c.get('Mode').upper()}_PORT_{target}={value}")
            elif network != "none":
                ports.append(f"{value}:{target}/{c.get('Mode')}")
        elif kind == "Variable":
            env.append(f"{target}={value}")
        elif kind == "Label":
            labels.append(f"{target}={value}")
        elif kind == "Device":
            devices.append(value)
    if overrides:
        raise SystemExit(f"--set target(s) not in template: {', '.join(overrides)}")
    for e in env:
        args += ["-e", e]
    for lab in labels:
        args += ["-l", lab]
    for p in ports:
        args += ["-p", p]
    for v in volumes:
        args += ["-v", v]
    for d in devices:
        args.append(f"--device={d}")
    extra = t.extra_params()
    if not any(p.startswith("--pids-limit") for p in extra):
        args += ["--pids-limit", DEFAULT_PIDS_LIMIT]
    args += extra
    args.append(t.fields["Repository"])
    args += shlex.split(t.fields.get("PostArgs", ""))
    return args


ALWAYS_KNOWN = {"TZ", "HOST_OS", "HOST_HOSTNAME", "HOST_CONTAINERNAME"}


def envcheck(t, scripts_dir):
    """Variables in the template that the image's scripts never read."""
    files = [p for p in Path(scripts_dir).rglob("*") if p.is_file()]
    if not files:
        raise SystemExit(f"envcheck: no files under {scripts_dir}")
    text = "\n".join(p.read_text(errors="replace") for p in files)
    unknown = []
    for name in t.variables():
        if name in ALWAYS_KNOWN or re.search(rf"\b{re.escape(name)}\b", text):
            continue
        role = re.fullmatch(r"SERVER_ROLE_\d+_(\w+)", name)
        if role and "SERVER_ROLE_" in text and re.search(rf"\b{role.group(1)}\)", text):
            continue  # dynamic: case "$group_param" in NAME) PASSWORD) ...
        if name.startswith("SERVER_GS_") and "SERVER_GS_" in text:
            continue  # dynamic: every SERVER_GS_* maps onto gameSettings
        unknown.append(name)
    return unknown


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate")
    v.add_argument("template")
    r = sub.add_parser("render")
    r.add_argument("template")
    r.add_argument("--set", action="append", default=[], metavar="TARGET=VALUE",
                   help="override a Config by its Target (container path, port or variable name)")
    r.add_argument("--name", help="override the container name")
    r.add_argument("--tz", default="Etc/UTC")
    r.add_argument("--format", choices=("shell", "lines"), default="shell",
                   help="shell: one copy-pasteable command; lines: one argument per line")
    e = sub.add_parser("envcheck")
    e.add_argument("template")
    e.add_argument("scripts_dir")
    g = sub.add_parser("get", help="print a field (e.g. Repository) or a Config's value by Target")
    g.add_argument("template")
    g.add_argument("key")
    a = ap.parse_args(argv)

    t = Template(a.template)
    if a.cmd == "get":
        values = {c["Target"]: effective(c) for c in t.configs}
        if a.key not in values and a.key not in t.fields:
            raise SystemExit(f"{a.key} not in template")
        print(values.get(a.key, t.fields.get(a.key)))
        return 0
    if a.cmd == "validate":
        problems = validate(t)
        for p in problems:
            print(f"{t.path}: {p}", file=sys.stderr)
        print(f"{t.path}: {'FAIL' if problems else 'ok'} ({len(t.configs)} configs)")
        return 1 if problems else 0
    if a.cmd == "envcheck":
        unknown = envcheck(t, a.scripts_dir)
        for n in unknown:
            print(f"{t.path}: {n} is not read anywhere in the image's scripts", file=sys.stderr)
        print(f"envcheck: {'FAIL' if unknown else 'ok'} ({len(t.variables())} variables)")
        return 1 if unknown else 0

    overrides = {}
    for s in a.set:
        k, sep, val = s.partition("=")
        if not sep:
            raise SystemExit(f"--set needs TARGET=VALUE, got {s!r}")
        overrides[k] = val
    if a.name:
        t.fields["Name"] = a.name
    args = render(t, overrides, tz=a.tz)
    if a.format == "lines":
        print("\n".join(args))
    else:
        print("docker run -d " + " ".join(shlex.quote(x) for x in args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
