import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/casaos-source-update.py'
spec = importlib.util.spec_from_file_location('source_update', SCRIPT)
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


class SourceUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name) / 'machine'
        self.staging = Path(self.temporary.name) / 'staging'
        self.commands = []
        for relative in updater.MANAGED[:5]:
            old, new = self.root / relative, self.staging / relative
            if relative.endswith('/www'):
                old = old / 'index.html'
                new = new / 'index.html'
            for path, value in [(old, 'old'), (new, 'new')]:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(value)
        self.data = self.root / 'DATA/AppData/demo/database'
        self.data.parent.mkdir(parents=True)
        self.data.write_text('live user data')

    def command(self, *args):
        self.commands.append(args)
        return 'active'

    def test_install_then_rollback_keeps_data(self):
        backup = updater.install_staged(self.root, self.staging, 'main-test', self.command, lambda command: None)
        self.assertEqual((self.root / updater.MANAGED[0]).read_text(), 'new')
        self.assertEqual((self.root / updater.MANAGED[2] / 'index.html').read_text(), 'new')
        self.data.write_text('post-update data')
        updater.restore_files(self.root, backup)
        self.assertEqual((self.root / updater.MANAGED[0]).read_text(), 'old')
        self.assertEqual(self.data.read_text(), 'post-update data')
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)

    def test_startup_failure_restores_both_components(self):
        def health(command):
            raise RuntimeError('startup failed')
        with self.assertRaisesRegex(RuntimeError, 'startup failed'):
            updater.install_staged(self.root, self.staging, 'main-test', self.command, health)
        self.assertEqual((self.root / updater.MANAGED[0]).read_text(), 'old')
        self.assertEqual((self.root / updater.MANAGED[2] / 'index.html').read_text(), 'old')
        self.assertEqual(self.data.read_text(), 'live user data')
        self.assertEqual(self.commands[-1], ('systemctl', 'start', *updater.SERVICES))

    def test_copy_failure_restores_already_replaced_files(self):
        actual = updater.replace_path
        def replace(source, destination):
            if source == self.staging / updater.MANAGED[2]:
                raise OSError('disk full')
            return actual(source, destination)
        with patch.object(updater, 'replace_path', side_effect=replace):
            with self.assertRaisesRegex(OSError, 'disk full'):
                updater.install_staged(self.root, self.staging, 'main-test', self.command, lambda command: None)
        self.assertEqual((self.root / updater.MANAGED[0]).read_text(), 'old')
        self.assertEqual((self.root / updater.MANAGED[2] / 'index.html').read_text(), 'old')

    def test_symlinked_installation_is_rejected_before_service_stop(self):
        target = self.root / updater.MANAGED[0]
        target.unlink()
        target.symlink_to(self.data)
        with self.assertRaises(ValueError):
            updater.install_staged(self.root, self.staging, 'main-test', self.command, lambda command: None)
        self.assertEqual(self.commands, [])
        self.assertEqual(self.data.read_text(), 'live user data')

    def test_new_files_are_removed_on_restore(self):
        backup = updater.backup_installation(self.root, 'main-test')
        added = self.root / updater.MANAGED[6]
        added.parent.mkdir(parents=True, exist_ok=True)
        added.write_text('new updater')
        updater.restore_files(self.root, backup)
        self.assertFalse(added.exists())

    def test_docker_compatibility_preflight(self):
        for minimum, expected in [('1.24', False), ('1.44', True), ('1.45', None)]:
            with self.subTest(minimum=minimum):
                def command(*args):
                    if args[0] == 'casaos':
                        return 'v0.4.15'
                    if args[0] == 'docker':
                        return json.dumps({'MinAPIVersion': minimum})
                    if 'ExecStart' in args:
                        return '/usr/bin/casaos /usr/bin/casaos-app-management'
                    return 'active'
                with patch.object(updater, 'run', side_effect=command), \
                     patch.object(updater.platform, 'system', return_value='Linux'), \
                     patch.object(updater.shutil, 'which', return_value='/usr/bin/tool'), \
                     patch.object(Path, 'is_file', return_value=True):
                    if expected is None:
                        with self.assertRaisesRegex(ValueError, 'Docker API minimum'):
                            updater.preflight()
                    else:
                        self.assertEqual(updater.preflight(), expected)

    def test_web_update_reports_success_only_after_child_finishes(self):
        log = self.root / 'upgrade.log'
        def command(args, **kwargs):
            self.assertEqual(args[-2:], ['update', '--yes'])
            self.assertNotIn('upgrade successfully', log.read_text())
            return type('Result', (), {'returncode': 0})()
        updater.web_update(log, command)
        self.assertIn('CasaOS upgrade successfully', log.read_text())

    def test_web_update_reports_failure(self):
        log = self.root / 'upgrade.log'
        with self.assertRaises(RuntimeError):
            updater.web_update(log, lambda *a, **kw: type('Result', (), {'returncode': 1})())
        self.assertIn('CasaOS upgrade failed', log.read_text())
        self.assertNotIn('CasaOS upgrade successfully', log.read_text())

    def test_legacy_backup_does_not_remove_core_binary(self):
        backup = updater.backup_installation(self.root, 'old-updater')
        record = json.loads((backup / 'backup.json').read_text())
        del record['managed']
        (backup / 'backup.json').write_text(json.dumps(record))
        core = self.root / 'usr/bin/casaos'
        core.write_text('core')
        updater.restore_files(self.root, backup)
        self.assertEqual(core.read_text(), 'core')

    def test_core_is_restored_on_failed_install(self):
        core = self.root / 'usr/bin/casaos'
        core.write_text('old core')
        (self.staging / 'usr/bin/casaos').write_text('new core')
        def health(command):
            self.assertEqual(core.read_text(), 'new core')
            raise RuntimeError('core startup failed')
        with self.assertRaises(RuntimeError):
            updater.install_staged(self.root, self.staging, 'main-test', self.command, health)
        self.assertEqual(core.read_text(), 'old core')

    def test_dirty_checkout_is_not_overwritten(self):
        source = Path(self.temporary.name) / 'sources'
        (source / 'CasaOS').mkdir(parents=True)
        calls = []
        def git(*args):
            calls.append(args)
            if 'get-url' in args:
                return 'https://github.com/filippov-au/CasaOS.git'
            return ' M local-file'
        with self.assertRaisesRegex(ValueError, 'local changes'):
            updater.sync_repository('CasaOS', source, git)
        self.assertFalse(any('fetch' in command for command in calls))

    def test_checkout_uses_own_main(self):
        source = Path(self.temporary.name) / 'sources'
        calls = []
        def git(*args):
            calls.append(args)
            return 'a' * 40
        self.assertEqual(updater.sync_repository('CasaOS-UI', source, git), 'a' * 40)
        self.assertIn('--branch=main', calls[0])
        self.assertIn('https://github.com/filippov-au/CasaOS-UI.git', calls[0])

    def test_manifest_cannot_restore_arbitrary_paths(self):
        backup = updater.backup_installation(self.root, 'main-test')
        (backup / 'backup.json').write_text(json.dumps({'format': 1, 'present': ['etc/shadow']}))
        with self.assertRaises(ValueError):
            updater.restore_files(self.root, backup)


if __name__ == '__main__':
    unittest.main()
