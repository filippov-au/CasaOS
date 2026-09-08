#!/usr/bin/env python3
"""Build filippov-au CasaOS main branches and install or roll back the resulting components."""
import argparse
import datetime
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

REPOSITORIES = ('CasaOS', 'CasaOS-AppManagement', 'CasaOS-UI')
SOURCE = Path('/opt/casaos-source')
GO_IMAGE = 'golang:1.21.13-bookworm'
NODE_IMAGE = 'node:20-bookworm'
RUNNING_SCRIPT = Path(__file__).read_bytes()
SERVICE = 'casaos-app-management.service'
SERVICES = ('casaos.service', SERVICE)
UPDATE_UNIT = 'casaos-source-update.service'
MANAGED = (
    'usr/bin/casaos-app-management',
    'usr/bin/appfile2compose',
    'var/lib/casaos/www',
    'var/lib/casaos/ui-message-bus.json',
    'etc/casaos/start.d/register-ui-events.sh',
    'etc/systemd/system/casaos-app-management.service.d/90-casaos-fork.conf',
    'usr/local/sbin/casaos-source-update',
    'var/lib/casaos/fork-updates/installed.json',
    'usr/bin/casaos',
    'etc/systemd/system/casaos-source-update.service',
)
STATE = 'var/lib/casaos/fork-updates'
DROPIN = MANAGED[5]


def run(*args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def regular_layout(root):
    # Swapping a symlinked directory could replace an unrelated installation or volume.
    for relative in MANAGED:
        target = root / relative
        for part in (target, *target.parents):
            if part == root:
                break
            if part.is_symlink():
                raise ValueError(f'Custom symlinked installation path is unsupported: {part}')
        if target.exists() and not (target.is_file() or target.is_dir()):
            raise ValueError(f'Unsupported installation path: {target}')


def replace_path(source, target):
    """Copy before swapping, with staging on the same filesystem as the destination."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.parent / ('.casaos-new-' + uuid.uuid4().hex)
    previous = target.parent / ('.casaos-old-' + uuid.uuid4().hex)
    moved = completed = False
    try:
        if source.is_dir():
            shutil.copytree(source, temporary)
        else:
            shutil.copy2(source, temporary)
        if target.exists():
            target.rename(previous)
            moved = True
        temporary.rename(target)
        completed = True
    except BaseException:
        if moved and not target.exists():
            previous.rename(target)
        raise
    finally:
        paths = [temporary, previous] if completed else [temporary]
        for path in paths:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()


def backup_installation(root, version):
    folder = root / STATE / 'backups' / (datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8])
    folder.mkdir(parents=True, mode=0o700)
    present = []
    for relative in MANAGED:
        source = root / relative
        if source.exists():
            destination = folder / 'files' / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, destination)
            else:
                shutil.copy2(source, destination)
            present.append(relative)
    (folder / 'backup.json').write_text(json.dumps({'format': 1, 'installing': version, 'present': present, 'managed': list(MANAGED)}, indent=2))
    return folder


def restore_files(root, backup):
    record = json.loads((backup / 'backup.json').read_text())
    if record.get('format') != 1 or not set(record['present']).issubset(MANAGED):
        raise ValueError('Invalid backup manifest')
    managed = record.get('managed', list(MANAGED[:8]))
    if not set(managed).issubset(MANAGED) or not set(record['present']).issubset(managed):
        raise ValueError('Invalid backup manifest')
    for relative in managed:
        destination = root / relative
        if relative in record['present']:
            replace_path(backup / 'files' / relative, destination)
        elif destination.is_dir():
            shutil.rmtree(destination)
        elif destination.exists():
            destination.unlink()


def healthy(command=run):
    # Type=notify gates startup; also catch a process that exits immediately afterwards.
    for _ in range(5):
        for service in SERVICES:
            if command('systemctl', 'is-active', service) != 'active':
                raise RuntimeError(service + ' did not remain active')
        time.sleep(1)


def install_staged(root, staging, version, command=run, health=healthy):
    regular_layout(root)
    backup = backup_installation(root, version)
    print('Backup:', backup, flush=True)
    try:
        command('systemctl', 'stop', *reversed(SERVICES))
        for relative in MANAGED:
            if (staging / relative).exists():
                replace_path(staging / relative, root / relative)
        command('systemctl', 'daemon-reload')
        command('systemctl', 'start', *SERVICES)
        health(command)
    except BaseException:
        print('Install failed; restoring the previous components.', file=sys.stderr)
        command('systemctl', 'stop', *reversed(SERVICES))
        restore_files(root, backup)
        command('systemctl', 'daemon-reload')
        command('systemctl', 'start', *SERVICES)
        raise
    return backup


def preflight():
    if platform.system() != 'Linux':
        raise ValueError('This installer requires Linux')
    for name in ['systemctl', 'docker', 'casaos']:
        if shutil.which(name) is None:
            raise ValueError(f'Required command is missing: {name}')
    if not re.search(r'\bv?0\.4\.\d+', run('casaos', '-v')):
        raise ValueError('This updater requires an existing CasaOS 0.4.x installation')
    if not Path('/usr/bin/casaos-app-management').is_file() or not Path('/var/lib/casaos/www/index.html').is_file():
        raise ValueError('Expected standard CasaOS installation paths were not found')
    for service, executable in zip(SERVICES, ('/usr/bin/casaos', '/usr/bin/casaos-app-management')):
        if run('systemctl', 'is-active', service) != 'active':
            raise ValueError(service + ' must be active before installation')
        if not Path(executable).is_file() or executable not in run('systemctl', 'show', '-p', 'ExecStart', '--value', service):
            raise ValueError(service + ' uses a custom executable path')
    docker = json.loads(run('docker', 'version', '--format', '{{json .Server}}'))
    minimum = tuple(int(n) for n in docker.get('MinAPIVersion', '1.24').split('.'))
    if minimum > (1, 44):
        raise ValueError('This updater has not been validated with this Docker API minimum')
    return minimum > (1, 43)


def sync_repository(name, base=SOURCE, command=run):
    repository = base / name
    url = 'https://github.com/filippov-au/' + name + '.git'
    base.mkdir(parents=True, exist_ok=True)
    if repository.is_symlink():
        raise ValueError('Source directory must not be a symlink')
    if not repository.exists():
        command('git', 'clone', '--depth=1', '--branch=main', url, str(repository))
    else:
        origin = command('git', '-C', str(repository), 'remote', 'get-url', 'origin')
        if origin != url:
            raise ValueError('Unexpected source origin: ' + origin)
        if command('git', '-C', str(repository), 'status', '--porcelain'):
            raise ValueError('Source checkout has local changes: ' + str(repository))
        command('git', '-C', str(repository), 'fetch', '--depth=1', 'origin', 'main')
        command('git', '-C', str(repository), 'checkout', '--detach', 'FETCH_HEAD')
    return command('git', '-C', str(repository), 'rev-parse', 'HEAD')


def docker_build(image, repository, staging, script, extra=()):
    args = ['docker', 'run', '--rm', '--network=bridge', '-e', 'CI=true', '-v', str(repository) + ':/src',
            '-v', str(staging) + ':/out', '-w', '/src', *extra, image, 'bash', '-euc', script]
    # Stream output instead of keeping compiler and package-manager logs in memory.
    subprocess.run(args, check=True)


def build_components(commits, staging, base=SOURCE):
    binaries = staging / 'usr/bin'
    binaries.mkdir(parents=True)
    docker_build(GO_IMAGE, base / 'CasaOS', staging, r"""
        go generate ./...
        go test ./... -run '^$'
        go test ./internal/sourceupdate ./route/v1 -run 'Source|Check|Start'
        CGO_ENABLED=0 go build -buildvcs=false -trimpath -tags 'netgo osusergo' \
          -ldflags '-s -w' -o /out/usr/bin/casaos .
    """, ['-v', 'casaos-source-gomod:/go/pkg/mod',
            '-v', 'casaos-source-gocache:/root/.cache/go-build'])
    docker_build(GO_IMAGE, base / 'CasaOS-AppManagement', staging, r"""
        go generate ./...
        go test ./... -run '^$'
        go test ./service ./route/...
        go test ./pkg/docker -run '^TestPullStreamDetectsDaemonErrors$'
        CGO_ENABLED=0 go build -buildvcs=false -trimpath -tags 'netgo osusergo' \
          -ldflags "-s -w -X main.commit=$SOURCE_COMMIT" -o /out/usr/bin/casaos-app-management .
        CGO_ENABLED=0 go build -buildvcs=false -trimpath -tags 'netgo osusergo' \
          -ldflags '-s -w' -o /out/usr/bin/appfile2compose ./cmd/appfile2compose
    """, ['-e', 'SOURCE_COMMIT=' + commits['CasaOS-AppManagement'],
            '-v', 'casaos-source-gomod:/go/pkg/mod',
            '-v', 'casaos-source-gocache:/root/.cache/go-build'])
    docker_build(NODE_IMAGE, base / 'CasaOS-UI', staging, r"""
        npx --yes pnpm@9.0.6 install --frozen-lockfile --store-dir /pnpm/store
        npx --yes pnpm@9.0.6 exec vitest run
        npx --yes pnpm@9.0.6 run build
    """, ['-v', 'casaos-source-pnpm:/pnpm/store', '-v', 'casaos-source-npm:/root/.npm'])
    sysroot = base / 'CasaOS-UI/build/sysroot'
    for relative in MANAGED[2:5]:
        source, destination = sysroot / relative, staging / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
    if not (staging / 'var/lib/casaos/www/index.html').is_file():
        raise ValueError('The UI build did not produce an entrypoint')
    updater = staging / MANAGED[6]
    updater.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(base / 'CasaOS/scripts/casaos-source-update.py', updater)
    updater.chmod(0o755)
    unit = staging / MANAGED[9]
    unit.parent.mkdir(parents=True, exist_ok=True)
    unit.write_text('[Unit]\nDescription=Update CasaOS from fork main branches\n'
                    'After=network-online.target docker.service\nWants=network-online.target\n\n'
                    '[Service]\nType=oneshot\nRemainAfterExit=yes\n'
                    'ExecStart=/usr/local/sbin/casaos-source-update web-update\n'
                    'TimeoutStartSec=infinity\n')


def read_installed(root):
    marker = root / MANAGED[7]
    return json.loads(marker.read_text()) if marker.exists() else None


def check_sources(command=run, root=Path('/')):
    installed = read_installed(root)
    for name in REPOSITORIES:
        latest = command('git', 'ls-remote', 'https://github.com/filippov-au/' + name + '.git', 'refs/heads/main').split()[0]
        previous = (installed or {}).get('commits', {}).get(name)
        print(name + ':', latest[:12], '(installed)' if latest == previous else '(update available)')


def web_update(log_path=Path('/var/log/casaos/upgrade.log'), command=subprocess.run):
    """Keep the existing update dialog's log and completion-marker contract.

    systemd owns this process, so restarting casaos.service cannot kill the build.
    The child retains the updater lock and normal backup/recovery behavior.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open('w') as log:
        try:
            result = command([sys.executable, '-u', str(Path(__file__).resolve()), 'update', '--yes'],
                             stdout=log, stderr=subprocess.STDOUT)
            if result.returncode != 0:
                raise RuntimeError('Source updater exited with code ' + str(result.returncode))
        except BaseException as error:
            log.write('\n' + str(error) + '\nCasaOS upgrade failed\n')
            log.flush()
            raise
        log.write('\nCasaOS upgrade successfully\n')
        log.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('web-update', help='Run from the CasaOS update dialog via systemd')
    sub.add_parser('check', help='Compare installed commits with your main branches')
    update = sub.add_parser('update', help='Build your main branches and install after tests pass')
    update.add_argument('--yes', action='store_true', help='Skip the installation confirmation')
    update.add_argument('--force', action='store_true', help='Rebuild even if these commits are already installed')
    update.add_argument('--build-only', action='store_true', help='Build and test, leaving installed components untouched')
    rollback = sub.add_parser('rollback', help='Restore previously backed-up system components')
    rollback.add_argument('--backup', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'check':
        check_sources()
        return
    if os.geteuid() != 0:
        parser.error('Run update or rollback with sudo')
    if args.action == 'web-update':
        web_update()
        return
    root = Path('/')
    state = root / STATE
    regular_layout(root)
    if SOURCE.is_symlink():
        raise ValueError('The source root must not be a symlink')
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / 'lock').open('w') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == 'rollback':
            backup = args.backup.resolve()
            if backup.parent != (state / 'backups').resolve() or not (backup / 'backup.json').is_file():
                raise ValueError('Choose a backup printed by this updater')
            record = json.loads((backup / 'backup.json').read_text())
            if record.get('format') != 1 or not set(record['present']).issubset(MANAGED):
                raise ValueError('Invalid backup manifest')
            safety = backup_installation(root, 'manual-rollback')
            print('Current components saved:', safety)
            try:
                run('systemctl', 'stop', *reversed(SERVICES))
                restore_files(root, backup)
                run('systemctl', 'daemon-reload')
                run('systemctl', 'start', *SERVICES)
                healthy()
            except BaseException:
                run('systemctl', 'stop', *reversed(SERVICES))
                restore_files(root, safety)
                run('systemctl', 'daemon-reload')
                run('systemctl', 'start', *SERVICES)
                raise
            print('Previous components restored. App data was not changed.')
            return
        if shutil.which('git') is None:
            raise ValueError('Install git first: apt install git')
        if not args.build_only:
            api_override = preflight()
        else:
            run('docker', 'info', '--format', '{{.ServerVersion}}')
            api_override = False
        if platform.machine() not in ('x86_64', 'aarch64'):
            raise ValueError('This updater supports x86-64 and ARM64 only')
        if not args.yes:
            print('Fetch your main branches, build and test CasaOS, UI and app-management, and back up existing components.')
            if input('Continue? [y/N] ').lower() != 'y':
                return
        commits = {name: sync_repository(name) for name in REPOSITORIES}
        latest_updater = SOURCE / 'CasaOS/scripts/casaos-source-update.py'
        if latest_updater.read_bytes() != RUNNING_SCRIPT:
            print('Using the updated build instructions from your main branch.', flush=True)
            fcntl.flock(lock, fcntl.LOCK_UN)
            arguments = [sys.executable, str(latest_updater), *sys.argv[1:]]
            if '--yes' not in arguments:
                arguments.append('--yes')
            os.execv(sys.executable, arguments)
        for name, commit in commits.items():
            print(name + ':', commit, flush=True)
        installed = read_installed(root)
        if not args.force and installed and installed.get('commits') == commits:
            print('Already running these main commits.')
            return
        version = 'main-' + commits['CasaOS-AppManagement'][:12] + '-' + commits['CasaOS-UI'][:12]
        workspace = Path(tempfile.mkdtemp(prefix='build-', dir=state))
        staging = workspace / 'root'
        staging.mkdir()
        try:
            build_components(commits, staging)
            if api_override:
                dropin = staging / DROPIN
                dropin.parent.mkdir(parents=True, exist_ok=True)
                dropin.write_text('[Service]\nEnvironment=DOCKER_API_VERSION=1.44\n')
            marker = staging / MANAGED[7]
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps({'version': version, 'commits': commits}, indent=2) + '\n')
            if args.build_only:
                print('Build and tests passed. Prepared files:', staging)
                print('The installed CasaOS was not changed.')
                return
            backup = install_staged(root, staging, version)
            print('Installed', version, '— refresh CasaOS in your browser.')
            print('Rollback command: sudo casaos-source-update rollback --backup', backup)
            print('Use the CasaOS Update button or casaos-source-update for future updates from your main branches.')
        finally:
            if not args.build_only:
                shutil.rmtree(workspace)


if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt) as error:
        print('Update failed:', error, file=sys.stderr)
        sys.exit(1)
