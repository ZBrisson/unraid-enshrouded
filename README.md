<img src="icon.png" width="96" align="right" alt="">

# unraid-enshrouded

Run a dedicated server for the game [Enshrouded](https://store.steampowered.com/app/1203620/Enshrouded/) on an [Unraid](https://unraid.net/) NAS. A dedicated server keeps your world online when you're not playing, so friends can join at any time.

This repo is an **Unraid template**: a saved container configuration that Unraid's Docker page turns into a fill-in form. The server itself is [mornedhels/enshrouded-server](https://github.com/mornedhels/enshrouded-server), used unchanged. The template adds sensible defaults for Unraid. The repo's tests boot a real server whenever the template changes.

What you get:

- **Game updates** are checked every 30 minutes and applied only while nobody is online.
- **World backups** are zipped every 6 hours to a backup share, and the newest 28 are kept.
- **A daily restart** runs at 05:00, again only when the server is empty.
- **Two access roles** are set up: **Admin** (full control) and **Friends** (build and play, can't kick or ban). Each role has its own password.

## Requirements

- Unraid 6.12 or 7.x with Docker enabled. CI tests the newest 6.x and 7.x releases on the Docker Engine each one ships (listed in [`.github/unraid.json`](.github/unraid.json)). On real hardware it has been tested on Unraid 7.3.2.
- About 16 GB of RAM and 4+ CPU cores for the server alone.
- Around 10 GB free on an SSD pool for the game files; an update briefly needs about twice that.
- The ability to forward **UDP port 15637** on your router.

## Install

1. On the Unraid server, open a terminal as root and run:

   ```bash
   mkdir -p /mnt/user/appdata/enshrouded /mnt/user/backup/enshrouded
   chown 99:100 /mnt/user/appdata/enshrouded /mnt/user/backup/enshrouded
   wget -O /boot/config/plugins/dockerMan/templates-user/my-enshrouded.xml \
     https://raw.githubusercontent.com/ZBrisson/unraid-enshrouded/main/my-enshrouded.xml
   ```

2. In the web UI, go to **Docker → Add Container** and pick **enshrouded** under Template.
3. Set the **Admin** and **Friends** role passwords (they must differ), change the server name if you like, and click **Apply**.
4. On your router, forward **UDP 15637** to the Unraid server.

The first start downloads the game (about 8 GB). Watch it with `docker logs -f enshrouded`. The server is ready when the log shows:

```
[online] Server connected to Steam successfully
[Session] 'HostOnline' (up)!
```

Players then find it by name in the game's server browser and enter one of the role passwords.

## Everyday use

**Change settings.** Go to **Docker → enshrouded → Edit**. The common settings are on the form. Click **Show more settings** to see role permissions, chat, difficulty and schedules. Schedules use the server's local time zone.

**Restore a backup.** Stop the container. Unzip the backup you want from `/mnt/user/backup/enshrouded/` into `/mnt/user/appdata/enshrouded/server/savegame/`, replacing the files there. Then start the container.

**Off-site copies.** Point your backup tool (for example Duplicati) at `/mnt/user/backup/enshrouded/`. If you also back up `appdata/enshrouded`, keep encryption on: `server/enshrouded_server.json` stores the role passwords in plain text. Most of that folder is game files that Steam can re-download, so you can exclude everything except `enshrouded_server.json` and `savegame/`.

**Switch between Proton and Wine.** The template offers two image tags: `stable-proton` (recommended) and `stable-wine` (fallback, reportedly slower). To switch, go to **Edit**, change **Repository**, and click **Apply**. The game, the save and the passwords are kept.

**Don't re-download the template** over an installed container. After the first Apply, Unraid's copy of `my-enshrouded.xml` holds your passwords and settings, and a fresh `wget` would erase them.

## Troubleshooting

**Which game version is running?** The server browser always shows `0.0.15.0`, so ignore that. The real build is the `Game Version (SVN)` line in the log, or `buildid` in `server/steamapps/appmanifest_2278520.acf`.

**The server keeps restarting with `wine: chdir to …/pfx: No such file or directory`.** The Proton files under `server/steamapps/compatdata` were deleted, usually by running the image's `enshrouded-force-update` job while the server was up. Don't run that job. To force a clean check of the game files instead:

```bash
docker stop enshrouded
rm -rf /mnt/user/appdata/enshrouded/server/steamapps/compatdata   # Proton files only; the save is in server/savegame
docker start enshrouded
```

On start, the log says `Proton files are not available`, the game files are checked (about a minute, no re-download), and the server starts.

**The game never downloads: the log says `Steamcmd needs to be online to update` or `CreateBoundSocket: failed to create socket, error … (38)`.** Docker 29.4.2's default seccomp profile blocks a socket call that the 32-bit `steamcmd` makes, so it can't reach Steam even though the network is fine. Check with `docker version --format '{{.Server.Version}}'`. Docker 29.4.3 reverts the rule, and Unraid 7.3.0 went straight from 29.3.1 to 29.4.3, so this mostly hits a Docker you installed or pinned yourself. If you can't upgrade, add `--security-opt seccomp=unconfined` to **Extra Parameters**. That removes the syscall filter for this container only; the dropped capabilities and `no-new-privileges` still apply.

**Players get a version mismatch.** The game was patched and the server hasn't updated yet. It updates at the next check once nobody is online. Restarting the container also triggers a check.

## What the template sets

| Setting | Value | Why |
|---|---|---|
| Image | `mornedhels/enshrouded-server:stable-proton` (or `stable-wine`) | latest upstream release; Unraid shows when an update is available |
| Port | `15637/udp`, same number on host and container | the server tells Steam this port, so they must match |
| `/opt/enshrouded` | `/mnt/user/appdata/enshrouded` | game files, `enshrouded_server.json` (passwords) and the world save |
| `/backups` | `/mnt/user/backup/enshrouded` | backup zips, kept off the SSD pool |
| `PUID`/`PGID` | `99`/`100` | Unraid's standard `nobody:users` owner |
| `--stop-timeout=90` | | gives the server time to save on shutdown; Docker's default is 10 s |
| `--memory=16g` | | stops a runaway server from starving the rest of the NAS |
| `--cap-drop=ALL` + 5 capabilities, `no-new-privileges` | | upstream's [hardening](https://github.com/mornedhels/enshrouded-server/blob/main/docs/SECURITY_HARDENING.md) recommendation |
| Roles | Admin (everything, 1 reserved slot), Friends (build, no kick/ban) | Enshrouded 1.0 uses roles instead of one server password |
| Schedules | update check every 30 min, backup every 6 h (keep 28), restart 05:00 | updates and restarts wait until nobody is online |

## Tested on

| Where | Image | Result |
|---|---|---|
| Unraid 7.3.2, Ryzen 7 3700X, 32 GB, 2026-10-28 | `stable-proton` (1.8.1) | `HostOnline`; game build 26083076, matching Steam's current build; no version mismatch |
| GitHub Actions (smoke test), 2026-10-28 | `stable-proton`, `stable-wine` (1.8.1) | both pass every smoke check listed under [Development](#development) |

## Development

Most changes only touch `my-enshrouded.xml`. CI checks every change:

| Workflow | When | Checks |
|---|---|---|
| `ci` / static | every push and PR, weekly | the XML is well-formed; Unraid template rules (`tools/template.py validate`); image-specific rules (`tests/test_template.py`); pyflakes, shellcheck, actionlint |
| `ci` / image | same | every image tag in the template pulls, and every variable the template sets is read by that image's scripts (catches upstream renames) |
| `ci` / steam | every push and PR, weekly | Steam's servers report a build for the game, checked from each tested Unraid's Docker Engine |
| `ci` / template-urls | pushes to main | `TemplateURL` and `Icon` resolve |
| `smoke` | PRs touching the template or tests, weekly, manual | boots each image tag on each tested Unraid's Docker Engine, with the same arguments Unraid would use, then checks: the server answers with the template's name and slots; `enshrouded_server.json` matches the template; files are owned by 99:100; the installed build matches Steam's; `docker stop` saves and exits within 90 s; a restart reuses the install; a backup zip containing the save lands in `/backups` |
| `drift` / upstream-release | weekly, manual, PRs that edit what it checks | fails when upstream publishes a release newer than `KNOWN_RELEASE` in `drift.yml`. Then run `smoke`, update [Tested on](#tested-on), and bump `KNOWN_RELEASE`. |
| `drift` / unraid-release | same as above | fails when Unraid releases something newer than a target in `.github/unraid.json`, or when a target's Docker version disagrees with Unraid's release notes. Then update the file and run `smoke`. |
| `scheduled-alerts` | after each scheduled `ci`, `smoke` or `drift` run | a failing run opens an issue (or comments on the open one); the next passing scheduled run closes it |

The tests also enforce three Unraid quirks found by reading the image's scripts and Unraid's `xmlToCommand()`:

- **Typed values can't be empty.** The image passes numbers and true/false settings to `jq --argjson`. If one is empty, `jq` fails and the server gets an **empty** `enshrouded_server.json`. Unraid passes empty variables as `-e X=''`, so every such setting needs a value.
- **Dropdowns need a stored value.** Without one, Unraid passes the dropdown's option list, such as the literal text `true|false`.
- **The process limit is mirrored.** Unraid adds `--pids-limit 2048` unless Extra Parameters sets one, so the smoke test uses the same limit.

The jobs that run containers use **the Docker Engine Unraid ships**, not the runner's, and fail if the engine doesn't match (`.github/actions/unraid-docker`). The engine is part of what's being tested: GitHub's ubuntu-26.04 image shipped Docker 29.4.2, whose seccomp profile blocks `steamcmd`, and the smoke test went red for a problem no Unraid user had. `.github/unraid.json` lists the targets: the newest release of the two newest Unraid majors, each with its Docker version. `drift / unraid-release` reads Unraid's release notes and goes red when that list is out of date. CI can't reproduce Unraid's kernel or userland, so the [Tested on](#tested-on) rows from real hardware remain the final check.

Run locally:

```bash
python3 tools/template.py validate my-enshrouded.xml
python3 -m pytest -q tests/
python3 tools/template.py render my-enshrouded.xml --set SERVER_ROLE_0_PASSWORD=x --set SERVER_ROLE_1_PASSWORD=y
RUNTIME=podman tests/steam_buildid.sh
RUNTIME=docker tests/smoke.sh        # heavy: downloads the game, needs ~16 GB RAM
```

## Credits

Image and scripts: [mornedhels/enshrouded-server](https://github.com/mornedhels/enshrouded-server) (MIT). Icon: Enshrouded artwork © Keen Games, taken from [nodiaque's template](https://github.com/nodiaque/unraid_template).
