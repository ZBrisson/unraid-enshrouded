"""Static checks: the template is a valid Unraid template and its values work with this image."""
import re
import shlex
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import steam_build  # noqa: E402
import template as tpl  # noqa: E402

TEMPLATE = ROOT / "my-enshrouded.xml"


@pytest.fixture(scope="module")
def t():
    return tpl.Template(TEMPLATE)


@pytest.fixture(scope="module")
def env(t):
    return t.variables()


def test_unraid_template_is_valid(t):
    assert tpl.validate(t) == []


RELEASE_TAG = re.compile(r"(stable-(proton|wine)|\d+\.\d+\.\d+-(proton|wine))")


def test_upstream_image_and_tag_policy(t):
    images = tpl.branch_images(t)
    assert {tpl.split_image(i["image"])[0] for i in images} == {"mornedhels/enshrouded-server"}
    # Release tags may float; anything else (dev-*) moves on every upstream push, so pin it.
    for i in images:
        _, tag, digest = tpl.split_image(i["image"])
        assert RELEASE_TAG.fullmatch(tag) or digest, f"{i['image']}: pin non-release tags by digest"
    assert sorted(i["name"].rsplit("-", 1)[-1] for i in images) == ["proton", "wine"]


def test_split_image():
    assert tpl.split_image("a/b:dev@sha256:00") == ("a/b", "dev", "sha256:00")
    assert tpl.split_image("a/b:stable-wine") == ("a/b", "stable-wine", "")
    assert tpl.split_image("reg:5000/a/b") == ("reg:5000/a/b", "", "")


# The image feeds these to `jq --argjson`. An empty or non-JSON value makes jq fail and the
# script then writes an EMPTY enshrouded_server.json (echo "$(jq ...)" > file).
ARGJSON_INT = {"SERVER_SLOT_COUNT", "SERVER_QUERYPORT"}
ARGJSON_BOOL = {"SERVER_ENABLE_VOICE_CHAT", "SERVER_ENABLE_TEXT_CHAT"}
ROLE_BOOL = re.compile(r"SERVER_ROLE_\d+_CAN_\w+")
ROLE_INT = re.compile(r"SERVER_ROLE_\d+_RESERVED_SLOTS")
# Checked by the image with == "true" / != "false"; anything else silently changes behaviour.
STRICT_BOOL = {"UPDATE_CHECK_PLAYERS", "RESTART_CHECK_PLAYERS"}


def test_jq_argjson_values_are_typed(env):
    for name, value in env.items():
        if name in ARGJSON_INT or ROLE_INT.fullmatch(name):
            assert value.isdigit(), f"{name}={value!r} must be an integer"
        if name in ARGJSON_BOOL or name in STRICT_BOOL or ROLE_BOOL.fullmatch(name):
            assert value in ("true", "false"), f"{name}={value!r} must be true or false"


def test_slots_and_reserved_slots(env):
    slots = int(env["SERVER_SLOT_COUNT"])
    assert 1 <= slots <= 16
    reserved = sum(int(v) for k, v in env.items() if ROLE_INT.fullmatch(k))
    assert reserved < slots, "reserved slots would leave no room for anyone else"


ROLE_FIELDS = {"NAME", "PASSWORD", "CAN_KICK_BAN", "CAN_ACCESS_INVENTORIES", "CAN_EDIT_WORLD",
               "CAN_EDIT_BASE", "CAN_EXTEND_BASE", "RESERVED_SLOTS"}


def test_roles_are_complete_contiguous_and_password_protected(t, env):
    roles = {}
    for name in env:
        m = re.fullmatch(r"SERVER_ROLE_(\d+)_(\w+)", name)
        if m:
            roles.setdefault(int(m.group(1)), set()).add(m.group(2))
    assert sorted(roles) == list(range(len(roles))), "role indexes must start at 0 with no gaps"
    for idx, fields in roles.items():
        assert fields == ROLE_FIELDS, f"role {idx} fields: missing {ROLE_FIELDS - fields}"
    for c in t.by_type("Variable"):
        if c["Target"].endswith("_PASSWORD"):
            assert c["Required"] == "true" and c["Mask"] == "true", c["Target"]
    names = [env[f"SERVER_ROLE_{i}_NAME"] for i in roles]
    assert len(set(names)) == len(names), "role names must differ"
    assert "SERVER_PASSWORD" not in env, "deprecated; Enshrouded ignores it"


def test_query_port_is_consistent(t, env):
    ports = t.by_type("Port")
    assert len(ports) == 1
    p = ports[0]
    assert p["Mode"] == "udp"
    assert p["Target"] == tpl.effective(p) == env["SERVER_QUERYPORT"], \
        "host port, container port and SERVER_QUERYPORT must match"


def test_paths_follow_unraid_layout(t, env):
    paths = {c["Target"]: tpl.effective(c) for c in t.by_type("Path")}
    assert paths["/opt/enshrouded"] == "/mnt/user/appdata/enshrouded"
    assert paths["/backups"].startswith("/mnt/user/backup/")
    assert env["BACKUP_DIR"] in paths, "BACKUP_DIR must be a mounted path, or backups stay in appdata"


def test_runs_as_unraid_nobody_users(env):
    assert (env["PUID"], env["PGID"]) == ("99", "100")


def test_extra_params(t):
    extra = t.extra_params()
    timeout = [int(p.split("=", 1)[1]) for p in extra if p.startswith("--stop-timeout=")]
    assert timeout and timeout[0] >= 90, "the server's own stop path waits up to 90 s; Docker's default is 10"
    assert "--cap-drop=ALL" in extra
    # Exactly the set upstream documents in docs/SECURITY_HARDENING.md.
    assert {p.split("=", 1)[1] for p in extra if p.startswith("--cap-add=")} == \
        {"CHOWN", "DAC_OVERRIDE", "SETUID", "SETGID", "KILL"}
    assert "--security-opt=no-new-privileges=true" in extra
    assert any(p.startswith("--memory=") for p in extra)


CRON_FIELD = re.compile(r"(\*|\d+(-\d+)?)(/\d+)?(,(\*|\d+(-\d+)?)(/\d+)?)*")


@pytest.mark.parametrize("name", ["UPDATE_CRON", "BACKUP_CRON", "RESTART_CRON"])
def test_cron_schedules(env, name):
    value = env[name]
    fields = value.split()
    assert len(fields) == 5 and all(CRON_FIELD.fullmatch(f) for f in fields), f"{name}={value!r}"


def test_backup_not_more_often_than_ten_minutes(env):
    minute = env["BACKUP_CRON"].split()[0]
    m = re.fullmatch(r"\*/(\d+)", minute)
    assert minute != "*" and (m is None or int(m.group(1)) >= 10)


def test_render_matches_dockerman(t):
    args = tpl.render(t, {"SERVER_ROLE_0_PASSWORD": "a b'c", "SERVER_ROLE_1_PASSWORD": "x"})
    assert args[0] == "--name=enshrouded"
    assert "--net=bridge" in args
    assert args[args.index("--pids-limit") + 1] == "2048"
    assert "15637:15637/udp" in args
    assert "/mnt/user/appdata/enshrouded:/opt/enshrouded:rw" in args
    assert "SERVER_ROLE_0_PASSWORD=a b'c" in args
    assert "HOST_OS=Unraid" in args and "TZ=Etc/UTC" in args
    assert args[-1] == t.fields["Repository"]
    # The shell form must survive a round trip through the shell.
    assert shlex.split(" ".join(shlex.quote(a) for a in args)) == args


def test_render_rejects_unknown_override(t):
    with pytest.raises(SystemExit):
        tpl.render(t, {"NOPE": "1"})


def test_validator_catches_known_mistakes(tmp_path):
    bad = TEMPLATE.read_text()
    bad = re.sub(r"(<Repository>[^:<]+):[^<]+</Repository>", r"\1:latest</Repository>", bad)
    bad = re.sub(r"<Tag>stable-wine</Tag>", r"<Tag>stable-wine@sha256:nothex</Tag>", bad)
    bad = bad.replace('Target="SERVER_ENABLE_TEXT_CHAT" Default="false|true" Mode="" Description="" Type="Variable" '
                      'Display="advanced" Required="true" Mask="false">false<',
                      'Target="SERVER_ENABLE_TEXT_CHAT" Default="false|true" Mode="" Description="" Type="Variable" '
                      'Display="advanced" Required="true" Mask="false"><')
    bad = bad.replace("/mnt/user/appdata/enshrouded<", "/mnt/cache/appdata/enshrouded<")
    f = tmp_path / "my-enshrouded.xml"
    f.write_text(bad)
    (tmp_path / "icon.png").write_bytes(b"")
    problems = "\n".join(tpl.validate(tpl.Template(f)))
    assert "explicit tag" in problems
    assert "sha256:<64 hex>" in problems
    assert "dropdown" in problems
    assert "/mnt/user/" in problems


def test_envcheck_flags_unknown_variables(t, tmp_path):
    (tmp_path / "defaults").write_text(
        "\n".join(n for n in t.variables() if not n.startswith(("SERVER_ROLE_", "SERVER_GS_")))
        + '\ngrep "^SERVER_ROLE_[0-9]+_"\n' + "\n".join(f"  {f})" for f in ROLE_FIELDS)
        + "\ncompgen | grep '^SERVER_GS_'\n")
    assert tpl.envcheck(t, tmp_path) == []
    (tmp_path / "defaults").write_text("SERVER_NAME\n")
    assert "PUID" in tpl.envcheck(t, tmp_path)


STEAMCMD_SAMPLE = '''Redirecting stderr to '/root/Steam/logs/stderr.txt'
AppID : 2278520, change number : 1/2, last change : Thu Oct 15 2026
"2278520"
{
\t"common"
\t{
\t\t"name"\t\t"Enshrouded Dedicated Server"
\t\t"gameid"\t\t"2278520"
\t}
\t"depots"
\t{
\t\t"branches"
\t\t{
\t\t\t"public"
\t\t\t{
\t\t\t\t"buildid"\t\t"25988414"
\t\t\t}
\t\t\t"experimental"
\t\t\t{
\t\t\t\t"buildid"\t\t"26000001"
\t\t\t\t"pwdrequired"\t\t"0"
\t\t\t}
\t\t}
\t}
}
'''


def test_steam_build_parser():
    assert steam_build.build_id(STEAMCMD_SAMPLE, "public") == "25988414"
    assert steam_build.build_id(STEAMCMD_SAMPLE, "experimental") == "26000001"
    assert steam_build.build_id(STEAMCMD_SAMPLE, "nope") is None
    assert steam_build.build_id("Steam>quit", "public") is None
