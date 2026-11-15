"""Synthetic fixtures only: these tests never discover or modify a real game."""
import copy
import json
from pathlib import Path
import struct
import tempfile
import tracemalloc
import unittest
from unittest import mock

import core


def fixture():
    data = b'APP-other-mod-anchorWORKER'
    header = {'files': {
        'app.js': {'offset': '0', 'size': 20, 'integrity': core.integrity(data[:20], 4)},
        'alias.js': {'offset': '0', 'size': 20},
        'worker.js': {'size': 6, 'unpacked': True, 'integrity': core.integrity(b'WORKER')},
        'link': {'link': 'app.js'},
        'extra': {'files': {'asset': {'offset': '20', 'size': len(data) - 20, 'custom': 'preserve'}}},
    }, 'customHeader': True}
    return core._encode_header(header) + data


class ArchiveTests(unittest.TestCase):
    def test_preserves_other_files_and_deduplicated_ranges(self):
        old = core.AsarArchive(fixture())
        new = core.AsarArchive(old.replace({'app.js': b'APP-our-code-other-mod-anchor'}, {'worker.js': b'NEW-WORKER'}))
        self.assertEqual(old.read('alias.js'), new.read('alias.js'))
        self.assertEqual(old.read('extra/asset'), new.read('extra/asset'))
        self.assertEqual(new.read('app.js'), b'APP-our-code-other-mod-anchor')
        self.assertTrue(new.header['customHeader'])
        self.assertEqual(old.entry('extra/asset'), new.entry('extra/asset'))
        self.assertEqual(new.entries['link'], old.entries['link'])
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'worker.js').write_bytes(b'NEW-WORKER')
            self.assertEqual(new.read('worker.js', Path(d)), b'NEW-WORKER')

    def test_corrupt_integrity_rejected(self):
        damaged = bytearray(fixture())
        damaged[-len(b'APP-other-mod-anchorWORKER')] ^= 1
        with self.assertRaises(core.InstallError):
            core.AsarArchive(bytes(damaged)).read('app.js')

    def test_traversal_and_out_of_bounds_rejected(self):
        for header in ({'files': {'..': {'size': 0, 'offset': '0'}}},
                       {'files': {'file': {'size': 100, 'offset': '100'}}}):
            with self.assertRaises(core.InstallError):
                core.AsarArchive(core._encode_header(header))

    def test_truncated_header_rejected(self):
        with self.assertRaises(core.InstallError):
            core.AsarArchive(struct.pack('<IIII', 4, 100, 96, 50))

    def test_wrong_storage_kind_rejected(self):
        with self.assertRaises(core.InstallError):
            core.AsarArchive(fixture()).replace({'worker.js': b'abc'})


class PayloadTests(unittest.TestCase):
    def setUp(self):
        self.old = dict(prefix='OLD-PREFIX\n', suffix='\nOLD-SUFFIX', patches=[['anchor', 'old-call(anchor)']])
        self.new = dict(prefix='NEW-PREFIX\n', suffix='\nNEW-SUFFIX', patches=[['anchor', 'new-call(anchor)']])

    def test_exact_round_trip(self):
        base = b'other mod; anchor; tail'
        self.assertEqual(core.reverse_section(core.apply_section(base, self.old), self.old), base)

    def test_transition_keeps_other_mod_wrappers(self):
        installed = b'OTHER-PREFIX\n' + core.apply_section(b'anchor', self.old) + b'\nOTHER-SUFFIX'
        result = core.transition_section(installed, self.old, self.new)
        self.assertEqual(result, b'OTHER-PREFIX\nNEW-PREFIX\nnew-call(anchor)\nNEW-SUFFIX\nOTHER-SUFFIX')
        self.assertEqual(core.reverse_section(result, self.new), b'OTHER-PREFIX\nanchor\nOTHER-SUFFIX')

    def test_modified_owned_block_rejected(self):
        installed = core.apply_section(b'anchor', self.old).replace(b'OLD-PREFIX', b'BAD-PREFIX')
        with self.assertRaises(core.InstallError):
            core.reverse_section(installed, self.old)

    def test_multiple_anchors_rejected(self):
        with self.assertRaises(core.InstallError):
            core.apply_section(b'anchor; anchor', self.old)

    def test_migration_requires_matching_pair(self):
        a = dict(app=self.old, sim=self.old, version='1')
        b = dict(app=self.new, sim=self.new, version='2')
        old = core.apply_section(b'anchor', self.old)
        new = core.apply_section(b'anchor', self.new)
        self.assertEqual(core.reverse_own_payload(old, old, [a, copy.deepcopy(a), b])[:2], (b'anchor', b'anchor'))
        with self.assertRaises(core.InstallError):
            core.reverse_own_payload(old, new, [a, b])


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.app = self.root / 'app.asar'
        self.worker = self.root / 'worker.js'
        self.registration = self.root / 'mods' / 'own.json'
        self.app.write_bytes(b'BASE-with-other-mod')
        self.worker.write_bytes(b'BASE-WORKER')
        self.app.chmod(0o644)
        self.original_mode = self.app.stat().st_mode & 0o777
        self.tx = core.Transaction(self.root / 'state', [self.app, self.worker, self.registration])
        self.changes = {self.app: b'MODDED-with-other-mod', self.worker: b'MODDED-WORKER', self.registration: b'{}'}

    def tearDown(self):
        self.temp.cleanup()

    def test_install_idempotent_uninstall_and_preserves_other_mod(self):
        self.assertTrue(self.tx.install(self.changes, {'version': '1.0.0'}))
        self.assertFalse(self.tx.install(self.changes, {'version': '1.0.0'}))
        self.assertEqual(self.tx.status()['status'], 'installed')
        self.assertTrue(self.tx.uninstall())
        self.assertEqual(self.app.read_bytes(), b'BASE-with-other-mod')
        self.assertEqual(self.worker.read_bytes(), b'BASE-WORKER')
        self.assertFalse(self.registration.exists())
        self.assertEqual(self.app.stat().st_mode & 0o777, self.original_mode)
        self.assertFalse(self.tx.uninstall())

    def test_failure_rolls_back_every_file(self):
        def failure():
            raise RuntimeError('simulated signing failure')
        with self.assertRaisesRegex(RuntimeError, 'simulated signing failure'):
            self.tx.install(self.changes, {}, post_apply=failure)
        self.assertEqual(self.app.read_bytes(), b'BASE-with-other-mod')
        self.assertEqual(self.worker.read_bytes(), b'BASE-WORKER')
        self.assertFalse(self.registration.exists())
        self.assertEqual(self.tx.status()['status'], 'not-installed')

    def test_external_change_rejected_before_uninstall(self):
        self.tx.install(self.changes, {})
        self.app.write_bytes(b'STEAM-UPDATE')
        with self.assertRaises(core.InstallError):
            self.tx.uninstall()
        self.assertEqual(self.worker.read_bytes(), b'MODDED-WORKER')
        self.assertEqual(self.app.read_bytes(), b'STEAM-UPDATE')

    def test_upgrade_retains_original_baselines(self):
        self.tx.install(self.changes, {'version': '1'})
        updated = dict(self.changes)
        updated[self.worker] = b'NEW-OWN-VERSION'
        self.tx.install(updated, {'version': '2'})
        self.tx.uninstall()
        self.assertEqual(self.worker.read_bytes(), b'BASE-WORKER')

    def test_uninstall_failure_rolls_back_and_preserves_receipt(self):
        self.tx.install(self.changes, {})
        original = core.atomic_write
        failed = False
        def fail_once(path, data, mode=None, **kwargs):
            nonlocal failed
            if path == self.worker and isinstance(data, core.FileContent) and data.path.read_bytes() == b'BASE-WORKER' and not failed:
                failed = True
                raise OSError('injected disk failure')
            return original(path, data, mode, **kwargs)
        with mock.patch.object(core, 'atomic_write', side_effect=fail_once):
            with self.assertRaises(OSError):
                self.tx.uninstall()
        self.assertEqual(self.app.read_bytes(), b'MODDED-with-other-mod')
        self.assertEqual(self.worker.read_bytes(), b'MODDED-WORKER')
        self.assertEqual(self.tx.status()['status'], 'installed')

    def test_backup_corruption_rejected_before_writes(self):
        self.tx.install(self.changes, {})
        record = json.loads(self.tx.receipt.read_text())
        (self.tx.state_dir / 'backups' / record['files'][0]['before']).write_bytes(b'CORRUPTED')
        with self.assertRaises(core.InstallError):
            self.tx.uninstall()
        self.assertEqual(self.worker.read_bytes(), b'MODDED-WORKER')

    def test_unapproved_path_and_symlink_rejected(self):
        other = self.root / 'other'
        with self.assertRaises(core.InstallError):
            self.tx.install({other: b'bad'}, {})
        self.app.unlink()
        try:
            self.app.symlink_to(self.worker)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows user lacks symbolic-link creation privilege')
            raise
        with self.assertRaises(core.InstallError):
            self.tx.install(self.changes, {})

    def test_delete_and_restore_owned_file(self):
        self.registration.parent.mkdir()
        self.registration.write_bytes(b'owned-old-registration')
        self.tx.install({self.registration: None}, {})
        self.assertFalse(self.registration.exists())
        self.assertFalse(self.tx.install({self.registration: None}, {}))
        self.tx.uninstall()
        self.assertEqual(self.registration.read_bytes(), b'owned-old-registration')

    def test_expected_before_rejects_change_after_planning(self):
        expected = {self.app: core.sha256(self.app.read_bytes()), self.worker: core.sha256(self.worker.read_bytes()), self.registration: None}
        self.app.write_bytes(b'changed-after-plan')
        with self.assertRaisesRegex(core.InstallError, 'File changed after planning'):
            self.tx.install(self.changes, {}, expected_before=expected)
        self.assertEqual(self.app.read_bytes(), b'changed-after-plan')
        self.assertFalse(self.registration.exists())

    def test_recover_interrupted_partial_commit(self):
        self.tx.state_dir.mkdir()
        base = self.app.read_bytes()
        before = core.sha256(base)
        core.atomic_write(self.tx.state_dir / 'backups' / before, base)
        self.tx.journal.write_text(json.dumps(dict(schema=1, priorReceipt=None, files=[
            dict(path=str(self.app), before=before, after=core.sha256(b'PARTIAL'), mode=0o644),
            dict(path=str(self.registration), before=None, after=core.sha256(b'{}'), mode=None)])))
        self.app.write_bytes(b'PARTIAL')
        self.assertEqual(self.tx.status()['status'], 'interrupted')
        self.tx.recover()
        self.assertEqual(self.app.read_bytes(), base)
        self.assertFalse(self.registration.exists())
        self.assertFalse(self.tx.journal.exists())

    def test_recover_refuses_external_change(self):
        self.tx.state_dir.mkdir()
        before = core.sha256(self.app.read_bytes())
        core.atomic_write(self.tx.state_dir / 'backups' / before, self.app.read_bytes())
        self.tx.journal.write_text(json.dumps(dict(schema=1, priorReceipt=None, files=[
            dict(path=str(self.app), before=before, after=core.sha256(b'PARTIAL'), mode=0o644)])))
        self.app.write_bytes(b'EXTERNAL')
        with self.assertRaisesRegex(core.InstallError, 'externally changed'):
            self.tx.recover()
        self.assertEqual(self.app.read_bytes(), b'EXTERNAL')

    def test_uninstall_preserves_external_change_during_commit(self):
        self.tx.install(self.changes, {})
        original = core.atomic_write
        def interference(path, data, mode=None, **kwargs):
            result = original(path, data, mode, **kwargs)
            if path == self.tx.journal:
                self.worker.write_bytes(b'EXTERNAL')
            return result
        with mock.patch.object(core, 'atomic_write', side_effect=interference):
            with self.assertRaises(core.InstallError):
                self.tx.uninstall()
        self.assertEqual(self.worker.read_bytes(), b'EXTERNAL')


    def test_pre_apply_guard_runs_after_flushed_copy_before_target_replacement(self):
        events = []
        fsync = core.os.fsync
        def flushed(fd):
            events.append('fsync')
            return fsync(fd)
        def started():
            self.assertEqual(events[-1], 'fsync')
            self.assertEqual(self.app.read_bytes(), b'BASE-with-other-mod')
            self.assertTrue(any(path.read_bytes() == self.changes[self.app] for path in self.root.glob('.ta-*.tmp')))
            raise core.InstallError('game started after preparation')
        with mock.patch.object(core.os, 'fsync', side_effect=flushed):
            with self.assertRaisesRegex(core.InstallError, 'game started'):
                self.tx.install(self.changes, {}, pre_apply=started)
        self.assertEqual(self.app.read_bytes(), b'BASE-with-other-mod')
        self.assertEqual(self.worker.read_bytes(), b'BASE-WORKER')
        self.assertFalse(self.registration.exists())
        self.assertFalse(self.tx.journal.exists())

    def test_edit_during_streamed_temp_copy_is_preserved(self):
        replacement = self.root / 'replacement.bin'; replacement.write_bytes(b'PREPARED NEW CONTENT')
        copy = core._copy_stream
        def edit_during_copy(source, destination, count=None):
            copy(source, destination, count)
            if Path(source.name) == replacement:
                self.app.write_bytes(b'EXTERNAL DURING COPY')
        with mock.patch.object(core, '_copy_stream', side_effect=edit_during_copy):
            with self.assertRaises(core.InstallError):
                self.tx.install({self.app: core.FileContent(replacement)}, {})
        self.assertEqual(self.app.read_bytes(), b'EXTERNAL DURING COPY')
        self.assertEqual(self.worker.read_bytes(), b'BASE-WORKER')
        self.assertTrue(self.tx.journal.exists())
        self.assertTrue((self.tx.state_dir / 'backups' / core.sha256(b'BASE-with-other-mod')).is_file())

    def test_pre_apply_guard_also_protects_deletions(self):
        def started():
            raise core.InstallError('game started before deletion')
        with self.assertRaisesRegex(core.InstallError, 'before deletion'):
            self.tx.install({self.app: None}, {}, pre_apply=started)
        self.assertEqual(self.app.read_bytes(), b'BASE-with-other-mod')
        self.assertFalse(self.tx.journal.exists())



class LongPathTests(unittest.TestCase):
    def target_and_limited_mkstemp(self, root):
        # The reported Windows backup is legal at 247 characters, while a
        # destination-derived temporary name grows to 261 and exceeds MAX_PATH.
        name = 'a' * 64
        parent_length = 247 - len(name) - 1
        padding = parent_length - len(str(root)) - 1
        self.assertGreater(padding, 0)
        parent = root / ('p' * padding)
        parent.mkdir()
        target = parent / name
        self.assertEqual(len(str(target)), 247)
        self.assertGreaterEqual(len(str(parent / ('.' + name + '.abcdefgh.tmp'))), 260)
        real = tempfile.mkstemp
        made = []
        def limited(*args, **kwargs):
            directory = Path(kwargs.get('dir', '.'))
            prefix = kwargs.get('prefix', 'tmp')
            suffix = kwargs.get('suffix', '')
            if len(str(directory / (prefix + 'abcdefgh' + suffix))) >= 260:
                raise OSError(2, 'simulated Windows MAX_PATH temporary-file failure')
            fd, path = real(*args, **kwargs)
            made.append(Path(path))
            self.assertLess(len(str(path)), 260)
            return fd, path
        return target, limited, made

    def test_atomic_write_long_backup_name_uses_short_temp_name(self):
        with tempfile.TemporaryDirectory() as d:
            target, limited, made = self.target_and_limited_mkstemp(Path(d).resolve())
            with mock.patch.object(core.tempfile, 'mkstemp', side_effect=limited):
                core.atomic_write(target, b'new backup bytes')
            self.assertEqual(target.read_bytes(), b'new backup bytes')
            self.assertEqual(list(target.parent.iterdir()), [target])
            self.assertTrue(made)
            self.assertFalse(any(path.exists() for path in made))

    def test_readonly_temp_cleanup_preserves_original_replace_error(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d, 'existing-file')
            target.write_bytes(b'unchanged original')
            temporary = []
            denied_unlinks = []
            original_replace = core.os.replace
            original_unlink = core.os.unlink
            replace_error = PermissionError('original target replacement denied')
            def deny_replace(source, destination):
                if Path(destination) == target:
                    temporary.append(Path(source))
                    self.assertEqual(Path(source).stat().st_mode & 0o222, 0)
                    raise replace_error
                return original_replace(source, destination)
            def windows_unlink(path, *args, **kwargs):
                candidate = Path(path)
                if candidate in temporary and candidate.exists() and not candidate.stat().st_mode & 0o222:
                    denied_unlinks.append(candidate)
                    raise PermissionError('Windows refuses unlink of readonly temporary file')
                return original_unlink(path, *args, **kwargs)
            with mock.patch.object(core.os, 'replace', side_effect=deny_replace), mock.patch.object(core.os, 'unlink', side_effect=windows_unlink):
                with self.assertRaises(PermissionError) as raised:
                    core.atomic_write(target, b'replacement bytes', 0o444)
            self.assertIs(raised.exception, replace_error, 'Cleanup must preserve the original replacement error')
            self.assertEqual(target.read_bytes(), b'unchanged original')
            self.assertEqual(len(temporary), 1)
            self.assertFalse(temporary[0].exists(), 'Failed replacement must not leak readonly temporary data')
            self.assertEqual(list(Path(d).iterdir()), [target])

    def test_archive_edit_long_destination_uses_short_temp_name(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            target, limited, made = self.target_and_limited_mkstemp(root)
            source = root / 'source.asar'
            source.write_bytes(fixture())
            before = source.read_bytes()
            edit = core.AsarArchive.from_file(source).plan_replace({'app.js': b'new owned script'})
            with mock.patch.object(core.tempfile, 'mkstemp', side_effect=limited):
                result = edit.write_to(target)
            self.assertEqual(result.path, target)
            self.assertEqual(core.AsarArchive.from_file(target).read('app.js'), b'new owned script')
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(list(target.parent.iterdir()), [target])
            self.assertTrue(made)
            self.assertFalse(any(path.exists() for path in made))


class StreamingTests(unittest.TestCase):
    def test_sparse_archive_over_four_gibibytes_reads_only_target(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, 'large.asar')
            offset = 4 * 1024 ** 3 + 123
            header = core._encode_header({'files': {'target': {'size': 5, 'offset': str(offset)}}})
            with path.open('wb') as stream:
                stream.write(header)
                stream.seek(len(header) + offset)
                stream.write(b'hello')
            tracemalloc.start()
            try:
                with mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('whole-file read')):
                    archive = core.AsarArchive.from_file(path)
                    self.assertIsNone(archive.data)
                    self.assertEqual(archive.read('target'), b'hello')
                _, peak = tracemalloc.get_traced_memory()
                self.assertLess(peak, 2 * 1024 ** 2)
            finally:
                tracemalloc.stop()

    def test_streamed_rebuild_preserves_payload_and_is_memory_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            source, target = Path(d, 'original.asar'), Path(d, 'new.asar')
            payload_size = 32 * 1024 ** 2
            header = core._encode_header({'files': {
                'app': {'offset': '0', 'size': 5},
                'large': {'offset': '5', 'size': payload_size - 5}}})
            with source.open('wb') as stream:
                stream.write(header)
                stream.write(b'hello')
                stream.seek(len(header) + payload_size - 1)
                stream.write(b'z')
            tracemalloc.start()
            try:
                with mock.patch.object(Path, 'read_bytes', side_effect=AssertionError('whole-file read')):
                    archive = core.AsarArchive.from_file(source)
                    plan = archive.plan_replace({'app': b'new script'})
                    content = plan.write_to(target)
                    self.assertIsInstance(content, core.FileContent)
                    result = core.AsarArchive.from_file(target)
                    self.assertEqual(result.read('app'), b'new script')
                    self.assertEqual(plan.header_bytes, result.header_bytes)
                    self.assertEqual(result.entry('large'), archive.entry('large'))
                    self.assertEqual(target.stat().st_size, plan.size)
                    with target.open('rb') as stream:
                        stream.seek(result.payload_start + payload_size - 1)
                        self.assertEqual(stream.read(1), b'z')
                _, peak = tracemalloc.get_traced_memory()
                self.assertLess(peak, 12 * 1024 ** 2)
            finally:
                tracemalloc.stop()

    def test_streamed_transaction_and_rollback_do_not_read_whole_files(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            target, replacement = root / 'game', root / 'replacement'
            for path, marker in ((target, b'old'), (replacement, b'new')):
                with path.open('wb') as stream:
                    stream.write(marker)
                    stream.seek(20 * 1024 ** 2 - 1)
                    stream.write(marker[:1])
            before = core.file_sha(target)
            tx = core.Transaction(root / 'state', [target])
            original_read_bytes = Path.read_bytes
            def only_small_files(path):
                if path in (target, replacement) or path.parent.name == 'backups':
                    raise AssertionError('whole-file read')
                return original_read_bytes(path)
            tracemalloc.start()
            try:
                with mock.patch.object(Path, 'read_bytes', only_small_files):
                    tx.install({target: core.FileContent(replacement)}, {}, expected_before={target: before})
                    self.assertEqual(core.file_sha(target), core.file_sha(replacement))
                    self.assertIsInstance(tx.baselines()[target], core.FileContent)
                    tx.uninstall()
                    self.assertEqual(core.file_sha(target), before)
                _, peak = tracemalloc.get_traced_memory()
                self.assertLess(peak, 12 * 1024 ** 2)
            finally:
                tracemalloc.stop()

    def test_recovery_skips_unchanged_read_only_target(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d).resolve()
            target = root / 'readonly.exe'
            target.write_bytes(b'original')
            digest = core.file_sha(target)
            tx = core.Transaction(root / 'state', [target])
            tx.state_dir.mkdir()
            core.atomic_write(tx.state_dir / 'backups' / digest, b'original')
            tx.journal.write_text(json.dumps(dict(schema=1, files=[
                dict(path=str(target), before=digest, after=core.sha256(b'replacement'), mode=0o444)])))
            target.chmod(0o444)
            original = core.atomic_write
            def no_target_write(path, *args, **kwargs):
                self.assertNotEqual(path, target, 'Recovery must not rewrite an unchanged file')
                return original(path, *args, **kwargs)
            with mock.patch.object(core, 'atomic_write', side_effect=no_target_write):
                tx.recover()
            self.assertEqual(core.file_sha(target), digest)

    def test_stale_archive_plan_refuses_changed_source(self):
        with tempfile.TemporaryDirectory() as d:
            source = Path(d, 'source.asar')
            source.write_bytes(fixture())
            archive = core.AsarArchive.from_file(source)
            plan = archive.plan_replace({'app.js': b'new'})
            with source.open('ab') as stream:
                stream.write(b'external')
            with self.assertRaises(core.InstallError):
                plan.write_to(Path(d, 'target.asar'))


if __name__ == '__main__':
    unittest.main()
