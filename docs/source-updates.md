# Update your CasaOS fork directly from main

This workflow installs the customized **CasaOS core, UI and app-management service** from the `main` branches in `filippov-au/CasaOS-UI` and `filippov-au/CasaOS-AppManagement`. It builds the core and obtains this updater from `filippov-au/CasaOS`. It does not require GitHub Releases and does not rebuild the gateway, user, storage, or message-bus services or upgrade Debian.

The updater records exact installed commit IDs. It runs the component tests and builds before replacing files, saves the previous components, and restores them if the core or app-management fails to start. It preserves app containers, app data, existing CasaOS configuration, and app-update history.

## First installation on mediastation

For your Debian 13 x86-64 mini-PC with Docker 28.5.2, run as root:

```sh
apt-get update
apt-get install -y git python3 ca-certificates
git clone --depth 1 --branch main https://github.com/filippov-au/CasaOS.git /opt/casaos-source/CasaOS
python3 /opt/casaos-source/CasaOS/scripts/casaos-source-update.py update
```

After this one-time installation, use **Settings → Update → Upgrade Now** in CasaOS. The existing update indicator checks your three `main` branches when the dashboard loads, when Settings opens, and every five minutes while the dashboard is open. The server caches checks for 30 seconds. The dialog lists installed and remote commit IDs and streams the normal upgrade log. A failed GitHub check is reported separately from “up to date” and does not block login.

If the previous command-line updater is already installed, run `casaos-source-update update` once to install this integration. Thereafter the same built-in button follows your fork; no release is needed.

The existing installation must be CasaOS 0.4.x, managed by systemd, with the usual `/usr/bin/casaos`, `/usr/bin/casaos-app-management`, and `/var/lib/casaos/www` paths. The updater checks these conditions before installation. If the clone directory already exists, use the updater there; do not clone over local work.

The first build downloads Go and Node build images and dependencies, so it takes longer and needs several GB of free disk space. Go, Node, and pnpm run inside disposable Docker containers; their caches remain in named Docker volumes for future builds. The build containers do not receive the Docker socket. Application containers keep running during the build.

Reload the browser after installation. The updater is installed as `/usr/local/sbin/casaos-source-update`.

## Subsequent updates

```sh
casaos-source-update check
casaos-source-update update
```

`check` compares installed commits with your GitHub `main` branches without changing the installation. `update` fetches those branches, builds and tests them, and then installs them. If the same commits are already installed, it exits without rebuilding. Use `update --force` to rebuild the same commits or `update --yes` for unattended confirmation.

To compile and test without changing installed components:

```sh
casaos-source-update update --build-only
```

This leaves prepared files under the printed `/var/lib/casaos/fork-updates/build-.../root` directory. Build-only mode still downloads sources, images, and dependencies. It does not stop services or install files.

The updater refuses to overwrite local changes in its source checkouts. These checkouts are managed using fetched commits; use separate clones for development.

## Roll back a system-component update

Every installation prints its backup directory and an exact rollback command:

```sh
casaos-source-update rollback --backup /var/lib/casaos/fork-updates/backups/<printed-backup-id>
```

Rollback restores the previous core, UI, app-management binaries, updater, and any managed Docker compatibility setting. It keeps current app data. A backup of the current components is also saved before a manual rollback. Backups are retained until you explicitly remove them.

This is separate from the App Store's per-app rollback. Neither operation reverses application database migrations.

For Docker daemons requiring API 1.44, the updater installs a service-specific compatibility setting, matching the tested Docker 29 configuration. Docker 28.5.2 does not normally need it. The setting is included in backups.

The built-in CasaOS **system** update button follows the fork when the source updater is installed. It keeps the existing `/v1/sys/version` and `/v1/sys/update` APIs and `/var/log/casaos/upgrade.log` dialog contract. A fixed `casaos-source-update.service` runs the build independently of the core service, so closing the browser or restarting CasaOS does not interrupt it. Duplicate web starts and an already-running terminal updater are rejected; the child updater retains its exclusive installation lock.

Official installations without the source updater retain the original version check and `UpdateUrl` behavior. No configurable URL is accepted from the browser. The App Store **Updates** tab continues to manage installed applications separately.

The first upgrade to this integration also starts backing up the core binary. Older backups do not contain it and leave that binary untouched when restored. Normal component rollback does not reverse databases or Debian changes.

## Implementation and tests

The standalone updater is `scripts/casaos-source-update.py`. Its system paths are deliberately restricted; symlinked custom installations require adapting the updater rather than silently replacing other directories.

```sh
python3 -m unittest discover -s tests -p 'test_source_update.py' -v
```

Tests simulate installation and service failure in temporary directories, including core recovery, data preservation, log completion markers, legacy backups, local Git changes, and unsafe backup manifests. Go tests cover commit checks, offline login, the existing update API contract, and duplicate starts. They do not change the machine's installed CasaOS.
