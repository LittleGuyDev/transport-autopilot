"""Synthetic Steam settings fixtures: never read or launch the real client."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location('tested_steam_setup', Path(__file__).with_name('steam.py'))
steam = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = steam
SPEC.loader.exec_module(steam)

LINE = '"/safe/python" -I -S "/safe/update_launcher.py" --config "/safe/config.json" --steam -- %command%'


def local(value=None, newline=b'\n'):
    field = b'' if value is None else b'"LaunchOptions"\t' + steam._encode(value) + b' // retained comment\n'
    return (b'\xef\xbb\xbf// header\n"UserLocalConfigStore"\n{\n"Other" "\\\\literal\\\"quoted"\n'
            b'"Software" { "Valve" { "Steam" { "apps" { "940230" {\n' + field +
            b'"Unrelated" "keep me"\n} "1234" { "LaunchOptions" "other app" } } } } }\n}\n').replace(b'\n', newline)


class FakeProcess:
    def __init__(self, running=False, close=True, on_shutdown=None):
        self.running = running
        self.close = close
        self.on_shutdown = on_shutdown
        self.calls = []

    def is_running(self, executable):
        self.calls.append('inspect')
        return self.running

    def shutdown(self, executable):
        self.calls.append('shutdown')
        if self.on_shutdown:
            self.on_shutdown()
        if self.close:
            self.running = False

    def restart(self, executable):
        self.calls.append('restart')
        self.running = True

    def wait(self):
        pass


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name).resolve()
        self.root = self.base / 'Steam'
        self.state = self.base / 'private'
        self.config = self.root / 'userdata/123/config/localconfig.vdf'
        self.archive = self.root / 'steamapps/common/Geoplanetical/Geoplanetical.app/Contents/Resources/app.asar'
        self.write(self.root / 'Steam.AppBundle/Steam/Contents/MacOS/steam_osx', b'fake executable')
        self.write(self.root / 'steam.exe', b'fake executable')
        self.write(self.root / 'registry.vdf', b'"HKCU" { "Software" { "Valve" { "Steam" { "AutoLoginUser" "fixture" } } } }')
        self.users = self.root / 'config/loginusers.vdf'
        self.write(self.users, ('"users" { "%d" { "AccountName" "fixture" "MostRecent" "1" "AutoLogin" "1" } }' % (steam.STEAM_ID_BASE + 123)).encode())
        self.write(self.root / 'steamapps/libraryfolders.vdf', b'"libraryfolders" { "0" { "path" ' + steam._encode(str(self.root)) + b' } }')
        self.write(self.root / 'steamapps/appmanifest_940230.acf', b'"AppState" { "appid" "940230" "installdir" "Geoplanetical" }')
        self.write(self.archive, b'fixture archive; not read by Steam helper')
        self.write(self.config, local('--existing "space argument"'))

    def write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def private(self, path):
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.chmod(0o700)
        return path

    def kwargs(self, **extra):
        return dict(platform='darwin', steam_root=self.root, **extra)

    def configure(self, line=LINE, **kwargs):
        return steam.configure(self.archive, self.state, line, private=self.private,
                               **self.kwargs(process=kwargs.pop('process', FakeProcess()), **kwargs))

    def remove(self, **kwargs):
        return steam.remove(self.archive, self.state, private=self.private,
                            **self.kwargs(process=kwargs.pop('process', FakeProcess()), **kwargs))

    def value(self):
        entry = steam._launch(self.config.read_bytes())
        return None if entry is None else entry.value.value

    def record(self):
        return json.loads((self.state / steam.METADATA).read_text())


class ParserTests(unittest.TestCase):
    def test_lossless_token_replacement_bom_crlf_comments_escapes(self):
        source = local('--old', b'\r\n')
        field = steam._launch(source)
        updated = steam.replace_launch_options(source, LINE + ' --old')
        self.assertEqual(updated, source[:field.value.start] + steam._encode(LINE + ' --old') + source[field.value.end:])
        self.assertTrue(updated.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(updated.count(b'\r\n'), source.count(b'\r\n'))

    def test_quoted_braces_and_comment_markers_are_values(self):
        root = steam.parse(b'key "{ // /* }" /* block */ next unquoted // eol\n')
        self.assertEqual(steam._value(root, 'key'), '{ // /* }')
        self.assertEqual(steam._value(root, 'next'), 'unquoted')

    def test_missing_ancestors_insert_without_reformatting(self):
        source = b'"UserLocalConfigStore" { "Unrelated" "same" } //tail'
        updated = steam.replace_launch_options(source, LINE)
        self.assertIn(b'"Unrelated" "same"', updated)
        self.assertTrue(updated.endswith(b'} //tail'))
        self.assertEqual(steam._launch(updated).value.value, LINE)

    def test_remove_only_key_value_span(self):
        source = local('--old')
        field = steam._launch(source)
        self.assertEqual(steam.replace_launch_options(source, None), source[:field.key.start] + source[field.value.end:])

    def test_invalid_or_ambiguous_documents_fail_closed(self):
        cases = [b'"key"', b'a {', b'a }', b'}', b'{ a b }', b'a b A c',
                 b'a { x 1 X 2 }', b'a "\\q"', b'a "unterminated', b'/* unfinished',
                 b'a b [condition]', b'#include "file"', b'a "bad\0value"', b'\xff',
                 b'a {' * 66 + b'}' * 66]
        for source in cases:
            with self.subTest(source=source[:20]):
                with self.assertRaises(steam.SteamError):
                    steam.parse(source)

    def test_wrong_root_and_scalar_ancestors_refused(self):
        for source in (b'"OtherRoot" {}', b'"UserLocalConfigStore" { "Software" "scalar" }'):
            with self.assertRaises(steam.SteamError):
                steam.replace_launch_options(source, LINE)


class DiscoveryTests(Fixture):
    def test_mac_autologin_works_without_most_recent(self):
        self.write(self.users, self.users.read_bytes().replace(b' "MostRecent" "1"', b''))
        target = steam.discover(self.archive, **self.kwargs())
        self.assertEqual(target.localconfig, self.config)

    def test_multiple_userdata_never_select_newest(self):
        self.write(self.root / 'userdata/999/config/localconfig.vdf', local('--unselected'))
        self.assertEqual(steam.discover(self.archive, **self.kwargs()).localconfig, self.config)

    def test_ambiguous_account_name_refused(self):
        self.write(self.users, ('"users" { "%d" { "AccountName" "fixture" } "%d" { "AccountName" "FIXTURE" } }' %
                               (steam.STEAM_ID_BASE + 123, steam.STEAM_ID_BASE + 999)).encode())
        with self.assertRaises(steam.SteamError):
            steam.discover(self.archive, **self.kwargs())

    def test_missing_autologin_refused_even_one_userdata(self):
        self.write(self.root / 'registry.vdf', b'"HKCU" { "Software" { "Valve" { "Steam" {} } } }')
        with self.assertRaises(steam.SteamError):
            steam.discover(self.archive, **self.kwargs())

    def test_windows_active_user_and_last_selected(self):
        for active in (123, 0):
            target = steam.discover(self.archive, platform='win32',
                                    windows_registry=lambda: {'SteamPath': str(self.root), 'ActiveUser': active})
            self.assertEqual(target.localconfig, self.config)
        with self.assertRaises(steam.SteamError):
            steam.discover(self.archive, platform='win32', windows_registry=lambda: {'SteamPath': str(self.root), 'ActiveUser': 456})

    def test_windows_ambiguous_auto_login_refused(self):
        self.write(self.users, ('"users" { "%d" { "AccountName" "fixture" "AutoLogin" "1" } "%d" { "AccountName" "other" "AutoLogin" "1" } }' %
                               (steam.STEAM_ID_BASE + 123, steam.STEAM_ID_BASE + 999)).encode())
        with self.assertRaises(steam.SteamError):
            steam.discover(self.archive, platform='win32', windows_registry=lambda: {'SteamPath': str(self.root), 'ActiveUser': 0})

    def test_windows_registry_autologin_without_recent_or_flags(self):
        self.write(self.users, ('"users" { "%d" { "AccountName" "fixture" } }' % (steam.STEAM_ID_BASE + 123)).encode())
        target = steam.discover(self.archive, platform='win32', windows_registry=lambda:
                               {'SteamPath': str(self.root), 'ActiveUser': 0, 'AutoLoginUser': 'FIXTURE'})
        self.assertEqual(target.localconfig, self.config)
        with self.assertRaises(steam.SteamError):
            steam.discover(self.archive, platform='win32', windows_registry=lambda:
                           {'SteamPath': str(self.root), 'ActiveUser': 0, 'AutoLoginUser': 'missing'})

    def test_windows_unique_explicit_autologin_without_recent(self):
        self.write(self.users, self.users.read_bytes().replace(b' "MostRecent" "1"', b''))
        target = steam.discover(self.archive, platform='win32', windows_registry=lambda:
                               {'SteamPath': str(self.root), 'ActiveUser': 0})
        self.assertEqual(target.localconfig, self.config)

    def test_windows_recent_or_sole_userdata_without_authority_refused(self):
        self.write(self.users, self.users.read_bytes().replace(b' "AutoLogin" "1"', b''))
        with self.assertRaises(steam.SteamError):
            steam.discover(self.archive, platform='win32', windows_registry=lambda:
                           {'SteamPath': str(self.root), 'ActiveUser': 0})

    def test_wrong_library_or_app_manifest_refused(self):
        outside = self.base / 'unmanaged/resources/app.asar'
        with self.assertRaises(steam.SteamError):
            steam.discover(outside, **self.kwargs())
        manifest = self.root / 'steamapps/appmanifest_940230.acf'
        self.write(manifest, manifest.read_bytes().replace(b'940230', b'123456'))
        with self.assertRaises(steam.SteamError):
            steam.discover(self.archive, **self.kwargs())

    def test_external_library_supported_with_manifest_proof(self):
        external = self.base / 'External'
        manifest = self.root / 'steamapps/appmanifest_940230.acf'
        self.write(external / manifest.relative_to(self.root), manifest.read_bytes())
        archive = external / self.archive.relative_to(self.root)
        self.write(archive, b'fixture archive')
        self.write(self.root / 'steamapps/libraryfolders.vdf', b'"libraryfolders" { "1" { "path" ' + steam._encode(str(external)) + b' } }')
        self.assertEqual(steam.discover(archive, **self.kwargs()).root, self.root)

    def test_symlink_configuration_refused(self):
        original = self.config.read_bytes()
        target = self.base / 'real.vdf'
        target.write_bytes(original)
        self.config.unlink()
        try:
            self.config.symlink_to(target)
        except OSError as error:
            if os.name == 'nt' and getattr(error, 'winerror', None) == 1314:
                self.skipTest('Windows token lacks symbolic-link creation privilege')
            raise
        with self.assertRaises(steam.SteamError):
            steam.discover(self.archive, **self.kwargs())


class ApplyTests(Fixture):
    def test_plan_has_no_writes_or_shutdown(self):
        before = self.config.read_bytes()
        prepared = steam.plan(self.archive, self.state, LINE, **self.kwargs())
        self.assertFalse(self.state.exists())
        self.assertEqual(self.config.read_bytes(), before)
        self.assertIn('%command% --existing', prepared.record['current'])

    def test_backup_private_and_one_field_updated_mode_preserved(self):
        before = self.config.read_bytes()
        self.config.chmod(0o640)
        result = self.configure()
        self.assertTrue(result.changed)
        self.assertEqual(self.value(), LINE + ' --existing "space argument"')
        backup = self.state / self.record()['backup']
        self.assertEqual(backup.read_bytes(), before)
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o640)
        self.assertEqual(self.record()['phase'], 'complete')

    def test_idempotent_across_mods_and_new_source_prefix(self):
        self.configure()
        before = self.config.read_bytes()
        self.assertFalse(self.configure().changed)
        self.assertEqual(self.config.read_bytes(), before)
        next_line = LINE.replace('/safe/python', '/new/python')
        self.configure(next_line)
        self.assertEqual(self.value(), next_line + ' --existing "space argument"')
        self.assertNotIn('/safe/python', self.value())
        self.assertEqual(self.record()['original_value'], '--existing "space argument"')

    def test_adopt_exact_manual_line_or_recorded_old_prefix(self):
        self.write(self.config, local(LINE + ' --oldflag "two words"'))
        self.configure()
        self.assertEqual(self.value().count('%command%'), 1)
        self.assertEqual(self.record()['original_value'], '--oldflag "two words"')
        self.remove()
        self.assertEqual(self.value(), '--oldflag "two words"')

    def test_adopt_owned_line_after_python_changed(self):
        old = LINE.replace('/safe/python', '/previous/python')
        self.write(self.config, local(old + ' --oldflag'))
        self.configure(owned_lines=(old,))
        self.assertEqual(self.value(), LINE + ' --oldflag')

    def test_unknown_wrapper_shell_or_placeholder_refused_without_changes(self):
        for value in ('wrapper %command%', '-x %command%', '-x; unsafe', 'ENV=x game', '-x "unfinished'):
            self.write(self.config, local(value))
            before = self.config.read_bytes()
            with self.assertRaises(steam.SteamError):
                self.configure()
            self.assertEqual(self.config.read_bytes(), before)
            self.assertFalse(self.state.exists())

    def test_graceful_shutdown_and_restart_only_when_was_running(self):
        process = FakeProcess(True)
        self.configure(consent=True, process=process)
        self.assertEqual(process.calls.count('shutdown'), 1)
        self.assertEqual(process.calls.count('restart'), 1)
        stopped = FakeProcess()
        self.configure(process=stopped)
        self.assertNotIn('shutdown', stopped.calls)
        self.assertNotIn('restart', stopped.calls)

    def test_missing_consent_or_shutdown_timeout_no_config_edit(self):
        before = self.config.read_bytes()
        with self.assertRaises(steam.SteamError):
            self.configure(process=FakeProcess(True))
        process = FakeProcess(True, close=False)
        prepared = steam.plan(self.archive, self.state, LINE, **self.kwargs())
        with self.assertRaises(steam.SteamError):
            steam.apply(prepared, consent=True, process=process, private=self.private, timeout=0)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertNotIn('restart', process.calls)

    def test_clean_shutdown_flush_preserved_if_owned_field_unchanged(self):
        def flush():
            self.write(self.config, self.config.read_bytes().replace(b'keep me', b'Steam flushed this'))
        self.configure(consent=True, process=FakeProcess(True, on_shutdown=flush))
        self.assertIn(b'Steam flushed this', self.config.read_bytes())
        self.assertEqual(self.value(), LINE + ' --existing "space argument"')

    def test_windows_exit_clears_activeuser_and_no_recent_but_same_autologin(self):
        self.write(self.users, self.users.read_bytes().replace(b' "MostRecent" "1"', b''))
        registry = {'SteamPath': str(self.root), 'ActiveUser': 123, 'AutoLoginUser': 'fixture'}
        prepared = steam.plan(self.archive, self.state, LINE, platform='win32',
                              windows_registry=lambda: dict(registry))
        def exit_and_flush():
            registry['ActiveUser'] = 0
            self.write(self.config, self.config.read_bytes().replace(b'keep me', b'flushed unrelated'))
        steam.apply(prepared, consent=True, private=self.private,
                    process=FakeProcess(True, on_shutdown=exit_and_flush))
        self.assertEqual(self.value(), LINE + ' --existing "space argument"')
        self.assertIn(b'flushed unrelated', self.config.read_bytes())

    def test_windows_exit_account_switch_refused(self):
        registry = {'SteamPath': str(self.root), 'ActiveUser': 123, 'AutoLoginUser': 'fixture'}
        prepared = steam.plan(self.archive, self.state, LINE, platform='win32',
                              windows_registry=lambda: dict(registry))
        before = self.config.read_bytes()
        def switch_account():
            registry['ActiveUser'] = 0
            registry['AutoLoginUser'] = 'another-account'
        with self.assertRaises(steam.SteamError):
            steam.apply(prepared, consent=True, private=self.private,
                        process=FakeProcess(True, on_shutdown=switch_account))
        self.assertEqual(self.config.read_bytes(), before)

    def test_launch_field_changed_during_shutdown_refused_and_restart(self):
        process = FakeProcess(True, on_shutdown=lambda: self.write(self.config, local('--new-user-choice')))
        with self.assertRaises(steam.SteamError):
            self.configure(consent=True, process=process)
        self.assertEqual(self.value(), '--new-user-choice')
        self.assertIn('restart', process.calls)

    def test_account_change_after_plan_refused(self):
        prepared = steam.plan(self.archive, self.state, LINE, **self.kwargs())
        self.write(self.root / 'registry.vdf', b'"HKCU" { "Software" { "Valve" { "Steam" { "AutoLoginUser" "other" } } } }')
        with self.assertRaises(steam.SteamError):
            steam.apply(prepared, private=self.private, process=FakeProcess())

    def test_concurrent_config_change_after_plan_refused(self):
        prepared = steam.plan(self.archive, self.state, LINE, **self.kwargs())
        self.write(self.config, local('--concurrent'))
        with self.assertRaises(steam.SteamError):
            steam.apply(prepared, private=self.private, process=FakeProcess())
        self.assertEqual(self.value(), '--concurrent')

    def test_restart_just_before_replace_refused(self):
        process = FakeProcess()
        real_atomic = steam._atomic
        def race(path, data, *args, **kwargs):
            if path == self.config:
                process.running = True
            return real_atomic(path, data, *args, **kwargs)
        before = self.config.read_bytes()
        with mock.patch.object(steam, '_atomic', side_effect=race):
            with self.assertRaises(steam.SteamError):
                self.configure(process=process)
        self.assertEqual(self.config.read_bytes(), before)
        self.assertEqual(self.record()['phase'], 'prepared')

    def test_interrupted_after_replace_can_resume_from_prepared_journal(self):
        original = steam._save_record
        def crash(state, record):
            if record['phase'] == 'complete':
                raise OSError('simulated interruption')
            original(state, record)
        with mock.patch.object(steam, '_save_record', side_effect=crash):
            with self.assertRaises(OSError):
                self.configure()
        self.assertEqual(self.record()['phase'], 'prepared')
        self.assertFalse(self.configure().changed)
        self.assertEqual(self.record()['phase'], 'complete')

    def test_restore_absent_empty_and_flags_fields(self):
        for original in (None, '', '--keep "quoted"'):
            with self.subTest(original=original):
                self.state = self.base / ('state-' + str(len(str(original))))
                self.write(self.config, local(original))
                self.configure()
                result = self.remove()
                self.assertTrue(result.restored)
                self.assertEqual(self.value(), original)
                self.assertFalse(self.remove().changed)

    def test_removal_preserves_unrelated_changes_and_external_launch_edits(self):
        self.configure()
        self.write(self.config, self.config.read_bytes().replace(b'keep me', b'new unrelated choice'))
        self.remove()
        self.assertIn(b'new unrelated choice', self.config.read_bytes())
        self.configure()
        self.write(self.config, steam.replace_launch_options(self.config.read_bytes(), '--external-choice'))
        before = self.config.read_bytes()
        result = self.remove()
        self.assertFalse(result.restored)
        self.assertEqual(self.config.read_bytes(), before)

    def test_interrupted_removal_before_replace_resumes(self):
        self.configure()
        original = steam._atomic
        def crash(path, *args, **kwargs):
            if path == self.config:
                raise OSError('simulated before replace')
            return original(path, *args, **kwargs)
        with mock.patch.object(steam, '_atomic', side_effect=crash):
            with self.assertRaises(OSError):
                self.remove()
        self.assertEqual(self.record()['operation'], 'remove')
        self.assertEqual(self.record()['phase'], 'prepared')
        self.assertTrue(self.remove().restored)
        self.assertEqual(self.value(), '--existing "space argument"')

    def test_interrupted_removal_after_replace_resumes_for_absent_field(self):
        self.write(self.config, local(None))
        self.configure()
        original = steam._save_record
        def crash(state, record):
            if record['phase'] == 'complete' and record['operation'] == 'remove':
                raise OSError('simulated after replace')
            original(state, record)
        with mock.patch.object(steam, '_save_record', side_effect=crash):
            with self.assertRaises(OSError):
                self.remove()
        self.assertIsNone(self.value())
        self.assertEqual(self.record()['phase'], 'prepared')
        self.assertTrue(self.remove().restored)
        self.assertIsNone(self.value())
        self.assertEqual(self.record()['phase'], 'removed')

    def test_backup_and_prepared_journal_exist_before_config_replace(self):
        original = steam._atomic
        before = self.config.read_bytes()
        checked = []
        def verify_order(path, *args, **kwargs):
            if path == self.config:
                record = self.record()
                self.assertEqual(record['phase'], 'prepared')
                self.assertEqual((self.state / record['backup']).read_bytes(), before)
                checked.append(True)
            return original(path, *args, **kwargs)
        with mock.patch.object(steam, '_atomic', side_effect=verify_order):
            self.configure()
        self.assertEqual(checked, [True])

    def test_windows_replace_uses_strict_acl_preserving_flags(self):
        import ctypes
        function = mock.Mock(return_value=True)
        kernel = mock.Mock(ReplaceFileW=function)
        with mock.patch.object(ctypes, 'WinDLL', return_value=kernel, create=True):
            steam._replace_windows(self.config, self.base / 'temporary')
        self.assertEqual(function.call_args.args,
                         (str(self.config), str(self.base / 'temporary'), None, 0, None, None))

    def test_windows_temporary_copies_dacl_before_populating_file(self):
        import ctypes
        def get_security(source, flags, buffer, capacity, needed):
            self.assertEqual(flags, 4)
            ctypes.cast(needed, ctypes.POINTER(ctypes.c_ulong))[0] = 32
            return bool(buffer is not None)
        get = mock.Mock(side_effect=get_security)
        set_security = mock.Mock(return_value=True)
        advapi = mock.Mock(GetFileSecurityW=get, SetFileSecurityW=set_security)
        with mock.patch.object(ctypes, 'WinDLL', return_value=advapi, create=True):
            steam._copy_windows_dacl(self.config, self.base / 'temporary')
        self.assertEqual(get.call_count, 2)
        self.assertEqual(set_security.call_args.args[:2], (str(self.base / 'temporary'), 0x80000004))


if __name__ == '__main__':
    unittest.main()
