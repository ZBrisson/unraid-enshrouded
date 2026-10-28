<img src="icon.png" width="96" align="right" alt="">

# unraid-enshrouded

An Unraid template for an **Enshrouded** dedicated server, built on [mornedhels/enshrouded-server](https://github.com/mornedhels/enshrouded-server). The image is used as-is; this repo adds Unraid defaults and CI that checks the template against the image, Steam and a real server boot.

> **Image tags (2026-10-28):** the template uses upstream's floating `stable-proton`/`stable-wine` tags (release 1.8.1). From 2026-10-16 to 2026-10-28 it pinned upstream `dev-*` builds by digest, because 1.7.2 couldn't start Enshrouded 1.0 ([#254](https://github.com/mornedhels/enshrouded-server/issues/254)) and its backups skipped `world0.ksd` ([#256](https://github.com/mornedhels/enshrouded-server/pull/256)); 1.8.0 fixed both. CI's `upstream-release` job fails when a newer release appears, so it gets a smoke run before you rely on it.

Why this image: it updates the game on a schedule but skips the update while anyone is online, writes zip backups of the world with retention, restarts on a schedule, and maps every `enshrouded_server.json` setting (including server roles) to an environment variable.

## What the template sets

| | Value | Why |
|---|---|---|
| Image | `mornedhels/enshrouded-server:stable-proton` (or `stable-wine`, picked at install) | latest upstream release; see the note above |
| Port | `15637/udp`, same on host and container | the server advertises its query port |
| `/opt/enshrouded` | `/mnt/user/appdata/enshrouded` | game, config (role passwords) and world save |
| `/backups` | `/mnt/user/backup/enshrouded` | world zips, off the cache pool |
| `PUID`/`PGID` | `99`/`100` | Unraid `nobody:users` |
| `--stop-timeout=90` | | the server's own shutdown waits up to 90 s to save; Docker's default is 10 s |
| `--memory=16g` | | keeps a runaway server from starving the host |
| `--cap-drop=ALL` + 5 caps, `no-new-privileges` | | upstream's [hardening](https://github.com/mornedhels/enshrouded-server/blob/main/docs/SECURITY_HARDENING.md) set |
| Roles | Admin (everything, 1 reserved slot), Friends (build, no kick/ban) | `SERVER_PASSWORD` is deprecated; Enshrouded uses roles |
| Schedules | update check every 30 min, backup every 6 h (keep 28), restart 05:00 | updates and restarts only when nobody is online |

## Install

On the Unraid server, as root:

```bash
mkdir -p /mnt/user/appdata/enshrouded /mnt/user/backup/enshrouded
chown 99:100 /mnt/user/appdata/enshrouded /mnt/user/backup/enshrouded
wget -O /boot/config/plugins/dockerMan/templates-user/my-enshrouded.xml \
  https://raw.githubusercontent.com/ZBrisson/unraid-enshrouded/main/my-enshrouded.xml
```

Then **Docker → Add Container → Template: enshrouded**, set the two role passwords, and **Apply**. This is the one web UI step: a container started with `docker run` works, but Unraid's Docker page can't edit or update it.

Forward **UDP 15637** on the router. The first start downloads the game; follow it with `docker logs -f enshrouded` until the server is listed.

After the first start, the image restarts the server once at the next update check (`:00` or `:30`). It couldn't record the installed version on the first run, so it re-validates the install once to record it.

A healthy start ends with `[online] Server connected to Steam successfully` and `[Session] 'HostOnline' (up)!`. To check which game build is installed, read the `Game Version (SVN)` log line or `buildid` in `server/steamapps/appmanifest_2278520.acf`; the version the server reports to the server browser stays `0.0.15.0`.

### Changing the image on an existing container

**Docker → enshrouded → Edit**, change **Repository** (copy it from the current `my-enshrouded.xml` in this repo), **Apply**. The game files, save and passwords stay. Don't `wget` the template again: after the first Apply, `templates-user/my-enshrouded.xml` holds your passwords and settings, and a fresh copy would wipe them.

### Don't run `enshrouded-force-update` on a running container

It runs `rm -rf server/steamapps`, which also deletes the Proton prefix (`steamapps/compatdata/2278520/pfx`). If the server or the updater is running at the time, the server crash-loops with `wine: chdir to …/pfx : No such file or directory`, and the updater's retry can delete the prefix again. To force a clean re-validate:

```bash
docker stop enshrouded
rm -rf /mnt/user/appdata/enshrouded/server/steamapps/compatdata   # Proton prefix only; the save is in server/savegame
docker start enshrouded
```

On boot the updater logs `Proton files are not available`, verifies the game files (about a minute, no re-download) and starts the server.

### ntsync

Not available: Unraid 7.3.2 (kernel `6.18.38-Unraid`) doesn't ship the `ntsync` module (`modprobe ntsync` → *Module ntsync not found*). Proton falls back to fsync (`fsync: up and running` in the log). If a later Unraid ships it, check `modprobe ntsync && ls -l /dev/ntsync`, load it from `/boot/config/go` and add `--device=/dev/ntsync` to **Extra Parameters**.

### Time zone

Unraid passes `TZ` to every container, and the image honours it: the log's timestamps are local time, so `RESTART_CRON=0 5 * * *` means 05:00 local. The smoke test checks this too.

## Verified

| Where | Image | Result |
|---|---|---|
| Unraid 7.3.2 (kernel 6.18.38, Ryzen 7 3700X, 32 GB), 2026-10-16 | `dev-proton@sha256:d122801b…` | build 25988414 (= steamcmd); `HostOnline`; joined from the game client; backup zip written; container clock EDT; config 99:100 mode 600; idle 82/2048 pids, ~880 MiB |
| GitHub runner (smoke), 2026-10-16 | `dev-proton`, `dev-wine` (pinned) | server answers on 15637 after 104 s (proton, first start including the 8.1 GB download); config matches the template; `docker stop` in 14 s, exit 0, save written; backup zip contains `world0.ksd` |
| Unraid, 2026-10-16 | `stable-proton` 1.7.2 | **fails**: restart loop on `Steamworks Version Mismatch / No SteamUtils011` |

## Backups and your other backup jobs

- **Appdata share** (cache, mover array → cache) keeps the game on the SSD.
- **In-container backups** are the primary save backup: a zip of the world every 6 hours to `/mnt/user/backup/enshrouded`, newest 28 kept. Restore by stopping the container and unzipping into `appdata/enshrouded/server/savegame/`.
- **Duplicati**: add `/mnt/user/backup/enshrouded/` to the selected sources for an off-site copy. `enshrouded_server.json` holds the role passwords in plain text, so keep Duplicati's encryption on if you also back up appdata.
- **Appdata Backup plugin**: it stops the container, archives `appdata/enshrouded`, and starts it again. Most of that folder is the game itself, which Steam re-downloads. After the first start, run `du -sh /mnt/user/appdata/enshrouded/server/*` and add the large game folders to this container's exclusions, keeping `enshrouded_server.json` and `savegame/`. After the plugin's first run, check the container log for `Shutdown complete` to confirm it stopped cleanly.

## CI

| Workflow | When | Checks |
|---|---|---|
| `ci` / static | every push and PR, weekly | XML well-formed; Unraid template rules (`tools/template.py validate`); image-specific rules (`tests/test_template.py`); pyflakes, shellcheck, actionlint |
| `ci` / image | same | every `<Branch>` image pulls, and every variable the template sets is read by that image's scripts (catches upstream renames) |
| `ci` / upstream-release | same | fails once upstream publishes a release newer than the last one smoke-tested (`KNOWN_RELEASE`, 1.8.1) |
| `ci` / steam | same | steamcmd logs in anonymously and `GAME_BRANCH` has a build |
| `ci` / template-urls | pushes to main | `TemplateURL` and `Icon` resolve |
| `smoke` | PRs touching the template or tests, weekly, manual | boots each `<Branch>` image from the rendered template (same arguments as Unraid, including `--pids-limit 2048`): server answers A2S with the template's name and slots; `enshrouded_server.json` matches the template; files are 99:100; installed build = steamcmd's; `docker stop` finishes inside 90 s with a save; restart reuses the install; a backup zip with the save lands in `/backups` |

Some rules come from reading the image's scripts and Unraid's `xmlToCommand()`:

- **Typed values can't be empty.** The image passes the slot count, query port, chat flags and role permissions to `jq --argjson`. If one is empty, jq fails and the script writes an **empty** `enshrouded_server.json`. Unraid passes empty variables (`-e X=''`), so the tests require a typed value for each of these.
- **Dropdowns need a stored value.** With no value, Unraid passes the `Default` text, which for a dropdown is the literal `true|false`.
- **The pids limit is mirrored.** Unraid adds `--pids-limit 2048` unless Extra Parameters sets one, so the smoke test runs with it and fails if the server comes close to it.

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
