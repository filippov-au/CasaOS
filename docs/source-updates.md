# Update your CasaOS fork directly from main

This workflow installs the customized **UI and app-management service** from the `main` branches in `filippov-au/CasaOS-UI` and `filippov-au/CasaOS-AppManagement`. It fetches `filippov-au/CasaOS` for this updater. It does not require GitHub Releases and does not rebuild unrelated CasaOS services or upgrade Debian.

The updater records exact installed commit IDs. It runs the component tests and builds before replacing files, saves the previous components, and restores them if app-management fails to start. It preserves app containers, app data, existing CasaOS configuration, and app-update history.

## First installation on mediastation

For your Debian 13 x86-64 mini-PC with Docker 28.5.2, run as root:

```sh
apt-get update
apt-get install -y git python3 ca-certificates
git clone --depth 1 --branch main https://github.com/filippov-au/CasaOS.git /opt/casaos-source/CasaOS
python3 /opt/casaos-source/CasaOS/scripts/casaos-source-update.py update
```

The existing installation must be CasaOS 0.4.x, managed by systemd, with the usual `/usr/bin/casaos-app-management` and `/var/lib/casaos/www` paths. The updater checks these conditions before installation. If the clone directory already exists, use the updater there; do not clone over local work.

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

Rollback restores the previous UI, app-management binaries, updater, and any managed Docker compatibility setting. It keeps current app data. A backup of the current components is also saved before a manual rollback. Backups are retained until you explicitly remove them.

This is separate from the App Store's per-app rollback. Neither operation reverses application database migrations.

For Docker daemons requiring API 1.44, the updater installs a service-specific compatibility setting, matching the tested Docker 29 configuration. Docker 28.5.2 does not normally need it. The setting is included in backups.

The built-in CasaOS **system** update button still uses the official update channel and may replace customized components. Use `casaos-source-update` to follow your fork. The new App Store **Updates** tab manages installed applications as usual.

## Implementation and tests

The standalone updater is `scripts/casaos-source-update.py`. Its system paths are deliberately restricted; symlinked custom installations require adapting the updater rather than silently replacing other directories.

```sh
python3 -m unittest discover -s tests -p 'test_source_update.py' -v
```

Tests simulate installation and service failure in temporary directories, including automatic recovery, version-only data preservation, local Git changes, and unsafe backup manifests. They do not change the machine's installed CasaOS.
