"""Synthetic end-to-end installer tests. Never discover or modify a real game."""
import contextlib
import copy
import io
import json
import hashlib
import os
from pathlib import Path
import shutil
import struct
import tempfile
import unittest
from unittest import mock
import zlib

import core
import install as installer
import registration


def png():
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) + chunk(b'IDAT', zlib.compress(b'\0\0\0\0')) + chunk(b'IEND', b'')


def section(kind, version):
    return {'prefix': '/* __geoTransport %s %s */\n' % (kind, version),
            'suffix': '\n/* __transportAutobuildUI %s %s */' % (kind, version),
            'patches': [[kind + '_ANCHOR', kind + '_OWN_' + version + '(' + kind + '_ANCHOR)']]}


def archive_bytes(app, sim, unpacked=True, version='1.1.3'):
    packed = bytearray()
    header = {'files': {}}
    values = {'package.json': json.dumps({'name': 'geoplanetical', 'version': version,
              'steam': {'appId': 940230}}).encode(),
              installer.APP_SCRIPT: app, installer.SIM_SCRIPT: sim,
              'assets/foreign-mod.bin': b'KEEP FOREIGN ASSET'}
    for name, data in values.items():
        tree = header
        parts = name.split('/')
        for part in parts[:-1]:
            tree = tree['files'].setdefault(part, {'files': {}})
        value = {'size': len(data), 'integrity': core.integrity(data)}
        if name == installer.SIM_SCRIPT and unpacked:
            value['unpacked'] = True
        else:
            value['offset'] = str(len(packed)); packed.extend(data)
        tree['files'][parts[-1]] = value
    return core._encode_header(header) + packed


def snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.package = self.root / 'package'; self.package.mkdir()
        self.profile = self.root / 'profile'; self.profile.mkdir()
        (self.profile / 'saves').mkdir(); (self.profile / 'saves/campaign').write_bytes(b'KEEP SAVE')
        manifest = {'id': registration.MOD_ID, 'name': 'Transport Autopilot', 'version': '1.0.0',
                    'contentApi': 1, 'tier': 'presentation', 'thumbnail': 'thumbnail.png',
                    'requires': [], 'provides': {}, 'description': 'Owned transport automation', 'authors': ['Little Guy Dev']}
        (self.package / 'mod.json').write_text(json.dumps(manifest))
        (self.package / 'thumbnail.png').write_bytes(png())
        self.payload = {'schema': 1, 'id': registration.MOD_ID, 'version': '1.0.0', 'gameVersions': ['1.1.3'],
                        'app': section('APP', 'current'), 'sim': section('SIM', 'current')}
        self.old = {'labels': ['0.9.0'], 'app': section('APP', 'old'), 'sim': section('SIM', 'old')}
        (self.package / 'transport-payload.json').write_text(json.dumps(self.payload))
        (self.package / 'legacy_payloads.json').write_text(json.dumps({'schema': 1, 'payloads': [self.old]}))
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(mock.patch.object(installer.platform, 'game_running', return_value=False))
        self.stack.enter_context(mock.patch.object(registration, 'steam_libraries', return_value=[]))
        self.stack.enter_context(mock.patch.object(installer.platform, 'macos_integrity_changes', return_value={}))
        self.stack.enter_context(mock.patch.object(installer.platform, 'windows_integrity_changes', return_value={}))
        self.stack.enter_context(mock.patch.object(installer.platform, 'resign_macos'))
        def copy_app(args, **kwargs):
            self.assertEqual(args[0], '/usr/bin/ditto')
            shutil.copytree(args[1], args[2])
            return mock.Mock(returncode=0)
        self.stack.enter_context(mock.patch.object(installer.subprocess, 'run', side_effect=copy_app))

    def tearDown(self):
        self.stack.close(); self.temp.cleanup()

    def make_game(self, mac=False, unpacked=True, version='1.1.3', app=None, sim=None):
        game = self.root / 'game'
        if mac:
            self.app = game / 'Geoplanetical.app'
            self.archive = self.app / 'Contents/Resources/app.asar'
            self.archive.parent.mkdir(parents=True)
            (self.app / 'Contents/Info.plist').write_bytes(b'KEEP PLIST')
            (self.app / 'foreign-resource').write_bytes(b'KEEP RESOURCE')
        else:
            self.app = None
            self.archive = game / 'resources/app.asar'; self.archive.parent.mkdir(parents=True)
            (game / 'Geoplanetical.exe').write_bytes(b'KEEP EXE')
        self.base_app = app or b'FOREIGN_PREFIX; APP_ANCHOR; FOREIGN_SUFFIX'
        self.base_sim = sim or b'FOREIGN_PREFIX; SIM_ANCHOR; FOREIGN_SUFFIX'
        self.archive.write_bytes(archive_bytes(self.base_app, self.base_sim, unpacked, version))
        if unpacked:
            path = Path(str(self.archive) + '.unpacked') / installer.SIM_SCRIPT
            path.parent.mkdir(parents=True); path.write_bytes(self.base_sim)
        return game

    def plan(self, action='install'):
        return installer.plan(self.archive, action, self.package, self.profile)

    def assert_scripts(self, app, sim):
        parsed = core.AsarArchive(self.archive.read_bytes())
        self.assertEqual(parsed.read(installer.APP_SCRIPT, Path(str(self.archive) + '.unpacked')), app)
        self.assertEqual(parsed.read(installer.SIM_SCRIPT, Path(str(self.archive) + '.unpacked')), sim)
        self.assertEqual(parsed.read('assets/foreign-mod.bin'), b'KEEP FOREIGN ASSET')

    def assert_originals(self, before):
        for path, data in before.items():
            self.assertTrue(path.exists(), str(path)); self.assertEqual(path.read_bytes(), data, str(path))
        self.assertFalse((self.profile / 'mods/transport-autopilot/mod.json').exists())
        self.assertEqual((self.profile / 'saves/campaign').read_bytes(), b'KEEP SAVE')

    def relevant_before(self):
        return {p: p.read_bytes() for p in (self.archive.parent.parent.rglob('*') if not self.app else self.app.rglob('*')) if p.is_file()}

    def test_yes_uses_one_locked_plan_for_install_noop_and_uninstall(self):
        self.make_game()
        real_plan, real_exclusive = installer.plan, installer.exclusive
        locked = False
        phases = []
        planned = []
        @contextlib.contextmanager
        def lock(state):
            nonlocal locked
            with real_exclusive(state):
                locked = True
                try:
                    yield
                finally:
                    locked = False
        def plan(archive, action, **kwargs):
            phases.append(locked)
            result = real_plan(archive, action, self.package, **kwargs)
            planned.append(result)
            return result
        with mock.patch.object(installer, 'plan', side_effect=plan), mock.patch.object(installer, 'exclusive', lock), mock.patch.object(installer, 'PACKAGE', self.package), contextlib.redirect_stdout(io.StringIO()) as output:
            args = ['--game', str(self.archive), '--profile', str(self.profile), '--yes']
            self.assertEqual(installer.main(args), 0)
            installed = self.relevant_before()
            self.assertEqual(installer.main(args), 0)
            self.assertEqual(self.relevant_before(), installed)
            self.assertEqual(installer.main(['uninstall'] + args), 0)
        self.assertEqual(phases, [True, True, True])
        self.assertFalse(planned[1]['changes'])
        self.assertFalse(planned[1]['metadata'])
        self.assertIn('Game: Geoplanetical 1.1.3', output.getvalue())
        self.assertIn('is already installed', output.getvalue())
        self.assert_scripts(self.base_app, self.base_sim)

    def test_interactive_replans_changed_game_after_preview(self):
        self.make_game()
        real_plan = installer.plan
        plans = []
        external_app = self.base_app + b';EXTERNAL AFTER PREVIEW'
        def plan(archive, action, **kwargs):
            result = real_plan(archive, action, self.package, **kwargs)
            plans.append(result)
            return result
        def answer(prompt):
            self.assertEqual(len(plans), 1)
            self.archive.write_bytes(archive_bytes(external_app, self.base_sim))
            return ''
        with mock.patch.object(installer, 'plan', side_effect=plan), mock.patch.object(installer, 'PACKAGE', self.package), mock.patch.object(installer.sys.stdin, 'isatty', return_value=True), mock.patch('builtins.input', side_effect=answer), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(installer.main(['--game', str(self.archive), '--profile', str(self.profile)]), 0)
        self.assertEqual(len(plans), 2)
        self.assertNotEqual(plans[0]['expected'][str(self.archive)], plans[1]['expected'][str(self.archive)])
        self.assert_scripts(core.apply_section(external_app, self.payload['app']), core.apply_section(self.base_sim, self.payload['sim']))

    def test_interactive_rejects_unsupported_change_after_preview(self):
        self.make_game()
        real_plan = installer.plan
        def answer(prompt):
            self.archive.write_bytes(archive_bytes(self.base_app, self.base_sim, version='9.9.9'))
            return ''
        with mock.patch.object(installer, 'plan', side_effect=lambda archive, action, **kw: real_plan(archive, action, self.package, **kw)) as planning, mock.patch.object(installer, 'PACKAGE', self.package), mock.patch.object(installer.sys.stdin, 'isatty', return_value=True), mock.patch('builtins.input', side_effect=answer), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(core.InstallError, 'supports Geoplanetical'):
                installer.main(['--game', str(self.archive), '--profile', str(self.profile)])
        self.assertEqual(planning.call_count, 2)
        self.assertEqual(self.archive.read_bytes(), archive_bytes(self.base_app, self.base_sim, version='9.9.9'))
        self.assertFalse((self.profile / 'mods').exists())

    def test_each_unchanged_script_keeps_index_integrity_and_storage(self):
        for unchanged_key, unpacked in (('sim', True), ('sim', False), ('app', True), ('app', False)):
            with self.subTest(unchanged=unchanged_key, unpacked=unpacked):
                # Each case has a separate game/profile so previous registrations
                # cannot obscure whether this plan actually rewrites a script.
                case = self.root / (unchanged_key + str(unpacked)); case.mkdir()
                original_root, original_profile = self.root, self.profile
                self.root = case; self.profile = case / 'profile'; self.profile.mkdir()
                self.payload[unchanged_key] = {'prefix': '', 'suffix': '', 'patches': []}
                other_key = 'app' if unchanged_key == 'sim' else 'sim'
                self.payload[other_key] = section(other_key.upper(), 'current')
                payload_path = next(self.package.glob('*-payload.json'))
                payload_path.write_text(json.dumps(self.payload))
                self.make_game(unpacked=unpacked)
                before = core.AsarArchive.from_file(self.archive)
                name = installer.SIM_SCRIPT if unchanged_key == 'sim' else installer.APP_SCRIPT
                entry = copy.deepcopy(before.entry(name))
                real_replace = core.AsarArchive.plan_replace
                calls = []
                def replace(source, packed, unpacked_data=None):
                    calls.append((dict(packed), dict(unpacked_data or {})))
                    return real_replace(source, packed, unpacked_data)
                with mock.patch.object(core.AsarArchive, 'plan_replace', replace):
                    result = self.plan()
                self.assertEqual(len(calls), 1)
                self.assertNotIn(name, calls[0][0]); self.assertNotIn(name, calls[0][1])
                edit = result['changes'][self.archive]
                node = edit.header
                for part in name.split('/'):
                    node = node['files'][part]
                self.assertEqual(node, entry)
                self.assertEqual(len(edit.appends), 0 if unchanged_key == 'app' and unpacked else 1)
                worker = Path(str(self.archive) + '.unpacked') / installer.SIM_SCRIPT
                if unchanged_key == 'sim' and unpacked:
                    self.assertNotIn(worker, result['changes'])
                    worker_stat = worker.stat()
                installer.commit(result, self.profile)
                self.assert_scripts(core.apply_section(self.base_app, self.payload['app']), core.apply_section(self.base_sim, self.payload['sim']))
                self.assertEqual(core.AsarArchive.from_file(self.archive).entry(name), entry)
                if unchanged_key == 'sim' and unpacked:
                    self.assertEqual(worker.stat().st_mtime_ns, worker_stat.st_mtime_ns)
                self.root, self.profile = original_root, original_profile

    def test_mac_materializes_directly_with_modes_and_snapshot_archive_hash(self):
        self.make_game(mac=True)
        self.archive.chmod(0o640)
        worker = Path(str(self.archive) + '.unpacked') / installer.SIM_SCRIPT
        worker.chmod(0o604)
        # Windows normalizes POSIX permission bits; preserve the actual modes.
        archive_mode = self.archive.stat().st_mode & 0o777
        worker_mode = worker.stat().st_mode & 0o777
        result = self.plan()
        real_write, real_atomic = core.ArchiveEdit.write_to, installer.atomic_write
        written, markers = [], []
        def write(edit, path):
            written.append(Path(path))
            return real_write(edit, path)
        def atomic(path, data, mode=None):
            if Path(path).name == 'mac-pending.json':
                markers.append(json.loads(data))
            return real_atomic(path, data, mode)
        def sign(staged):
            self.assertEqual((staged / 'Contents/Resources/app.asar').stat().st_mode & 0o777, archive_mode)
            self.assertEqual((staged / 'Contents/Resources/app.asar.unpacked' / installer.SIM_SCRIPT).stat().st_mode & 0o777, worker_mode)
        with mock.patch.object(core.ArchiveEdit, 'write_to', write), mock.patch.object(installer, 'atomic_write', side_effect=atomic), mock.patch.object(installer.platform, 'resign_macos', side_effect=sign), mock.patch.object(installer, 'verify_game', wraps=installer.verify_game) as verification, mock.patch.object(installer, 'digest', wraps=installer.digest) as hashing:
            installer.commit(result, self.profile)
        self.assertEqual(len(written), 1)
        self.assertEqual(written[0].name, 'app.asar')
        self.assertEqual(written[0].parents[2].name, 'staged.app')
        self.assertEqual(verification.call_count, 2)
        self.assertFalse(any('staged.app' in Path(call.args[0]).parts for call in hashing.call_args_list))
        self.assertFalse(list(installer.state_root(self.archive).rglob('prepared.asar')))
        self.assertEqual(markers[0]['afterArchive'], markers[0]['afterTree']['Contents/Resources/app.asar'][2])
        self.assertEqual(self.archive.stat().st_mode & 0o777, archive_mode)
        self.assertEqual(worker.stat().st_mode & 0o777, worker_mode)

    def test_mac_materialization_failure_leaves_originals_unchanged(self):
        self.make_game(mac=True); before = self.relevant_before(); result = self.plan()
        def fail(edit, path):
            Path(path).write_bytes(b'INCOMPLETE STAGED OUTPUT')
            raise OSError('injected materialization failure')
        with mock.patch.object(core.ArchiveEdit, 'write_to', fail):
            with self.assertRaisesRegex(OSError, 'materialization'):
                installer.commit(result, self.profile)
        self.assert_originals(before)
        self.assertFalse(list(installer.state_root(self.archive).glob('operations/*/mac-pending.json')))

    def test_mac_staged_verification_failure_never_swaps_original(self):
        self.make_game(mac=True); before = self.relevant_before(); result = self.plan()
        with mock.patch.object(installer, 'verify_game', side_effect=core.InstallError('injected staged verification failure')):
            with self.assertRaisesRegex(core.InstallError, 'staged verification'):
                installer.commit(result, self.profile)
        self.assert_originals(before)
        self.assertFalse(list(installer.state_root(self.archive).glob('operations/*/mac-pending.json')))

    def test_windows_install_idempotent_uninstall_native_registration(self):
        self.make_game(); before = self.relevant_before()
        result = self.plan(); self.assertEqual(result['version'], '1.0.0')
        self.assertTrue(installer.commit(result, self.profile))
        self.assert_scripts(core.apply_section(self.base_app, self.payload['app']), core.apply_section(self.base_sim, self.payload['sim']))
        native = self.profile / 'mods/transport-autopilot'
        self.assertEqual(set(p.name for p in native.iterdir()), set(registration.FILES))
        self.assertFalse(installer.commit(self.plan(), self.profile))
        self.assertTrue(installer.commit(self.plan('uninstall'), self.profile))
        self.assert_scripts(self.base_app, self.base_sim)
        self.assert_originals({p: b for p, b in before.items() if p != self.archive})
        self.assertFalse(installer.commit(self.plan('uninstall'), self.profile))

    def test_mac_install_and_uninstall_preserve_other_files(self):
        self.make_game(mac=True); before = self.relevant_before()
        self.assertTrue(installer.commit(self.plan(), self.profile))
        self.assertTrue(installer.commit(self.plan('uninstall'), self.profile))
        self.assert_scripts(self.base_app, self.base_sim)
        self.assert_originals({p: b for p, b in before.items() if p != self.archive})

    def test_packed_worker_supported(self):
        self.make_game(unpacked=False)
        installer.commit(self.plan(), self.profile)
        self.assert_scripts(core.apply_section(self.base_app, self.payload['app']), core.apply_section(self.base_sim, self.payload['sim']))

    def test_old_release_transition_keeps_foreign_wrappers(self):
        a = b'FOREIGN OUTER;' + core.apply_section(b'APP_ANCHOR', self.old['app']) + b';FOREIGN TAIL'
        s = b'FOREIGN OUTER;' + core.apply_section(b'SIM_ANCHOR', self.old['sim']) + b';FOREIGN TAIL'
        self.make_game(app=a, sim=s)
        result = self.plan(); self.assertEqual(result['detected']['labels'], ['0.9.0'])
        installer.commit(result, self.profile)
        self.assert_scripts(core.transition_section(a, self.old['app'], self.payload['app']), core.transition_section(s, self.old['sim'], self.payload['sim']))

    def assert_relabelled_release_transition(self, old_version, target_version):
        manifest_path = self.package / 'mod.json'
        manifest = json.loads(manifest_path.read_text())
        manifest['version'] = target_version
        manifest_path.write_text(json.dumps(manifest))
        self.payload['version'] = target_version
        payload_name = 'transport-payload.json'
        (self.package / payload_name).write_text(json.dumps(self.payload))
        self.old['labels'] = [old_version]
        (self.package / 'legacy_payloads.json').write_text(json.dumps({'schema': 1, 'payloads': [self.old]}))
        base_app, base_sim = b'FOREIGN_PREFIX; APP_ANCHOR; FOREIGN_SUFFIX', b'FOREIGN_PREFIX; SIM_ANCHOR; FOREIGN_SUFFIX'
        self.make_game(app=core.apply_section(base_app, self.old['app']),
                       sim=core.apply_section(base_sim, self.old['sim']))
        result = self.plan()
        self.assertEqual(installer.detected_version(result['detected']), old_version)
        self.assertEqual(result['version'], target_version)
        installer.commit(result, self.profile)
        self.assert_scripts(core.apply_section(base_app, self.payload['app']), core.apply_section(base_sim, self.payload['sim']))
        self.assertEqual(json.loads((installer.state_root(self.archive) / 'installed.json').read_text())['version'], target_version)
        self.assertFalse(installer.commit(self.plan(), self.profile))
        installer.commit(self.plan('uninstall'), self.profile)
        self.assert_scripts(base_app, base_sim)
        self.assertEqual((self.profile / 'saves/campaign').read_bytes(), b'KEEP SAVE')

    def test_release_number_reset_upgrades_and_removes_private_candidate(self):
        self.assert_relabelled_release_transition('1.1.1', '1.1.0')

    def test_reused_release_number_still_upgrades_exact_older_shape(self):
        self.assert_relabelled_release_transition('1.1.0', '1.1.0')

    def test_mixed_release_rejected_without_writes(self):
        self.make_game(app=core.apply_section(b'APP_ANCHOR', self.old['app']), sim=core.apply_section(b'SIM_ANCHOR', self.payload['sim']))
        before = snapshot(self.root)
        with self.assertRaises(core.InstallError): self.plan()
        self.assertEqual(snapshot(self.root), before)

    def test_unsupported_game_rejected_without_writes(self):
        self.make_game(version='1.1.2'); before = snapshot(self.root)
        with self.assertRaises(core.InstallError): self.plan()
        self.assertEqual(snapshot(self.root), before)

    def test_foreign_change_after_plan_rejected(self):
        self.make_game(); result = self.plan()
        self.archive.write_bytes(b'EXTERNAL UPDATE')
        with self.assertRaises(core.InstallError): installer.commit(result, self.profile)
        self.assertEqual(self.archive.read_bytes(), b'EXTERNAL UPDATE')
        self.assertFalse((self.profile / 'mods').exists())

    def test_windows_failure_after_metadata_write_rolls_back_all_files(self):
        self.make_game(); before = self.relevant_before(); result = self.plan()
        real = installer.verify_game
        def fail_after_apply(*args):
            real(*args)
            self.assertTrue((self.profile / 'mods/transport-autopilot/mod.json').exists())
            raise OSError('injected final verification failure')
        with mock.patch.object(installer, 'verify_game', side_effect=fail_after_apply):
            with self.assertRaisesRegex(OSError, 'injected'): installer.commit(result, self.profile)
        self.assert_originals(before)
        self.assertFalse(list(installer.state_root(self.archive).glob('operations/*/files/pending.json')))

    def test_mac_metadata_failure_restores_app_and_registration(self):
        self.make_game(mac=True); before = self.relevant_before(); result = self.plan()
        real = core.atomic_write; failed = False
        def fault(path, data, mode=None, **kwargs):
            nonlocal failed
            if Path(path) == self.profile / 'mods/transport-autopilot/thumbnail.png' and not failed:
                failed = True; raise OSError('injected metadata failure')
            return real(path, data, mode, **kwargs)
        with mock.patch.object(core, 'atomic_write', side_effect=fault):
            with self.assertRaisesRegex(OSError, 'injected'): installer.commit(result, self.profile)
        self.assert_originals(before)
        self.assertFalse(list(installer.state_root(self.archive).glob('operations/*/mac-pending.json')))

    def test_mac_swap_failure_restores_original_app(self):
        self.make_game(mac=True); before = self.relevant_before(); result = self.plan()
        real = installer.os.replace; failed = False
        def fault(src, dst):
            nonlocal failed
            if Path(src).name == 'staged.app' and not failed:
                failed = True; raise OSError('injected swap failure')
            return real(src, dst)
        with mock.patch.object(installer.os, 'replace', side_effect=fault):
            with self.assertRaisesRegex(OSError, 'injected'): installer.commit(result, self.profile)
        self.assert_originals(before)

    def test_windows_crash_after_journal_is_recovered_through_orchestrator(self):
        self.make_game(); before = self.relevant_before(); result = self.plan()
        real = core.atomic_write
        def fault(path, data, mode=None, **kwargs):
            answer = real(path, data, mode)
            if Path(path).name == 'pending.json': raise OSError('crash after journal')
            return answer
        with mock.patch.object(core, 'atomic_write', side_effect=fault):
            with self.assertRaisesRegex(OSError, 'crash after journal'): installer.commit(result, self.profile)
        self.assertTrue(list(installer.state_root(self.archive).glob('operations/*/files/pending.json')))
        installer.recover_operations(self.archive, self.profile)
        self.assert_originals(before)
        self.assertFalse(list(installer.state_root(self.archive).glob('operations/*/files/pending.json')))

    def test_mac_crash_after_both_renames_recovers_before_metadata(self):
        self.make_game(mac=True); before = self.relevant_before(); result = self.plan()
        real = installer.os.replace
        def fault(src, dst):
            answer = real(src, dst)
            if Path(src).name == 'staged.app': raise OSError('crash after swap')
            return answer
        with mock.patch.object(installer.os, 'replace', side_effect=fault), mock.patch.object(installer, 'recover_operations'):
            with self.assertRaisesRegex(OSError, 'crash after swap'): installer.commit(result, self.profile)
        self.assertNotEqual(self.archive.read_bytes(), before[self.archive])
        installer.recover_operations(self.archive, self.profile)
        self.assert_originals(before)

    def interrupted_mac(self, after_metadata=False):
        self.make_game(mac=True); before = self.relevant_before(); result = self.plan()
        if after_metadata:
            real = Path.unlink
            def fault(path, *args, **kwargs):
                if path.name == 'mac-pending.json': raise OSError('crash after metadata commit')
                return real(path, *args, **kwargs)
            with mock.patch.object(Path, 'unlink', fault), mock.patch.object(installer, 'recover_operations'):
                with self.assertRaisesRegex(OSError, 'crash after metadata commit'):
                    installer.commit(result, self.profile)
        else:
            real = installer.os.replace
            def fault(src, dst):
                answer = real(src, dst)
                if Path(src).name == 'staged.app': raise OSError('crash after swap')
                return answer
            with mock.patch.object(installer.os, 'replace', side_effect=fault), mock.patch.object(installer, 'recover_operations'):
                with self.assertRaisesRegex(OSError, 'crash after swap'):
                    installer.commit(result, self.profile)
        return before, next(installer.state_root(self.archive).glob('operations/*/mac-pending.json'))

    def test_mac_recovery_rejects_modified_unpacked_backup(self):
        before, marker = self.interrupted_mac()
        backup_worker = marker.parent / 'before.app/Contents/Resources/app.asar.unpacked' / installer.SIM_SCRIPT
        backup_worker.write_bytes(b'EXTERNAL BACKUP CHANGE')
        current = snapshot(self.app)
        with self.assertRaisesRegex(core.InstallError, 'backup changed'):
            installer.recover_operations(self.archive, self.profile)
        self.assertEqual(snapshot(self.app), current)
        self.assertEqual(backup_worker.read_bytes(), b'EXTERNAL BACKUP CHANGE')
        self.assertTrue(marker.exists())

    def test_mac_recovery_rejects_foreign_file_changed_after_swap(self):
        before, marker = self.interrupted_mac()
        foreign = self.app / 'foreign-resource'; foreign.write_bytes(b'EXTERNAL APP CHANGE')
        current = snapshot(self.app)
        with self.assertRaisesRegex(core.InstallError, 'game changed'):
            installer.recover_operations(self.archive, self.profile)
        self.assertEqual(snapshot(self.app), current)
        self.assertTrue(marker.exists())

    def test_mac_foreign_file_change_during_staging_is_preserved(self):
        self.make_game(mac=True); result = self.plan()
        def copy_and_change(args, **kwargs):
            shutil.copytree(args[1], args[2])
            (self.app / 'foreign-resource').write_bytes(b'EXTERNAL DURING STAGING')
            return mock.Mock(returncode=0)
        original_archive = self.archive.read_bytes()
        with mock.patch.object(installer.subprocess, 'run', side_effect=copy_and_change):
            with self.assertRaisesRegex(core.InstallError, 'changed while'):
                installer.commit(result, self.profile)
        self.assertEqual(self.archive.read_bytes(), original_archive)
        self.assertEqual((self.app / 'foreign-resource').read_bytes(), b'EXTERNAL DURING STAGING')
        self.assertFalse((self.profile / 'mods').exists())

    def test_mac_recovery_after_committed_metadata_restores_both_sides(self):
        before, marker = self.interrupted_mac(after_metadata=True)
        self.assertTrue((self.profile / 'mods/transport-autopilot/mod.json').exists())
        installer.recover_operations(self.archive, self.profile)
        self.assert_originals(before)
        self.assertFalse(marker.exists())

    def test_windows_integrity_file_joins_rollback_transaction(self):
        self.make_game(); before = self.relevant_before()
        exe = self.archive.parent.parent / 'Geoplanetical.exe'
        def integrity(archive, old, new):
            return {exe: b'UPDATED INTEGRITY ' + new.encode()} if old != new else {}
        with mock.patch.object(installer.platform, 'windows_integrity_changes', side_effect=integrity):
            result = self.plan(); self.assertIn(exe, result['changes'])
            real = installer.verify_game
            def fail(*args):
                real(*args)
                self.assertTrue(exe.read_bytes().startswith(b'UPDATED INTEGRITY'))
                raise OSError('post-integrity fault')
            with mock.patch.object(installer, 'verify_game', side_effect=fail):
                with self.assertRaisesRegex(OSError, 'post-integrity'):
                    installer.commit(result, self.profile)
        self.assert_originals(before)

    def test_main_recovers_missing_mac_app_after_first_rename(self):
        game = self.make_game(mac=True); result = self.plan()
        real = installer.os.replace
        def fault(src, dst):
            if Path(src).name == 'staged.app': raise OSError('crash before new app rename')
            return real(src, dst)
        with mock.patch.object(installer.os, 'replace', side_effect=fault), mock.patch.object(installer, 'recover_operations'):
            with self.assertRaisesRegex(OSError, 'crash before new app rename'):
                installer.commit(result, self.profile)
        self.assertFalse(self.app.exists())
        self.assertTrue(list(installer.state_root(self.archive).glob('operations/*/mac-pending.json')))
        plan = installer.plan
        with mock.patch.object(installer, 'plan', side_effect=lambda archive, action, **kw: plan(archive, action, self.package, **kw)), mock.patch.object(installer, 'PACKAGE', self.package), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(installer.main(['--game', str(game), '--profile', str(self.profile), '--yes']), 0)
        self.assertTrue(self.app.exists())
        self.assertIn('Recovered the previous game', out.getvalue())
        self.assert_scripts(core.apply_section(self.base_app, self.payload['app']), core.apply_section(self.base_sim, self.payload['sim']))
        self.assertFalse(list(installer.state_root(self.archive).glob('operations/*/mac-pending.json')))

    def test_malformed_mac_pending_record_raises_install_error_without_changes(self):
        before, marker = self.interrupted_mac()
        value = json.loads(marker.read_text()); del value['beforeTree']
        marker.write_text(json.dumps(value))
        current = snapshot(self.root)
        with self.assertRaises(core.InstallError):
            installer.recover_operations(self.archive, self.profile)
        self.assertEqual(snapshot(self.root), current)

    def test_plan_rejects_unpacked_worker_changed_during_registration_planning(self):
        self.make_game(); original_archive = self.archive.read_bytes()
        worker = Path(str(self.archive) + '.unpacked') / installer.SIM_SCRIPT
        real = installer.registration.plan_registration
        external = self.base_sim + b';EXTERNAL CONCURRENT CHANGE'
        def change_worker(*args, **kwargs):
            result = real(*args, **kwargs)
            worker.write_bytes(external)
            return result
        with mock.patch.object(installer.registration, 'plan_registration', side_effect=change_worker):
            with self.assertRaises(core.InstallError): self.plan()
        self.assertEqual(worker.read_bytes(), external)
        self.assertEqual(self.archive.read_bytes(), original_archive)
        self.assertFalse(installer.state_root(self.archive).exists())
        self.assertFalse((self.profile / '.transport-autopilot-installer/native-registration.json').exists())

    def test_plan_rejects_native_metadata_changed_after_ownership_check(self):
        self.make_game(); original_archive = self.archive.read_bytes()
        native = self.profile / 'mods/transport-autopilot/mod.json'
        external = b'EXTERNAL METADATA CREATED DURING PLANNING'
        real = installer.registration.plan_registration
        def change_metadata(*args, **kwargs):
            result = real(*args, **kwargs)
            native.parent.mkdir(parents=True)
            native.write_bytes(external)
            return result
        with mock.patch.object(installer.registration, 'plan_registration', side_effect=change_metadata):
            with self.assertRaises(core.InstallError): self.plan()
        self.assertEqual(native.read_bytes(), external)
        self.assertEqual(self.archive.read_bytes(), original_archive)
        self.assertFalse(installer.state_root(self.archive).exists())
        self.assertFalse((self.profile / '.transport-autopilot-installer/native-registration.json').exists())

    def test_current_windows_payload_still_checks_executable_integrity(self):
        self.make_game(app=core.apply_section(b'APP_ANCHOR', self.payload['app']),
                       sim=core.apply_section(b'SIM_ANCHOR', self.payload['sim']))
        before = snapshot(self.root)
        def corrupt_integrity(archive, old_hash, new_hash):
            self.assertEqual(archive, self.archive)
            self.assertEqual(old_hash, new_hash, 'Unchanged payload must still validate the existing header hash')
            raise core.InstallError('Executable embedded integrity does not match')
        with mock.patch.object(installer.platform, 'windows_integrity_changes', side_effect=corrupt_integrity) as validation:
            with self.assertRaisesRegex(core.InstallError, 'embedded integrity'):
                self.plan()
        validation.assert_called_once()
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(installer.state_root(self.archive).exists())

    def test_current_mac_payload_still_checks_plist_integrity(self):
        self.make_game(mac=True, app=core.apply_section(b'APP_ANCHOR', self.payload['app']),
                       sim=core.apply_section(b'SIM_ANCHOR', self.payload['sim']))
        before = snapshot(self.root)
        def corrupt_integrity(archive, old_hash, new_hash):
            self.assertEqual(archive, self.archive)
            self.assertEqual(old_hash, new_hash, 'Unchanged payload must still validate the existing header hash')
            raise core.InstallError('Info.plist embedded integrity does not match')
        with mock.patch.object(installer.platform, 'macos_integrity_changes', side_effect=corrupt_integrity) as validation:
            with self.assertRaisesRegex(core.InstallError, 'embedded integrity'):
                self.plan()
        validation.assert_called_once()
        self.assertEqual(snapshot(self.root), before)
        self.assertFalse(installer.state_root(self.archive).exists())

    def test_confirmation_cancel_leaves_no_files_or_directories(self):
        self.make_game(); before = snapshot(self.root); dirs = {str(p) for p in self.root.rglob('*') if p.is_dir()}
        plan = installer.plan
        with mock.patch.object(installer, 'plan', side_effect=lambda archive, action, **kw: plan(archive, action, self.package, **kw)), mock.patch.object(installer, 'PACKAGE', self.package), mock.patch.object(installer.sys.stdin, 'isatty', return_value=True), mock.patch('builtins.input', return_value='cancel'), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(installer.main(['--game', str(self.archive), '--profile', str(self.profile)]), 0)
        self.assertEqual(snapshot(self.root), before)
        self.assertEqual({str(p) for p in self.root.rglob('*') if p.is_dir()}, dirs)
        self.assertIn('Cancelled', out.getvalue())
        self.assertNotIn('GeoCore', out.getvalue())

    def test_cancel_with_pending_recovery_does_not_recover(self):
        before, marker = self.interrupted_mac()
        pending_state = snapshot(self.root)
        plan = installer.plan
        with mock.patch.object(installer, 'plan', side_effect=lambda archive, action, **kw: plan(archive, action, self.package, **kw)), mock.patch.object(installer, 'PACKAGE', self.package), mock.patch.object(installer.sys.stdin, 'isatty', return_value=True), mock.patch('builtins.input', return_value='cancel'), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(installer.main(['--game', str(self.archive), '--profile', str(self.profile)]), 0)
        self.assertEqual(snapshot(self.root), pending_state)
        self.assertTrue(marker.exists())

    def test_windows_failure_before_journal_does_not_change_targets(self):
        self.make_game(); before = self.relevant_before(); result = self.plan()
        real = core.atomic_write
        def fault(path, data, mode=None, **kwargs):
            if Path(path).name == 'pending.json': raise OSError('before journal')
            return real(path, data, mode, **kwargs)
        with mock.patch.object(core, 'atomic_write', side_effect=fault):
            with self.assertRaisesRegex(OSError, 'before journal'):
                installer.commit(result, self.profile)
        self.assert_originals(before)
        self.assertFalse(list(installer.state_root(self.archive).glob('operations/*/files/pending.json')))

    def test_status_is_read_only_and_names_only_transport(self):
        self.make_game(); before = snapshot(self.root)
        plan = installer.plan
        with mock.patch.object(installer, 'plan', side_effect=lambda archive, action, **kw: plan(archive, action, self.package, **kw)), mock.patch.object(installer, 'PACKAGE', self.package), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(installer.main(['status', '--game', str(self.archive), '--profile', str(self.profile)]), 0)
        self.assertEqual(snapshot(self.root), before)
        self.assertIn('Transport Autopilot 1.0.0', out.getvalue()); self.assertNotIn('GeoCore', out.getvalue())


    def automatic_fixture(self):
        for name in installer.AUTO_FILES:
            (self.package / name).write_bytes(b'synthetic signed-package fixture')
        verifier, setup = mock.Mock(), mock.Mock()
        source = (self.package / 'update_setup.py').read_bytes()
        verifier.verify_package.return_value = mock.Mock(manifest={'files': {name: {
            'size': len(source), 'sha256': hashlib.sha256(source).hexdigest()}
            for name in ('update_setup.py', 'steam_setup.py')}})
        key = {'test': 'pinned fixture key'}
        enrollment = mock.Mock(launch_options='SIGNED FIXTURE %command%',
                               instructions=self.root / 'instructions.txt',
                               shortcut=self.root / 'Play.command')
        setup.register.return_value = enrollment
        setup.transaction.side_effect = lambda *args, **kwargs: contextlib.nullcontext()
        modules = {'auto_update.py': verifier, 'update_trust.py': mock.Mock(PUBLIC_KEY=key),
                   'update_setup.py': setup, 'steam_setup.py': mock.Mock()}
        self.stack.enter_context(mock.patch.object(installer, '_automatic_module', side_effect=lambda filename, **kwargs: modules[filename]))
        return verifier, setup, key, enrollment

    def manual_main(self, extra=()):
        plan = installer.plan
        with mock.patch.object(installer, 'PACKAGE', self.package), \
                mock.patch.object(installer, 'plan', side_effect=lambda archive, action, **kw: plan(archive, action, self.package, **kw)), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            result = installer.main(list(extra) + ['--game', str(self.archive), '--profile', str(self.profile), '--yes'])
        return result, output.getvalue()

    def test_partial_automatic_package_stops_before_any_game_write(self):
        self.make_game()
        (self.package / 'release-manifest.json').write_bytes(b'{}')
        before = snapshot(self.root)
        with mock.patch.object(installer, 'commit') as commit:
            with self.assertRaisesRegex(installer.InstallError, 'incomplete'):
                self.manual_main()
        commit.assert_not_called()
        self.assertEqual(snapshot(self.root), before)

    def test_automatic_authentication_failure_stops_before_plan_or_write(self):
        self.make_game()
        verifier, setup, _, _ = self.automatic_fixture()
        verifier.verify_package.side_effect = ValueError('signature failed')
        before = snapshot(self.root)
        with mock.patch.object(installer, 'commit') as commit:
            with self.assertRaisesRegex(ValueError, 'signature failed'):
                self.manual_main()
        commit.assert_not_called(); setup.register.assert_not_called()
        self.assertEqual(snapshot(self.root), before)

    def test_signed_manual_install_authenticates_before_commit_and_enrolls_after(self):
        self.make_game()
        verifier, setup, key, enrollment = self.automatic_fixture()
        events = []
        verifier.verify_package.side_effect = lambda *args: (events.append('verify'), verifier.verify_package.return_value)[1]
        commit = installer.commit
        def save(result, profile):
            events.append('commit')
            return commit(result, profile)
        def enroll(*args, **kwargs):
            events.append('enroll')
            return enrollment
        setup.register.side_effect = enroll
        with mock.patch.object(installer, 'commit', side_effect=save):
            result, output = self.manual_main()
        self.assertEqual(result, 0)
        self.assertEqual(events, ['verify', 'verify', 'verify', 'commit', 'enroll'])
        verifier.verify_package.assert_called_with(self.package, key, installer.AUTO_PRODUCT)
        setup.register.assert_called_once_with(self.package, self.archive, self.profile, public_key=key, trusted_engine=verifier, already_locked=True, steam_helper=mock.ANY, steam_consent=True)
        self.assertIn('Steam launch options are ready. No copy and paste', output)
        self.assertIn(str(enrollment.shortcut), output)

    def test_manual_package_changed_during_plan_stops_before_commit(self):
        self.make_game()
        verifier, setup, _, _ = self.automatic_fixture()
        verifier.verify_package.side_effect = [verifier.verify_package.return_value, None, ValueError('package changed')]
        before = self.archive.read_bytes()
        with mock.patch.object(installer, 'commit') as commit:
            with self.assertRaisesRegex(ValueError, 'package changed'):
                self.manual_main()
        commit.assert_not_called(); setup.register.assert_not_called()
        self.assertEqual(self.archive.read_bytes(), before)

    def test_automatic_setup_failure_reports_mod_already_installed(self):
        self.make_game()
        _, setup, _, _ = self.automatic_fixture()
        setup.register.side_effect = OSError('setup fixture failure')
        with self.assertRaisesRegex(installer.InstallError, 'mod is installed, but automatic-update setup failed'):
            self.manual_main()
        parsed = core.AsarArchive.from_file(self.archive)
        self.assertIn(b'__geo', parsed.read(installer.APP_SCRIPT))
        self.assertTrue((installer.state_root(self.archive) / 'installed.json').is_file())

    def test_no_automatic_and_snapshot_child_suppress_only_enrollment(self):
        self.make_game()
        verifier, setup, _, _ = self.automatic_fixture()
        for extra, environment in [(['--no-automatic'], {}), ([], {'LGD_AUTO_CHILD': '1'})]:
            with self.subTest(extra=extra, environment=environment), mock.patch.dict(os.environ, environment):
                verifier.verify_package.reset_mock(); setup.register.reset_mock(); setup.transaction.reset_mock()
                result, _ = self.manual_main(extra)
                self.assertEqual(result, 0)
                self.assertEqual(verifier.verify_package.call_count, 3)
                setup.register.assert_not_called()
                setup.transaction.assert_called_once_with(self.archive, trusted_engine=verifier)

    def test_uninstall_disables_only_this_product_before_removal_even_with_flags(self):
        self.make_game()
        _, setup, _, _ = self.automatic_fixture()
        self.manual_main(['--no-automatic'])
        commit, events = installer.commit, []
        setup.unregister.side_effect = lambda *args, **kwargs: events.append(('disable', args))
        def remove(result, profile):
            events.append(('commit', result['action']))
            return commit(result, profile)
        with mock.patch.object(installer, 'commit', side_effect=remove), \
                mock.patch.dict(os.environ, {'LGD_AUTO_CHILD': '1'}):
            result, _ = self.manual_main(['uninstall', '--no-automatic'])
        self.assertEqual(result, 0)
        self.assertEqual(events, [('disable', (installer.AUTO_PRODUCT, self.archive)), ('commit', 'uninstall')])
        setup.register.assert_not_called()

    def test_uninstall_failure_keeps_auto_disabled_and_reports_it(self):
        self.make_game()
        verifier, setup, _, _ = self.automatic_fixture()
        with mock.patch.object(installer, 'commit', side_effect=OSError('removal failed')), \
                contextlib.redirect_stderr(io.StringIO()) as output:
            with self.assertRaisesRegex(OSError, 'removal failed'):
                self.manual_main(['uninstall'])
        setup.unregister.assert_called_once_with(installer.AUTO_PRODUCT, self.archive, trusted_engine=verifier, already_locked=True, steam_helper=mock.ANY, steam_consent=True)
        self.assertIn('disabled', output.getvalue())


    def test_manual_shared_transaction_spans_commit_and_enrollment(self):
        self.make_game()
        verifier, setup, _, enrollment = self.automatic_fixture()
        active = False
        @contextlib.contextmanager
        def transaction(*args, **kwargs):
            nonlocal active
            self.assertEqual(args, (self.archive,))
            self.assertIs(kwargs['trusted_engine'], verifier)
            active = True
            try:
                yield
            finally:
                active = False
        setup.transaction.side_effect = transaction
        commit = installer.commit
        def save(result, profile):
            self.assertTrue(active)
            return commit(result, profile)
        def enroll(*args, **kwargs):
            self.assertTrue(active)
            self.assertTrue(kwargs['already_locked'])
            return enrollment
        setup.register.side_effect = enroll
        with mock.patch.object(installer, 'commit', side_effect=save):
            self.assertEqual(self.manual_main()[0], 0)
        self.assertFalse(active)

    def test_game_started_during_final_copy_stops_before_real_target_changes(self):
        self.make_game()
        result, before = self.plan(), self.relevant_before()
        atomic = core.atomic_write
        def start_at_flush(path, data, mode=None, before_replace=None):
            if Path(path) == self.archive and before_replace is not None:
                original_guard = before_replace
                def late_guard():
                    installer.platform.game_running.return_value = True
                    original_guard()
                before_replace = late_guard
            return atomic(path, data, mode, before_replace=before_replace)
        with mock.patch.object(core, 'atomic_write', side_effect=start_at_flush):
            with self.assertRaisesRegex(installer.InstallError, 'started'):
                installer.commit(result, self.profile)
        self.assert_originals(before)



    def test_setup_source_changed_after_authentication_never_executes_or_writes_game(self):
        self.make_game()
        load_module = installer._automatic_module
        verifier, setup, key, _ = self.automatic_fixture()
        marker = self.root / 'unverified-setup-executed'
        malicious = 'from pathlib import Path\nPath(' + repr(str(marker)) + ').write_bytes(b"executed")\n'
        def authenticated(*args):
            (self.package / 'update_setup.py').write_text(malicious)
            return verifier.verify_package.return_value
        verifier.verify_package.side_effect = authenticated
        def load(filename, **options):
            if filename == 'auto_update.py':
                return verifier
            if filename == 'update_trust.py':
                return mock.Mock(PUBLIC_KEY=key)
            return load_module(filename, **options)
        before = self.archive.read_bytes()
        with mock.patch.object(installer, '_automatic_module', side_effect=load), \
                mock.patch.object(installer, 'commit') as commit:
            with self.assertRaisesRegex(installer.InstallError, 'changed after verification'):
                self.manual_main()
        commit.assert_not_called(); setup.register.assert_not_called()
        self.assertFalse(marker.exists())
        self.assertEqual(self.archive.read_bytes(), before)

    def test_setup_loader_executes_the_same_bytes_it_authenticated(self):
        source = self.package / 'update_setup.py'
        safe = b'SAFE_SETUP = True\n'; source.write_bytes(safe)
        expected = {'size': len(safe), 'sha256': hashlib.sha256(safe).hexdigest()}
        marker = self.root / 'raced-setup-executed'
        malicious = 'from pathlib import Path\nPath(' + repr(str(marker)) + ').write_bytes(b"executed")\n'
        digest = hashlib.sha256
        def change_after_read(data):
            source.write_text(malicious)
            return digest(data)
        with mock.patch.object(installer, 'PACKAGE', self.package), \
                mock.patch.object(installer.hashlib, 'sha256', side_effect=change_after_read):
            module = installer._automatic_module('update_setup.py', expected=expected)
        self.assertTrue(module.SAFE_SETUP)
        self.assertEqual(source.read_text(), malicious)
        self.assertFalse(marker.exists())


if __name__ == '__main__':
    unittest.main()
