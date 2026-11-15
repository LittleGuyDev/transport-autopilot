"""Isolated enrollment/launch tests; never open Steam or start the real game."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import plistlib
import shlex
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from updater import setup
from updater import launcher
from updater.test_signature import TEST_SIGNING_AVAILABLE, TestSigner


@unittest.skipUnless(TEST_SIGNING_AVAILABLE, 'OpenSSL or explicit ephemeral RSA test fixture required')
class SetupLauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signer = TestSigner()

    @classmethod
    def tearDownClass(cls):
        cls.signer.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='updater-enrollment-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.archive = self.root / 'game/resources/app.asar'
        self.archive.parent.mkdir(parents=True)
        self.archive.write_bytes(b'synthetic archive; never installed or executed')
        self.executable = self.archive.parent.parent / 'Geoplanetical.exe'
        self.executable.write_bytes(b'synthetic executable; never executed')
        self.profile = self.root / 'profile'
        self.profile.mkdir()
        self.state = self.root / 'private-state'
        self.package = self.make_package('transport-autopilot')

    def make_package(self, product, sequence=1, extra_runtime=b''):
        package = self.root / (product + '-' + str(sequence))
        package.mkdir()
        payload = setup._engine().PRODUCTS[product]
        for name in ('install.py', 'core.py', 'platform_support.py', 'registration.py',
                     'legacy_payloads.json', 'mod.json', 'thumbnail.png', payload):
            (package / name).write_bytes(b'{}')
        (package / payload).write_text(json.dumps({'gameVersions': ['1.1.3']}), encoding='utf-8')
        sources = {'auto_update.py': 'engine.py', 'update_signature.py': 'signature.py',
                   'update_setup.py': 'setup.py', 'update_launcher.py': 'launcher.py'}
        for target, source in sources.items():
            (package / target).write_bytes((setup.HERE / source).read_bytes() + extra_runtime)
        (package / 'update_trust.py').write_text('PUBLIC_KEY = ' + repr(self.signer.public_key) + '\n', encoding='utf-8')
        manifest = {'schema': 1, 'product': product, 'version': '1.0.' + str(sequence), 'sequence': sequence,
                    'minimum_updater': 1, 'game_versions': ['1.1.3'], 'files': {}}
        for path in package.iterdir():
            data = path.read_bytes()
            manifest['files'][path.name] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
        raw = json.dumps(manifest, sort_keys=True).encode('utf-8')
        (package / 'release-manifest.json').write_bytes(raw)
        (package / 'release-manifest.sig').write_bytes(self.signer.sign(raw))
        return package

    def enroll(self, package=None, **kwargs):
        return setup.register(package or self.package, self.archive, self.profile,
                              public_key=self.signer.public_key, state_dir=self.state, **kwargs)

    def runtime_launcher(self):
        path = self.state / 'runtime/update_launcher.py'
        spec = importlib.util.spec_from_file_location('_test_installed_launcher', path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def updater(self, *args, **kwargs):
        kwargs['before_updates']()
        kwargs['on_ready'](SimpleNamespace(executable=self.executable))

    def test_register_creates_pinned_private_runtime_and_instructions(self):
        result = self.enroll()
        config = json.loads(result.config.read_text())
        self.assertEqual(config['game'], str(self.archive))
        self.assertEqual(config['profile'], str(self.profile))
        self.assertEqual(config['python'], str(Path(sys.executable).resolve()))
        self.assertEqual(config['products'], [{'product': 'transport-autopilot', 'package_root': str(self.package)}])
        self.assertEqual(result.launcher, self.state / 'runtime/update_launcher.py')
        self.assertIn('--steam -- %command%', result.launch_options)
        self.assertIn('Steam settings were not changed', result.instructions.read_text())
        self.assertTrue(result.shortcut.is_file())
        self.assertNotIn('--steam', result.shortcut.read_text())
        self.assertEqual(set(path.name for path in result.launcher.parent.iterdir()), set(setup.RUNTIME_FILES))
        releases = json.loads((self.state / 'releases.json').read_text())
        self.assertEqual(releases['transport-autopilot']['sequence'], 1)
        if os.name != 'nt':
            self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)
            self.assertEqual(result.config.stat().st_mode & 0o777, 0o600)

    def test_explicit_steam_setup_reports_the_actual_preserved_launch_line(self):
        helper = mock.Mock()
        helper.configure.side_effect = lambda archive, state, line, **kw: SimpleNamespace(launch_options=line + ' --existing-flag')
        result = self.enroll(steam_helper=helper, steam_consent=True)
        self.assertTrue(result.steam_configured)
        self.assertTrue(result.launch_options.endswith('%command% --existing-flag'))
        self.assertIn('No copy and paste is needed', result.instructions.read_text())
        arguments = helper.configure.call_args
        self.assertEqual(arguments.args[:2], (self.archive, self.state))
        self.assertEqual(arguments.kwargs['owned_lines'], ())
        self.assertIs(arguments.kwargs['private'], setup._private)
        self.assertTrue(arguments.kwargs['consent'])
        self.assertNotIn('steam_setup.py', [p.name for p in result.launcher.parent.iterdir()])

    def test_second_mod_steam_setup_recognizes_the_previous_protected_line(self):
        first = self.enroll()
        helper = mock.Mock()
        helper.configure.side_effect = lambda archive, state, line, **kw: SimpleNamespace(launch_options=line)
        tourism = self.make_package('tourism-dashboard')
        second = self.enroll(tourism, steam_helper=helper, steam_consent=True)
        self.assertEqual(helper.configure.call_args.kwargs['owned_lines'], (first.launch_options,))
        self.assertEqual(second.launch_options, first.launch_options)
        config = json.loads(second.config.read_bytes())
        self.assertEqual(len(config['products']), 2)
        self.assertEqual(set(config), {'schema', 'game', 'profile', 'python', 'products', 'runtime'})

    def test_failed_steam_setup_keeps_enrollment_for_a_safe_installer_retry(self):
        helper = mock.Mock()
        helper.configure.side_effect = ValueError('Steam still open')
        before = self.archive.read_bytes()
        with self.assertRaisesRegex(ValueError, 'Steam still open'):
            self.enroll(steam_helper=helper, steam_consent=True)
        self.assertEqual(self.archive.read_bytes(), before)
        config = json.loads((self.state / 'config.json').read_bytes())
        self.assertEqual(len(config['products']), 1)
        self.assertTrue((self.state / 'AUTOMATIC-UPDATES.txt').is_file())

    def test_last_removal_retries_steam_cleanup_after_enrollment_was_disabled(self):
        self.enroll()
        helper = mock.Mock()
        helper.remove.side_effect = [OSError('Steam did not close'), SimpleNamespace(restored=True)]
        with self.assertRaisesRegex(OSError, 'Steam did not close'):
            setup.unregister('transport-autopilot', self.archive, state_dir=self.state,
                             steam_helper=helper, steam_consent=True)
        self.assertEqual(json.loads((self.state / 'config.json').read_bytes())['products'], [])
        self.assertFalse(setup.unregister('transport-autopilot', self.archive, state_dir=self.state,
                                         steam_helper=helper, steam_consent=True))
        self.assertEqual(helper.remove.call_count, 2)
        helper.remove.assert_called_with(self.archive, self.state, consent=True, private=setup._private)

    def test_removing_one_mod_keeps_the_other_mods_steam_hook(self):
        self.enroll()
        self.enroll(self.make_package('tourism-dashboard'))
        helper = mock.Mock()
        self.assertTrue(setup.unregister('transport-autopilot', self.archive, state_dir=self.state,
                                        steam_helper=helper, steam_consent=True))
        helper.remove.assert_not_called()
        self.assertTrue(setup.unregister('tourism-dashboard', self.archive, state_dir=self.state,
                                        steam_helper=helper, steam_consent=True))
        helper.remove.assert_called_once()

    def test_first_install_can_enroll_a_safe_profile_that_does_not_exist_yet(self):
        self.profile = self.root / 'never-opened-game-profile'
        result = self.enroll()
        self.assertEqual(json.loads(result.config.read_text())['profile'], str(self.profile))
        self.assertFalse(self.profile.exists())

    def test_existing_profile_file_is_rejected(self):
        self.profile = self.root / 'profile-file'
        self.profile.write_bytes(b'leave unchanged')
        with self.assertRaisesRegex(ValueError, 'profile directory'):
            self.enroll()
        self.assertEqual(self.profile.read_bytes(), b'leave unchanged')

    def test_two_products_merge_without_replacing_first_trusted_runtime(self):
        self.enroll()
        engine_before = (self.state / 'runtime/auto_update.py').read_bytes()
        tourism = self.make_package('tourism-dashboard', extra_runtime=b'\n# distinct signed runtime\n')
        self.enroll(tourism)
        config = json.loads((self.state / 'config.json').read_text())
        self.assertEqual(len(config['products']), 2)
        self.assertEqual((self.state / 'runtime/auto_update.py').read_bytes(), engine_before)

    def legacy_runtime(self):
        """Create and sign the previously shipped two-product runtime."""
        path = self.package / 'auto_update.py'
        path.write_bytes(path.read_bytes().replace(
            b",\n            'vaccination-autopilot': 'vaccination-payload.json'", b''))
        path = self.package / 'update_setup.py'
        path.write_bytes(path.read_bytes().replace(
            b"len(value['products']) > len(engine.PRODUCTS)", b"len(value['products']) > 2"))
        self.resign(self.package)

    def resign(self, package):
        manifest_path = package / 'release-manifest.json'
        value = json.loads(manifest_path.read_bytes())
        for name in value['files']:
            data = (package / name).read_bytes()
            value['files'][name] = {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}
        raw = json.dumps(value, sort_keys=True).encode('utf-8')
        manifest_path.write_bytes(raw)
        (package / 'release-manifest.sig').write_bytes(self.signer.sign(raw))

    def test_legacy_two_product_runtime_migrates_to_signed_generation_for_third(self):
        self.legacy_runtime()
        self.enroll()
        self.enroll(self.make_package('tourism-dashboard'))
        old = {p.name:p.read_bytes() for p in (self.state / 'runtime').iterdir()}
        package = self.make_package('vaccination-autopilot')
        steam = mock.Mock()
        steam.configure.return_value = SimpleNamespace(launch_options='managed new launch options')
        result = self.enroll(package, steam_helper=steam, steam_consent=True)
        config = json.loads(result.config.read_bytes())
        runtime = Path(config['runtime'])
        self.assertRegex(runtime.name, r'^runtime-[a-f0-9]{24}$')
        self.assertEqual(len(config['products']), 3)
        self.assertEqual({p.name:p.read_bytes() for p in (self.state / 'runtime').iterdir()}, old)
        self.assertIn('vaccination-autopilot', setup._runtime_products(runtime))
        self.assertIn(str(runtime), steam.configure.call_args.args[2])
        self.assertIn(str(self.state / 'runtime/update_launcher.py'), steam.configure.call_args.kwargs['owned_lines'][0])
        self.assertTrue(setup._config(setup._engine(), result.config))
        spec = importlib.util.spec_from_file_location('_migrated_launcher_test', runtime / 'update_launcher.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        process = mock.Mock(return_value='launched')
        def verified(state, products, game, profile, **kwargs):
            self.assertEqual(len(products), 3)
            kwargs['before_updates']()
            kwargs['on_ready'](SimpleNamespace(executable=self.executable))
        self.assertEqual(module.launch(result.config, [str(self.executable)], updater=verified, popen=process), 'launched')
        process.assert_called_once_with([str(self.executable)], shell=False)
        module._error_log(result.config, 'safe generation diagnostic')
        self.assertIn('safe generation diagnostic', (self.state / 'last-error.txt').read_text())
        self.enroll(package)
        self.assertEqual(json.loads(result.config.read_bytes())['runtime'], str(runtime))
        self.assertTrue(setup.unregister('transport-autopilot', self.archive, state_dir=self.state))
        self.assertTrue(setup.unregister('tourism-dashboard', self.archive, state_dir=self.state))
        self.assertEqual([r['product'] for r in json.loads(result.config.read_bytes())['products']], ['vaccination-autopilot'])

    def test_migrated_generation_recovers_after_steam_setup_failure(self):
        self.legacy_runtime()
        self.enroll()
        old_runtime = self.state / 'runtime'
        package = self.make_package('vaccination-autopilot')
        steam = mock.Mock()
        steam.configure.side_effect = ValueError('Steam must close safely first')
        with self.assertRaisesRegex(ValueError, 'Steam must close'):
            self.enroll(package, steam_helper=steam, steam_consent=True)
        pending = json.loads((self.state / 'config.json').read_bytes())
        self.assertNotEqual(pending['runtime'], str(old_runtime))
        self.assertTrue((old_runtime / 'update_launcher.py').is_file())
        # The real helper's private journal recognizes its prior generated
        # field; a retry must keep the same complete signed generation.
        steam.configure.side_effect = None
        steam.configure.return_value = SimpleNamespace(launch_options='repaired managed options')
        result = self.enroll(package, steam_helper=steam, steam_consent=True)
        self.assertEqual(json.loads(result.config.read_bytes())['runtime'], pending['runtime'])
        self.assertTrue(result.steam_configured)

    def test_third_product_tamper_cannot_migrate_pinned_runtime_or_registration(self):
        self.legacy_runtime()
        self.enroll()
        before = (self.state / 'config.json').read_bytes()
        package = self.make_package('vaccination-autopilot')
        (package / 'auto_update.py').write_text('raise AssertionError("unsigned")')
        with self.assertRaises(ValueError):
            self.enroll(package)
        self.assertEqual((self.state / 'config.json').read_bytes(), before)
        self.assertEqual(list(self.state.glob('runtime-*')), [])

    def test_runtime_generation_path_cannot_escape_private_registration(self):
        for path in (self.root / 'runtime', self.state / 'runtime-../escape', self.state / 'runtime-untrusted'):
            with self.assertRaises(ValueError):
                setup._runtime_path(self.state, path)

    def test_manual_enrollment_uses_cached_engine_and_explicit_key_without_source_imports(self):
        engine = setup._engine()
        # Populate the retained engine's pinned-verifier cache during preflight.
        engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')
        load_key = setup._load_key
        def protected_key_only(runtime=None):
            if runtime is None:
                self.fail('Cached manual enrollment must not re-import the downloaded trust module')
            return load_key(runtime)
        with mock.patch.object(setup, '_engine', side_effect=AssertionError('Source engine re-import')), \
                mock.patch.object(setup, '_load_key', side_effect=protected_key_only):
            result = self.enroll(trusted_engine=engine)
        self.assertTrue(result.config.is_file())

    def test_changed_source_engine_cannot_execute_after_manual_preflight(self):
        engine = setup._engine()
        engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')
        (self.package / 'auto_update.py').write_text('raise AssertionError("Unverified source engine executed")\n')
        with mock.patch.object(setup, '_engine', side_effect=AssertionError('Source engine re-import')):
            with self.assertRaisesRegex(ValueError, 'verification'):
                self.enroll(trusted_engine=engine)
        self.assertFalse((self.state / 'runtime').exists())

    def test_signature_or_package_tamper_creates_no_runtime_or_enrollment(self):
        (self.package / 'core.py').write_bytes(b'unsigned attacker content')
        with self.assertRaises(ValueError):
            self.enroll()
        self.assertFalse((self.state / 'runtime').exists())
        self.assertFalse((self.state / 'config.json').exists())

    def test_conflicting_profile_and_game_are_rejected(self):
        self.enroll()
        profile = self.root / 'other-profile'
        profile.mkdir()
        with self.assertRaises(ValueError):
            setup.register(self.package, self.archive, profile,
                           public_key=self.signer.public_key, state_dir=self.state)
        other_archive = self.root / 'other-game/resources/app.asar'
        other_archive.parent.mkdir(parents=True)
        other_archive.write_bytes(b'other game')
        with self.assertRaises(ValueError):
            setup.register(self.package, other_archive, self.profile,
                           public_key=self.signer.public_key, state_dir=self.state)

    def test_lower_release_and_same_sequence_conflict_are_rejected(self):
        newer = self.make_package('transport-autopilot', 2)
        self.enroll(newer)
        with self.assertRaisesRegex(ValueError, 'rollback'):
            self.enroll()
        manifest_path = newer / 'release-manifest.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['version'] = '1.0.99'
        raw = json.dumps(manifest, sort_keys=True).encode()
        manifest_path.write_bytes(raw)
        (newer / 'release-manifest.sig').write_bytes(self.signer.sign(raw))
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            self.enroll(newer)

    @unittest.skipIf(os.name == 'nt', 'Windows symlinks need developer-mode privileges')
    def test_link_paths_and_shared_existing_state_are_rejected(self):
        alias = self.root / 'package-link'
        alias.symlink_to(self.package, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.enroll(alias)
        self.state.mkdir(mode=0o755)
        self.state.chmod(0o755)
        with self.assertRaisesRegex(ValueError, 'private'):
            self.enroll()

    @unittest.skipIf(os.name == 'nt', 'Windows symlinks need developer-mode privileges')
    def test_instruction_symlink_never_overwrites_another_file(self):
        result = self.enroll()
        victim = self.root / 'leave-untouched.txt'
        victim.write_text('unchanged')
        result.instructions.unlink()
        result.instructions.symlink_to(victim)
        with self.assertRaisesRegex(ValueError, 'symlink'):
            self.enroll()
        self.assertEqual(victim.read_text(), 'unchanged')

    def test_steam_launch_forwards_exact_argv_without_shell_or_uri(self):
        self.enroll()
        module = self.runtime_launcher()
        args = [str(self.executable), '--flag', 'a b', ';touch never', '$HOME', '"literal"']
        process, opener = mock.Mock(), mock.Mock()
        module.launch(self.state / 'config.json', args, popen=process, steam_opener=opener, updater=self.updater)
        process.assert_called_once_with(args, shell=False)
        opener.assert_not_called()

    def test_wrong_executable_is_rejected_before_updating(self):
        self.enroll()
        updater, process, opener = mock.Mock(), mock.Mock(), mock.Mock()
        with self.assertRaisesRegex(ValueError, 'different executable'):
            self.runtime_launcher().launch(self.state / 'config.json', [str(self.root / 'evil.exe')],
                                           popen=process, steam_opener=opener, updater=updater)
        updater.assert_not_called()
        process.assert_not_called()
        opener.assert_not_called()

    def test_update_failure_stops_game_and_steam(self):
        self.enroll()
        process, opener = mock.Mock(), mock.Mock()
        for args in (None, [str(self.executable)]):
            with self.subTest(args=args), self.assertRaisesRegex(ValueError, 'rejected'):
                self.runtime_launcher().launch(self.state / 'config.json', args, popen=process,
                    steam_opener=opener, updater=mock.Mock(side_effect=ValueError('rejected')))
        process.assert_not_called()
        opener.assert_not_called()

    def test_shortcut_opens_steam_only_after_updates(self):
        self.enroll()
        events = []
        def update(*args, **kwargs):
            self.updater(*args, **kwargs)
            events.append('updated')
        self.runtime_launcher().launch(self.state / 'config.json', updater=update,
            popen=mock.Mock(side_effect=AssertionError('no direct game launch')),
            steam_opener=lambda: events.append('steam'))
        self.assertEqual(events, ['updated', 'steam'])

    def test_unregister_preserves_other_mod_and_last_removal_allows_plain_launch(self):
        self.enroll()
        self.enroll(self.make_package('tourism-dashboard'))
        self.assertTrue(setup.unregister('transport-autopilot', self.archive, state_dir=self.state))
        self.assertFalse(setup.unregister('transport-autopilot', self.archive, state_dir=self.state))
        config = json.loads((self.state / 'config.json').read_text())
        self.assertEqual([item['product'] for item in config['products']], ['tourism-dashboard'])
        self.assertTrue(setup.unregister('tourism-dashboard', self.archive, state_dir=self.state))
        process, updater = mock.Mock(), mock.Mock()
        self.runtime_launcher().launch(self.state / 'config.json', [str(self.executable)],
                                       popen=process, updater=updater)
        process.assert_called_once_with([str(self.executable)], shell=False)
        updater.assert_not_called()

    def test_manual_unregister_uses_retained_engine_without_source_reload(self):
        self.enroll()
        engine = setup._engine()
        with mock.patch.object(setup, '_engine', side_effect=AssertionError('Source engine re-import')):
            self.assertTrue(setup.unregister('transport-autopilot', self.archive,
                                             state_dir=self.state, trusted_engine=engine))

    def test_manual_transaction_serializes_enrollment_and_removal_with_launchers(self):
        engine = setup._engine()
        with setup.transaction(self.archive, engine, state_dir=self.state):
            with self.assertRaises(ValueError):
                with engine.exclusive(self.state):
                    self.fail('A launcher entered during the manual transaction')
            self.enroll(trusted_engine=engine, already_locked=True)
            self.assertTrue(setup.unregister('transport-autopilot', self.archive, state_dir=self.state,
                                             trusted_engine=engine, already_locked=True))
            with self.assertRaises(ValueError):
                with engine.exclusive(self.state):
                    self.fail('Enrollment/removal released the manual transaction lock')
        with engine.exclusive(self.state):
            pass

    def test_unregister_race_aborts_before_stale_enrollment_can_apply(self):
        self.enroll()
        process = mock.Mock()
        def racing_update(*args, **kwargs):
            setup.unregister('transport-autopilot', self.archive, state_dir=self.state)
            kwargs['before_updates']()
            self.fail('Changed enrollment should stop before any installer applies')
        with self.assertRaisesRegex(ValueError, 'enrollment changed'):
            self.runtime_launcher().launch(self.state / 'config.json', [str(self.executable)],
                                           updater=racing_update, popen=process)
        process.assert_not_called()

    def test_foreign_config_runtime_cannot_select_imports_or_launch(self):
        self.enroll()
        config_path = self.state / 'config.json'
        value = json.loads(config_path.read_text())
        value['runtime'] = str(self.root)
        config_path.write_text(json.dumps(value))
        process = mock.Mock()
        with self.assertRaisesRegex(ValueError, 'trusted launcher'):
            self.runtime_launcher().launch(config_path, [str(self.executable)], popen=process)
        process.assert_not_called()

    def test_native_mac_executable_matches_enrolled_bundle(self):
        app = self.root / 'Geoplanetical.app'
        self.archive = app / 'Contents/Resources/app.asar'
        self.archive.parent.mkdir(parents=True)
        self.archive.write_bytes(b'synthetic native archive')
        self.executable = app / 'Contents/MacOS/Geoplanetical'
        self.executable.parent.mkdir()
        self.executable.write_bytes(b'synthetic native executable')
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleExecutable': 'Geoplanetical'}))
        self.enroll()
        process = mock.Mock()
        self.runtime_launcher().launch(self.state / 'config.json', [str(self.executable), '--native'],
                                       updater=self.updater, popen=process)
        process.assert_called_once_with([str(self.executable), '--native'], shell=False)

    def test_missing_native_bundle_reaches_worker_and_launches_only_after_recovery(self):
        app = self.root / 'Geoplanetical.app'
        self.archive = app / 'Contents/Resources/app.asar'
        self.archive.parent.mkdir(parents=True)
        self.archive.write_bytes(b'synthetic native archive')
        self.executable = app / 'Contents/MacOS/Geoplanetical'
        self.executable.parent.mkdir()
        self.executable.write_bytes(b'synthetic native executable')
        plist = app / 'Contents/Info.plist'
        plist.write_bytes(plistlib.dumps({'CFBundleExecutable': 'Geoplanetical'}))
        self.enroll()
        shutil.rmtree(app)
        process = mock.Mock()
        def recovering_update(*args, **kwargs):
            kwargs['before_updates']()
            self.archive.parent.mkdir(parents=True)
            self.archive.write_bytes(b'recovered synthetic archive')
            self.executable.parent.mkdir()
            self.executable.write_bytes(b'recovered synthetic executable')
            plist.write_bytes(plistlib.dumps({'CFBundleExecutable': 'Geoplanetical'}))
            kwargs['on_ready'](SimpleNamespace(executable=self.executable))
        self.runtime_launcher().launch(self.state / 'config.json', [str(self.executable)],
                                       updater=recovering_update, popen=process)
        process.assert_called_once_with([str(self.executable)], shell=False)

    def test_missing_native_bundle_never_launches_when_worker_did_not_restore_it(self):
        app = self.root / 'Geoplanetical.app'
        self.archive = app / 'Contents/Resources/app.asar'
        self.archive.parent.mkdir(parents=True)
        self.archive.write_bytes(b'synthetic native archive')
        self.executable = app / 'Contents/MacOS/Geoplanetical'
        self.enroll()
        shutil.rmtree(app)
        process = mock.Mock()
        with self.assertRaisesRegex(ValueError, 'missing or invalid'):
            self.runtime_launcher().launch(self.state / 'config.json', [str(self.executable)],
                                           updater=self.updater, popen=process)
        process.assert_not_called()

    def test_cli_requires_original_argv_for_steam_mode(self):
        with mock.patch.object(launcher, 'launch') as launch:
            self.assertEqual(launcher.main(['--config', '/absolute/config.json', '--steam', '--',
                                            '/game/Geoplanetical.exe', '--flag', 'value']), 0)
            launch.assert_called_once_with('/absolute/config.json', ['/game/Geoplanetical.exe', '--flag', 'value'])
        with mock.patch('sys.stderr'), self.assertRaises(SystemExit):
            launcher.main(['--config', '/absolute/config.json', '--steam'],
                          notifier=mock.Mock(), interactive=True)

    def test_noninteractive_main_failure_writes_private_log_and_notifies_exact_message(self):
        self.enroll()
        module = self.runtime_launcher()
        notifier = mock.Mock()
        error_log = self.state / 'last-error.txt'
        with mock.patch.object(module, 'launch', side_effect=ValueError('Release signature rejected')), \
                mock.patch('sys.stderr', new=io.StringIO()) as stderr:
            result = module.main(['--config', str(self.state / 'config.json')],
                                  notifier=notifier, interactive=False)
        self.assertEqual(result, 1)
        expected = ('Reason: Release signature rejected\n\nError log: ' + str(error_log) + '\n\n'
                    + module.REPAIR_INSTRUCTIONS)
        notifier.assert_called_once_with('Automatic update stopped', expected)
        self.assertEqual(stderr.getvalue(), 'Automatic update stopped\n' + expected + '\n')
        self.assertEqual(error_log.read_text(), 'Automatic update stopped\n\nReason: Release signature rejected\n\n'
                         + module.REPAIR_INSTRUCTIONS + '\n')
        self.assertLessEqual(error_log.stat().st_size, module.MAX_ERROR_BYTES)
        if os.name != 'nt':
            self.assertEqual(error_log.stat().st_mode & 0o777, 0o600)

    def test_interactive_failure_logs_and_prints_without_native_popup(self):
        self.enroll()
        module = self.runtime_launcher()
        notifier = mock.Mock()
        with mock.patch.object(module, 'launch', side_effect=OSError('Python worker is unavailable')), \
                mock.patch('sys.stderr', new=io.StringIO()) as stderr:
            self.assertEqual(module.main(['--config', str(self.state / 'config.json')],
                                         notifier=notifier, interactive=True), 1)
        notifier.assert_not_called()
        self.assertIn('Python worker is unavailable', stderr.getvalue())
        self.assertTrue((self.state / 'last-error.txt').is_file())

    def test_invalid_steam_arguments_keep_exit_two_and_show_actionable_feedback(self):
        self.enroll()
        module = self.runtime_launcher()
        notifier = mock.Mock()
        with mock.patch('sys.stderr', new=io.StringIO()), self.assertRaises(SystemExit) as error:
            module.main(['--config', str(self.state / 'config.json'), '--steam'],
                        notifier=notifier, interactive=False)
        self.assertEqual(error.exception.code, 2)
        notifier.assert_called_once()
        self.assertIn('Invalid launch options: --steam requires', notifier.call_args.args[1])
        self.assertTrue((self.state / 'last-error.txt').is_file())

    def test_failure_without_stderr_still_notifies_and_logs(self):
        self.enroll()
        module = self.runtime_launcher()
        notifier = mock.Mock()
        with mock.patch.object(module, 'launch', side_effect=ValueError('Launch blocked')), \
                mock.patch('sys.stderr', None):
            self.assertEqual(module.main(['--config', str(self.state / 'config.json')], notifier=notifier), 1)
        notifier.assert_called_once()
        self.assertTrue((self.state / 'last-error.txt').is_file())

    def test_foreign_config_never_selects_error_log_directory(self):
        self.enroll()
        module = self.runtime_launcher()
        foreign = self.root / 'foreign-state'
        foreign.mkdir(mode=0o700)
        path = foreign / 'config.json'
        shutil.copyfile(self.state / 'config.json', path)
        notifier = mock.Mock()
        with mock.patch.object(module, 'launch', side_effect=ValueError(str(foreign / 'attacker-selected.txt'))), \
                mock.patch('sys.stderr', new=io.StringIO()):
            self.assertEqual(module.main(['--config', str(path)], notifier=notifier, interactive=False), 1)
        self.assertFalse((foreign / 'last-error.txt').exists())
        self.assertFalse((foreign / 'attacker-selected.txt').exists())
        self.assertFalse((self.state / 'last-error.txt').exists())
        self.assertIn('A private error log could not be written', notifier.call_args.args[1])

    @unittest.skipIf(os.name == 'nt', 'Windows symlinks need developer-mode privileges')
    def test_error_log_symlink_never_overwrites_a_different_file(self):
        self.enroll()
        module = self.runtime_launcher()
        victim = self.root / 'leave-untouched.txt'
        victim.write_text('unchanged')
        (self.state / 'last-error.txt').symlink_to(victim)
        with mock.patch('sys.stderr', new=io.StringIO()):
            module._report_failure(self.state / 'config.json', ValueError('blocked'),
                                   notifier=mock.Mock(), interactive=False)
        self.assertEqual(victim.read_text(), 'unchanged')
        self.assertTrue((self.state / 'last-error.txt').is_symlink())

    def test_error_log_is_bounded_sanitized_and_replaced_instead_of_appended(self):
        self.enroll()
        module = self.runtime_launcher()
        notifier = mock.Mock()
        with mock.patch('sys.stderr', new=io.StringIO()):
            module._report_failure(self.state / 'config.json', ValueError('\x1b[31m\n' + '🔒' * 20000),
                                   notifier=notifier, interactive=False)
        path = self.state / 'last-error.txt'
        self.assertLessEqual(path.stat().st_size, module.MAX_ERROR_BYTES)
        self.assertNotIn('\x1b', path.read_text())
        self.assertIn(module.REPAIR_INSTRUCTIONS, path.read_text())
        with mock.patch('sys.stderr', new=io.StringIO()):
            module._report_failure(self.state / 'config.json', ValueError('newest error'),
                                   notifier=notifier, interactive=False)
        self.assertIn('newest error', path.read_text())
        self.assertNotIn('🔒', path.read_text())
        self.assertEqual(list(self.state.glob('.last-error-*.tmp')), [])

    def test_notifier_failure_preserves_failure_exit_and_persistent_details(self):
        self.enroll()
        module = self.runtime_launcher()
        with mock.patch.object(module, 'launch', side_effect=ValueError('Verification failed')), \
                mock.patch('sys.stderr', new=io.StringIO()) as stderr:
            self.assertEqual(module.main(['--config', str(self.state / 'config.json')],
                notifier=mock.Mock(side_effect=OSError('No GUI session')), interactive=False), 1)
        self.assertIn('dialog could not be displayed', stderr.getvalue())
        self.assertIn('Verification failed', (self.state / 'last-error.txt').read_text())

    def test_macos_notifier_uses_fixed_script_and_separate_untrusted_arguments(self):
        title = 'Automatic update stopped'
        message = 'quotes " ; do shell script "unexpected"\n$(not-a-command)'
        with mock.patch.object(launcher.sys, 'platform', 'darwin'), \
                mock.patch.object(launcher.subprocess, 'Popen') as popen:
            launcher._notify_failure(title, message)
        command = popen.call_args.args[0]
        self.assertEqual(command[:2], ['/usr/bin/osascript', '-e'])
        self.assertEqual(command[-3:], ['--', title, message])
        self.assertNotIn(message, command[2])
        self.assertIn('giving up after 60', command[2])
        self.assertFalse(popen.call_args.kwargs['shell'])

    @unittest.skipUnless(os.name == 'nt', 'Native Windows typed-notifier test without showing UI')
    def test_windows_notifier_passes_text_to_typed_native_api(self):
        import ctypes
        show = mock.Mock(return_value=1)
        library = SimpleNamespace(MessageBoxW=show)
        with mock.patch.object(launcher.sys, 'platform', 'win32'), mock.patch.object(ctypes, 'WinDLL', return_value=library):
            launcher._notify_failure('Automatic update stopped', 'Reason: blocked')
        show.assert_called_once_with(None, 'Reason: blocked', 'Automatic update stopped', 0x00050030)
        self.assertEqual(len(show.argtypes), 4)
        self.assertIs(show.restype, ctypes.c_int)

    @unittest.skipIf(os.name == 'nt', 'Uses a bounded synthetic POSIX executable, never a real game')
    def test_isolated_installed_launcher_ignores_pythonpath_and_forwards_arguments(self):
        self.enroll()
        setup.unregister('transport-autopilot', self.archive, state_dir=self.state)
        record = self.root / 'synthetic-argv.txt'
        self.executable.write_text('#!/bin/sh\nprintf \'%s\\n\' "$@" > ' + shlex.quote(str(record)) + '\n')
        self.executable.chmod(0o700)
        poison = self.root / 'untrusted-cwd'
        poison.mkdir()
        (poison / 'json.py').write_text('raise RuntimeError("Unsigned PYTHONPATH import")\n')
        (poison / 'update_setup.py').write_text('raise RuntimeError("Unsigned sibling import")\n')
        arguments = ['a b', ';literal-not-a-command', '$HOME', '"quoted"']
        def hashes():
            return {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for folder in (self.archive.parent.parent, self.profile, self.state)
                    for path in folder.rglob('*') if path.is_file()}
        before = hashes()
        result = subprocess.run([str(Path(sys.executable).resolve()), '-I', '-S',
            str(self.state / 'runtime/update_launcher.py'), '--config', str(self.state / 'config.json'),
            '--steam', '--', str(self.executable)] + arguments,
            cwd=poison, env={**os.environ, 'PYTHONPATH': str(poison)}, shell=False,
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        # Popen intentionally does not wait for the game; the tiny fixture may
        # complete just after the launcher process exits.
        import time
        deadline = time.monotonic() + 2
        while not record.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertEqual(record.read_text().splitlines(), arguments)
        self.assertEqual(hashes(), before)
        self.assertFalse((self.state / 'last-error.txt').exists())

    @unittest.skipUnless(os.name == 'nt', 'Native Windows security API acceptance')
    def test_windows_private_cache_can_be_revalidated(self):
        self.enroll()
        setup._private(self.state)
        setup._private(self.state / 'runtime')


if __name__ == '__main__':
    unittest.main()
