"""Authenticated update tests; synthetic games, profiles and ephemeral keys only."""
import contextlib
import hashlib
import importlib.util
import importlib._bootstrap_external
import io
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib

from updater import engine
from updater.test_signature import TEST_SIGNING_AVAILABLE, TestSigner

ROOT = Path(__file__).resolve().parent.parent


def png():
    def chunk(kind, value):
        return struct.pack('>I', len(value)) + kind + value + struct.pack('>I', zlib.crc32(kind + value) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(b'\0\0\0\0')) + chunk(b'IEND', b''))


def section(kind, release='current'):
    return {'prefix': '/* __geoTransport %s %s */\n' % (kind, release),
            'suffix': '\n/* __transportAutobuildUI %s %s */' % (kind, release),
            'patches': [[kind + '_ANCHOR', kind + '_OWN_' + release + '(' + kind + '_ANCHOR)']]}


def pe(header_hash):
    """Minimal bounded PE resource fixture, never an executable game."""
    data = bytearray(2048)
    data[:2] = b'MZ'; struct.pack_into('<I', data, 0x3c, 128)
    data[128:132] = b'PE\0\0'
    struct.pack_into('<H', data, 134, 1); struct.pack_into('<H', data, 148, 224)
    struct.pack_into('<H', data, 152, 0x10b)
    struct.pack_into('<II', data, 152 + 96 + 16, 4096, 1024)
    struct.pack_into('<IIII', data, 376 + 8, 1024, 4096, 1024, 512)
    base = 512
    for offset, label in ((100, 'INTEGRITY'), (130, 'ELECTRONASAR')):
        struct.pack_into('<H', data, base + offset, len(label))
        data[base + offset + 2:base + offset + 2 + len(label) * 2] = label.encode('utf-16le')
    for offset, name, target in ((0, 0x80000000 | 100, 0x80000000 | 24),
                                  (24, 0x80000000 | 130, 0x80000000 | 48), (48, 1033, 72)):
        struct.pack_into('<H', data, base + offset + 14, 1)
        struct.pack_into('<II', data, base + offset + 16, name, target)
    payload = json.dumps([{'file': 'resources\\app.asar', 'alg': 'SHA256', 'value': header_hash}]).encode()
    struct.pack_into('<II', data, base + 72, 4096 + 256, len(payload))
    data[base + 256:base + 256 + len(payload)] = payload
    return bytes(data)


@unittest.skipUnless(TEST_SIGNING_AVAILABLE, 'An ephemeral RSA test signer is required')
class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.signer = TestSigner()

    @classmethod
    def tearDownClass(cls):
        cls.signer.close()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.package = self.root / 'package'; self.package.mkdir()
        self.state = self.root / 'state'
        self.profile = self.root / 'profile'; self.profile.mkdir()
        self.archive = self.root / 'game/resources/app.asar'
        self.archive.parent.mkdir(parents=True)
        self.exe = self.archive.parent.parent / 'Geoplanetical.exe'
        for name in ('install.py', 'core.py', 'platform_support.py', 'registration.py'):
            shutil.copyfile(ROOT / 'transport-autobuild/installer' / name, self.package / name)
        self.legacy_fixture_installer(self.package)
        with (self.package / 'platform_support.py').open('a') as stream:
            stream.write('\n# Signed synthetic test fixture: never discover live Steam or processes.\n'
                         'def steam_libraries(): return []\n'
                         'def game_running(archive): return False\n')
        for name in engine.RUNTIME_FILES | {'steam_setup.py'}:
            self.runtime_file(self.package, name)
        manifest = {'id': 'littleguydev-transport-autopilot', 'name': 'Transport Autopilot', 'version': '1.1.0',
                    'contentApi': 1, 'tier': 'presentation', 'thumbnail': 'thumbnail.png',
                    'requires': [], 'provides': {}, 'description': 'Synthetic fixture', 'authors': ['Little Guy Dev']}
        (self.package / 'mod.json').write_text(json.dumps(manifest))
        (self.package / 'thumbnail.png').write_bytes(png())
        self.payload = {'schema': 1, 'id': manifest['id'], 'version': manifest['version'],
                        'gameVersions': ['1.1.3'], 'app': section('APP'), 'sim': section('SIM')}
        (self.package / 'transport-payload.json').write_text(json.dumps(self.payload))
        (self.package / 'legacy_payloads.json').write_text(json.dumps({'schema': 1, 'payloads': []}))
        self.sign()
        self.installer = engine._load_installer(self.package)
        self.core = sys.modules['core']
        self.make_game()

    def tearDown(self):
        self.temp.cleanup()

    def runtime_file(self, package, name):
        sources = {'auto_update.py': 'engine.py', 'update_signature.py': 'signature.py',
                   'update_setup.py': 'setup.py', 'update_launcher.py': 'launcher.py', 'steam_setup.py': 'steam.py'}
        if name == 'update_trust.py':
            (package / name).write_text('PUBLIC_KEY = ' + repr(self.signer.public_key) + '\n')
        else:
            shutil.copyfile(ROOT / 'updater' / sources[name], package / name)

    def legacy_fixture_installer(self, package):
        # Keep coverage of pre-policy releases while production's officially
        # tested version advances. This signed fixture changes only its list.
        path = package / 'install.py'
        source = path.read_text()
        source = source.replace("payload.get('gameVersions') != ['1.1.4']",
                                "payload.get('gameVersions') != ['1.1.3']")
        path.write_text(source)


    def sign(self, *, sequence=1, product='transport-autopilot', mutate=None, package=None, version='1.1.0',
             game_versions=None):
        package = package or self.package
        if game_versions is None:
            game_versions = json.loads((package / engine.PRODUCTS[product]).read_bytes())['gameVersions']
        manifest = {'schema': 1, 'product': product, 'sequence': sequence, 'version': version,
                    'minimum_updater': 1, 'game_versions': game_versions, 'files': {}}
        policy = json.loads((package / engine.PRODUCTS[product]).read_bytes()).get('compatibilityPolicy')
        if policy is not None:
            manifest['compatibility_policy'] = policy
        for path in package.iterdir():
            if path.name not in (engine.MANIFEST, engine.SIGNATURE, engine.CHECKSUMS):
                data = path.read_bytes()
                manifest['files'][path.name] = {'size': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
        if mutate:
            mutate(manifest)
        raw = json.dumps(manifest, sort_keys=True).encode()
        (package / engine.MANIFEST).write_bytes(raw)
        (package / engine.SIGNATURE).write_bytes(self.signer.sign(raw))

    def companion_package(self, product, name, marker, app_kind, sim_kind):
        """Exercise shared coordination with renamed copies of our available installer.

        These are signed synthetic fixtures, not the other products' source or
        release packages. Their separate ownership/state paths test coexistence.
        """
        package = self.root / (product + '-package'); package.mkdir()
        replacements = [('Transport Autopilot', name), ('transport-autopilot', product),
                        ('transport-payload.json', engine.PRODUCTS[product]),
                        ('__geoTransport', marker), ('__transportAutobuildUI', marker + 'UI')]
        for filename in ('install.py', 'core.py', 'platform_support.py', 'registration.py'):
            source = (ROOT / 'transport-autobuild/installer' / filename).read_text()
            for before, after in replacements:
                source = source.replace(before, after)
            (package / filename).write_text(source)
        self.legacy_fixture_installer(package)
        with (package / 'platform_support.py').open('a') as stream:
            stream.write('\n# Synthetic fixture: never inspect live Steam or processes.\n'
                         'def steam_libraries(): return []\n'
                         'def game_running(archive): return False\n')
        for filename in engine.RUNTIME_FILES | {'steam_setup.py'}:
            self.runtime_file(package, filename)
        manifest = {'id': 'littleguydev-' + product, 'name': name, 'version': '1.0.0',
                    'contentApi': 1, 'tier': 'presentation', 'thumbnail': 'thumbnail.png',
                    'requires': [], 'provides': {}, 'description': 'Synthetic companion fixture',
                    'authors': ['Little Guy Dev']}
        (package / 'mod.json').write_text(json.dumps(manifest))
        (package / 'thumbnail.png').write_bytes(png())
        def companion_section(kind):
            return {'prefix': '/* ' + marker + ' ' + kind + ' */\n',
                    'suffix': '\n/* ' + marker + 'UI ' + kind + ' */',
                    'patches': [[kind + '_ANCHOR', kind + '_OWN(' + kind + '_ANCHOR)']]}
        payload = {'schema': 1, 'id': manifest['id'], 'version': '1.0.0', 'gameVersions': ['1.1.3'],
                   'app': companion_section(app_kind), 'sim': companion_section(sim_kind)}
        (package / engine.PRODUCTS[product]).write_text(json.dumps(payload))
        (package / 'legacy_payloads.json').write_text(json.dumps({'schema': 1, 'payloads': []}))
        self.sign(package=package, product=product, version='1.0.0')
        return package

    def tourism_package(self):
        return self.companion_package('tourism-dashboard', 'Tourism Dashboard', '__geoTourism', 'TOURX', 'TOURY')

    def vaccination_package(self):
        return self.companion_package('vaccination-autopilot', 'Vaccination Autopilot', '__geoVaccination', 'VACX', 'VACY')

    def make_game(self, app=b'FOREIGN;APP_ANCHOR;TAIL', sim=b'FOREIGN;SIM_ANCHOR;TAIL', version='1.1.3'):
        core = self.installer
        header, payload = {'files': {}}, bytearray()
        for name, data in [('package.json', json.dumps({'name': 'geoplanetical', 'version': version,
                              'steam': {'appId': 940230}}).encode()),
                           (core.APP_SCRIPT, app), (core.SIM_SCRIPT, sim), ('foreign.bin', b'KEEP FOREIGN')]:
            node = header
            parts = name.split('/')
            for part in parts[:-1]:
                node = node['files'].setdefault(part, {'files': {}})
            node['files'][parts[-1]] = {'size': len(data), 'offset': str(len(payload)),
                                      'integrity': self.core.integrity(data)}
            payload.extend(data)
        # Use the existing independent archive encoder, never game code.
        encoded = self.core._encode_header(header)
        self.archive.write_bytes(encoded + payload)
        self.exe.write_bytes(pe(core.sha256(core.AsarArchive.from_file(self.archive).header_bytes)))

    def apply(self):
        with mock.patch.object(self.installer.platform, 'game_running', return_value=False), \
                mock.patch.object(self.installer.registration, 'steam_libraries', return_value=[]), \
                engine.exclusive(self.state), \
                contextlib.redirect_stdout(io.StringIO()):
            return self.installer.main(['--game', str(self.archive), '--profile', str(self.profile), '--yes', '--no-automatic'],
                                       _coordinator_locked=True)

    def test_signed_complete_snapshot_is_private_and_independent(self):
        engine._private_directory(self.state)
        with engine.verified_snapshot(self.package, self.state, self.signer.public_key, 'transport-autopilot') as snapshot:
            if os.name != 'nt':
                self.assertEqual(snapshot.root.stat().st_mode & 0o777, 0o500)
                self.assertEqual((snapshot.root / 'install.py').stat().st_mode & 0o777, 0o400)
            else:
                self.assertFalse((snapshot.root / 'install.py').stat().st_mode & 0o200)
            (self.package / 'install.py').write_text('raise RuntimeError("untrusted")')
            self.assertNotIn(b'untrusted', (snapshot.root / 'install.py').read_bytes())
            self.assertEqual(engine.verify_package(snapshot.root, self.signer.public_key, 'transport-autopilot').digest, snapshot.digest)
        self.assertFalse(snapshot.root.exists())

    def test_tampered_python_rejected_before_any_import_or_runner(self):
        (self.package / 'core.py').write_text('raise RuntimeError("must not execute")')
        runner = mock.Mock()
        with self.assertRaisesRegex(engine.UpdateError, 'failed verification'):
            engine.run_updates(self.state, [{'product': 'transport-autopilot', 'package_root': self.package}],
                               self.archive, self.profile, public_key=self.signer.public_key,
                               python=Path(sys.executable).resolve(), runner=runner)
        runner.assert_not_called()

    def test_source_change_after_authentication_cannot_enter_snapshot(self):
        engine._private_directory(self.state)
        verify = engine.verify_package
        def change_after_verify(root, public_key, product, **options):
            result = verify(root, public_key, product, **options)
            if root == self.package:
                (self.package / 'core.py').write_bytes(b'raise RuntimeError("race")')
            return result
        with mock.patch.object(engine, 'verify_package', side_effect=change_after_verify):
            with self.assertRaisesRegex(engine.UpdateError, 'failed verification'):
                with engine.verified_snapshot(self.package, self.state, self.signer.public_key, 'transport-autopilot'):
                    self.fail('changed package reached installer execution')
        self.assertEqual(list((self.state / 'snapshots').iterdir()), [])

    def symlink(self, path, target):
        try:
            path.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            if getattr(exc, 'winerror', None) == 1314:
                self.skipTest('Windows user lacks symbolic-link creation privilege')
            raise

    def test_legacy_python_cache_is_never_imported_or_snapshotted(self):
        source = self.package / 'core.py'
        cache_file = Path(importlib.util.cache_from_source(str(source)))
        cache_file.parent.mkdir()
        poison = compile('raise RuntimeError("legacy cache executed")', str(source), 'exec')
        cache_file.write_bytes(importlib._bootstrap_external._code_to_timestamp_pyc(
            poison, int(source.stat().st_mtime), source.stat().st_size))
        engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')
        # The new manual bootstrap explicitly compiles sibling source bytes.
        loaded = engine._load_installer(self.package)
        self.assertTrue(callable(loaded.plan))
        engine._private_directory(self.state)
        with engine.verified_snapshot(self.package, self.state, self.signer.public_key, 'transport-autopilot') as snapshot:
            self.assertFalse((snapshot.root / '__pycache__').exists())
        with self.assertRaisesRegex(engine.UpdateError, 'cache'):
            engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot', allow_legacy_cache=False)

    def test_poisoned_signature_cache_cannot_execute_before_authentication(self):
        source = self.package / 'update_signature.py'
        cache_file = Path(importlib.util.cache_from_source(str(source)))
        cache_file.parent.mkdir()
        marker = self.root / 'poisoned-signature-cache-executed'
        poison = compile('from pathlib import Path\nPath(' + repr(str(marker)) + ').write_bytes(b"executed")\n'
                         'raise RuntimeError("poisoned signature verifier cache executed")', str(source), 'exec')
        cache_file.write_bytes(importlib._bootstrap_external._code_to_timestamp_pyc(
            poison, int(source.stat().st_mtime), source.stat().st_size))
        # This fresh manual bootstrap has an empty verifier function cache.
        bootstrap = self.installer._automatic_module('auto_update.py')
        package = bootstrap.verify_package(self.package, self.signer.public_key, 'transport-autopilot')
        self.assertEqual(package.manifest['product'], 'transport-autopilot')
        self.assertFalse(marker.exists())

    def test_legacy_cache_link_is_rejected(self):
        outside = self.root / 'outside-cache'; outside.mkdir()
        self.symlink(self.package / '__pycache__', outside)
        with self.assertRaisesRegex(engine.UpdateError, 'Symlink'):
            engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')

    def test_source_os_metadata_is_ignored_and_excluded_from_snapshot(self):
        for name in engine.SOURCE_METADATA:
            (self.package / name).write_bytes(b'Untrusted operating-system folder metadata')
        engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')
        engine._private_directory(self.state)
        with engine.verified_snapshot(self.package, self.state, self.signer.public_key, 'transport-autopilot') as snapshot:
            self.assertFalse(engine.SOURCE_METADATA & {path.name for path in snapshot.root.iterdir()})
        with self.assertRaisesRegex(engine.UpdateError, 'source-folder metadata'):
            engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot', allow_legacy_cache=False)

    def test_source_os_metadata_must_be_a_small_regular_non_link_file(self):
        for name in engine.SOURCE_METADATA:
            path = self.package / name
            path.mkdir()
            with self.assertRaisesRegex(engine.UpdateError, 'source-folder metadata'):
                engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')
            path.rmdir()
            with path.open('wb') as stream:
                stream.truncate(1024 * 1024 + 1)
            with self.assertRaisesRegex(engine.UpdateError, 'source-folder metadata'):
                engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')
            path.unlink()

    def test_source_os_metadata_link_is_rejected(self):
        outside = self.root / 'outside-metadata'; outside.mkdir()
        self.symlink(self.package / '.DS_Store', outside)
        with self.assertRaisesRegex(engine.UpdateError, 'Symlink'):
            engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')

    def test_unsigned_partial_and_extra_files_fail_closed(self):
        (self.package / 'extra.py').write_bytes(b'')
        with self.assertRaisesRegex(engine.UpdateError, 'unexpected'):
            engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')
        (self.package / 'extra.py').unlink()
        (self.package / 'thumbnail.png').unlink()
        with self.assertRaisesRegex(engine.UpdateError, 'incomplete'):
            engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')

    def test_optional_checksum_list_must_authenticate_every_exact_file(self):
        entries = []
        for path in sorted(self.package.iterdir()):
            entries.append(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name + '\n')
        checksums = self.package / engine.CHECKSUMS
        checksums.write_text(''.join(entries))
        engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')
        checksums.write_text(''.join(entries[:-1]))
        with self.assertRaisesRegex(engine.UpdateError, 'checksums'):
            engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')
        checksums.write_text(''.join(entries) + entries[0])
        with self.assertRaisesRegex(engine.UpdateError, 'duplicate'):
            engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')

    def test_signed_hostile_paths_and_schema_rejected(self):
        for mutate in [lambda m: m['files'].update({'../evil.py': {'size': 0, 'sha256': '0'*64}}),
                       lambda m: m.update(sequence=True), lambda m: m.update(minimum_updater=True),
                       lambda m: m.update(game_versions=['1.2.0'])]:
            self.sign(mutate=mutate)
            with self.assertRaises(engine.UpdateError):
                engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')

    def test_game_version_allowlist_is_explicit_bounded_and_matches_authenticated_payload(self):
        invalid = [[], ['*'], ['1.1.x'], ['1.1.3', '1.1.3'], ['01.1.3'], ['1.1.3-01'], ['1.1.3;command'],
                   ['1.1.4'], ['1.1.' + str(i) for i in range(engine.MAX_GAME_VERSIONS + 1)],
                   ['1.' + '1' * 65 + '.3']]
        for versions in invalid:
            with self.subTest(versions=versions):
                self.sign(game_versions=versions)
                with self.assertRaises(engine.UpdateError):
                    engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')
        self.payload['gameVersions'] = ['1.1.3', '1.1.4-rc.1+build.2']
        (self.package / 'transport-payload.json').write_text(json.dumps(self.payload))
        self.sign()
        engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')

    def test_installed_protocol_accepts_signed_future_game_with_matching_new_installer(self):
        self.make_game(version='1.1.4')
        self.payload['gameVersions'] = ['1.1.4']
        (self.package / 'transport-payload.json').write_text(json.dumps(self.payload))
        installer_path = self.package / 'install.py'
        source = installer_path.read_text()
        original = "payload.get('gameVersions') != ['1.1.3']"
        self.assertEqual(source.count(original), 1)
        installer_path.write_text(source.replace(original, "payload.get('gameVersions') != ['1.1.4']", 1))
        self.sign(sequence=2)
        result = engine.run_updates(self.state, [{'product': 'transport-autopilot', 'package_root': self.package}],
                                    self.archive, self.profile, public_key=self.signer.public_key,
                                    python=Path(sys.executable).resolve())
        self.assertTrue(result.changed)
        installed = json.loads((self.installer.state_root(self.archive) / 'installed.json').read_bytes())
        self.assertEqual(installed['gameVersion'], '1.1.4')

    def test_old_signed_package_rejects_future_game_before_any_write(self):
        self.make_game(version='1.1.4')
        before = self.archive.read_bytes()
        with self.assertRaisesRegex(engine.UpdateError, 'versions: 1.1.3'):
            engine.run_updates(self.state, [{'product': 'transport-autopilot', 'package_root': self.package}],
                               self.archive, self.profile, public_key=self.signer.public_key,
                               python=Path(sys.executable).resolve())
        self.assertEqual(before, self.archive.read_bytes())

    def test_exact_hooks_policy_accepts_future_game_and_checks_real_hooks(self):
        self.payload['compatibilityPolicy'] = {'mode': 'exact-hooks', 'minimumGameVersion': '1.1.4'}
        self.payload['gameVersions'] = ['1.1.4']
        (self.package / 'transport-payload.json').write_text(json.dumps(self.payload))
        path = self.package / 'install.py'
        path.write_text(path.read_text().replace("payload.get('gameVersions') != ['1.1.3']",
                                               "payload.get('gameVersions') != ['1.1.4']"))
        self.make_game(version='1.2.0')
        self.sign(sequence=2)
        result = engine.run_updates(self.state, [{'product':'transport-autopilot','package_root':self.package}],
            self.archive, self.profile, public_key=self.signer.public_key, python=Path(sys.executable).resolve())
        self.assertTrue(result.changed)
        self.assertTrue(result.executable)
        self.make_game(app=b'UNRECOGNIZED_GAME_CODE', version='1.2.1')
        before = self.archive.read_bytes()
        with self.assertRaises((engine.UpdateError, self.installer.InstallError)):
            engine.run_updates(self.state, [{'product':'transport-autopilot','package_root':self.package}],
                self.archive, self.profile, public_key=self.signer.public_key, python=Path(sys.executable).resolve())
        self.assertEqual(before, self.archive.read_bytes())


    def test_exact_hooks_policy_is_signed_bounded_and_preserves_legacy_behavior(self):
        policy = {'mode':'exact-hooks','minimumGameVersion':'1.1.4'}
        payload = {'gameVersions':['1.1.4'],'compatibilityPolicy':policy}
        for version in ('1.1.4', '1.1.5', '1.2.0', '2.0.0'):
            self.assertTrue(engine._allows_game_version(version, payload))
        for version in ('1.1.3', '1.2.0-beta', 'garbage', '01.2.0', '1.1.4;evil', None):
            self.assertFalse(engine._allows_game_version(version, payload))
        self.assertFalse(engine._allows_game_version('1.1.5', {'gameVersions':['1.1.4']}))
        self.payload['compatibilityPolicy'] = policy
        (self.package / 'transport-payload.json').write_text(json.dumps(self.payload))
        self.sign(mutate=lambda m: m.pop('compatibility_policy'))
        with self.assertRaisesRegex(engine.UpdateError, 'authenticated installer payload differ'):
            engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')
        for bad in ({'mode':'anything','minimumGameVersion':'1.1.4'}, {'mode':'exact-hooks','minimumGameVersion':'*'}):
            self.payload['compatibilityPolicy'] = bad
            (self.package / 'transport-payload.json').write_text(json.dumps(self.payload))
            self.sign()
            with self.assertRaises(engine.UpdateError):
                engine.verify_package(self.package, self.signer.public_key, 'transport-autopilot')


    def test_manifest_payload_game_version_mismatch_stops_before_installer_import(self):
        self.sign(game_versions=['1.1.4'])
        with mock.patch.object(engine, '_load_installer') as load:
            with self.assertRaisesRegex(engine.UpdateError, 'authenticated installer payload differ'):
                engine.worker(self.package, 'transport-autopilot', self.archive, self.profile,
                              self.signer.public_key, apply=False)
        load.assert_not_called()

    def test_symlink_package_and_ancestor_rejected(self):
        alias = self.root / 'alias'
        self.symlink(alias, self.package)
        with self.assertRaisesRegex(engine.UpdateError, 'Symlink'):
            engine.verify_package(alias, self.signer.public_key, 'transport-autopilot')

    def test_shared_lock_serializes_products(self):
        with engine.exclusive(self.state):
            with self.assertRaisesRegex(engine.UpdateError, 'in progress'):
                with engine.exclusive(self.state):
                    self.fail('second coordinator acquired the lock')

    def test_fast_path_checks_owned_bytes_and_avoids_archive_bulk(self):
        self.assertEqual(self.apply(), 0)
        # Sparse irrelevant asset space makes full archive hashing measurable.
        with self.archive.open('r+b') as stream:
            stream.truncate(3_500_000_000)
        with mock.patch.object(self.installer, 'digest', side_effect=AssertionError('full file hash')), \
                mock.patch.object(self.installer.registration, 'steam_libraries', return_value=[]):
            self.assertTrue(engine.fast_check(self.installer, self.archive, self.profile))
        if hasattr(self.archive.stat(), 'st_blocks'):
            self.assertLess(self.archive.stat().st_blocks * 512, 1024 * 1024)

    def test_modified_owned_script_stops_without_rewrite(self):
        self.apply()
        parsed = self.installer.AsarArchive.from_file(self.archive)
        app = parsed.read(self.installer.APP_SCRIPT).replace(b'APP_OWN_current', b'APP_OWN_edited')
        sim = parsed.read(self.installer.SIM_SCRIPT)
        self.make_game(app, sim)
        with self.assertRaises((engine.UpdateError, self.installer.InstallError)):
            engine.fast_check(self.installer, self.archive, self.profile)

    def test_fast_path_stops_unsupported_game_and_integrity_mismatch(self):
        self.make_game(version='1.2.0')
        with self.assertRaisesRegex(engine.UpdateError, '1.1.3'):
            engine.fast_check(self.installer, self.archive, self.profile)
        self.make_game()
        self.exe.write_bytes(pe('0' * 64))
        with self.assertRaisesRegex(ValueError, 'integrity'):
            engine.fast_check(self.installer, self.archive, self.profile)

    def test_native_mac_fast_path_validates_plist_and_exact_executable(self):
        app = self.root / 'native/Geoplanetical.app'
        self.archive = app / 'Contents/Resources/app.asar'; self.archive.parent.mkdir(parents=True)
        self.exe = app / 'Contents/MacOS/Geoplanetical'; self.exe.parent.mkdir()
        self.make_game(app=self.installer.apply_section(b'APP_ANCHOR', self.payload['app']),
                       sim=self.installer.apply_section(b'SIM_ANCHOR', self.payload['sim']))
        header_hash = self.installer.sha256(self.installer.AsarArchive.from_file(self.archive).header_bytes)
        info = {'CFBundleExecutable': 'Geoplanetical', 'CFBundleIdentifier': 'com.synthetic.game',
                'ElectronAsarIntegrity': {'Resources/app.asar': {'algorithm': 'SHA256', 'hash': header_hash}}}
        plist = app / 'Contents/Info.plist'; plist.write_bytes(plistlib.dumps(info))
        metadata, _ = self.installer.registration.plan_registration(self.package, self.archive,
                                                                    profile_dir=self.profile, libraries=[])
        metadata[self.installer.state_root(self.archive) / 'installed.json'] = self.installer.encoded({
            'schema': 1, 'id': self.installer.registration.MOD_ID, 'name': self.installer.NAME,
            'version': '1.1.0', 'gameVersion': '1.1.3', 'archive': str(self.archive),
            'payloadSha256': self.installer.sha256(self.installer.encoded({key: self.payload[key] for key in ('app', 'sim')})),
        })
        for path, data in metadata.items():
            path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
        with mock.patch.object(self.installer, 'digest', side_effect=AssertionError('full archive hash')):
            self.assertTrue(engine.fast_check(self.installer, self.archive, self.profile))
        self.assertEqual(engine._executable(self.installer, self.archive), self.exe)
        info['ElectronAsarIntegrity']['Resources/app.asar']['hash'] = '0' * 64
        plist.write_bytes(plistlib.dumps(info))
        with self.assertRaisesRegex(ValueError, 'integrity'):
            engine.fast_check(self.installer, self.archive, self.profile)
        info['CFBundleExecutable'] = '..'; plist.write_bytes(plistlib.dumps(info))
        with self.assertRaisesRegex(engine.UpdateError, 'executable'):
            engine._executable(self.installer, self.archive)

    def test_modified_native_owned_file_stops_before_mutation(self):
        self.apply()
        owned = self.profile / 'mods/transport-autopilot/mod.json'
        owned.write_bytes(owned.read_bytes() + b' ')
        before = self.archive.read_bytes()
        with self.assertRaisesRegex(ValueError, 'changed'):
            engine.fast_check(self.installer, self.archive, self.profile)
        self.assertEqual(before, self.archive.read_bytes())

    def test_modified_installed_record_stops_instead_of_being_rewritten(self):
        self.apply()
        record = self.installer.state_root(self.archive) / 'installed.json'
        original = json.loads(record.read_bytes())
        for changes in ({'payloadSha256': '0' * 64}, {'schema': True}):
            record.write_text(json.dumps({**original, **changes}))
            with self.assertRaisesRegex(engine.UpdateError, 'record changed'):
                engine.fast_check(self.installer, self.archive, self.profile)

    def test_private_labeled_migration_accepts_its_actual_semver_receipt(self):
        current = self.payload.copy()
        private = {**self.payload, 'version': '1.1.1', 'labels': ['1.1.1 private pre-release'],
                   'app': section('APP', 'private'), 'sim': section('SIM', 'private')}
        manifest_path = self.package / 'mod.json'
        manifest = json.loads(manifest_path.read_bytes())
        manifest['version'] = '1.1.1'; manifest_path.write_text(json.dumps(manifest))
        (self.package / 'transport-payload.json').write_text(json.dumps(private))
        self.sign(version='1.1.1')
        self.apply()
        manifest['version'] = '1.1.0'; manifest_path.write_text(json.dumps(manifest))
        (self.package / 'transport-payload.json').write_text(json.dumps(current))
        (self.package / 'legacy_payloads.json').write_text(json.dumps({'schema': 1, 'payloads': [private]}))
        self.sign()
        self.assertFalse(engine.fast_check(self.installer, self.archive, self.profile))
        self.apply()
        self.assertTrue(engine.fast_check(self.installer, self.archive, self.profile))
        record = json.loads((self.installer.state_root(self.archive) / 'installed.json').read_bytes())
        self.assertEqual(record['version'], '1.1.0')

    def test_real_transport_private_legacy_entry_names_explicit_semver(self):
        values = json.loads((ROOT / 'transport-autobuild/installer/legacy_payloads.json').read_bytes())['payloads']
        private = [value for value in values if value.get('labels') == ['1.1.1 private pre-release']]
        self.assertEqual(len(private), 1)
        self.assertEqual(private[0]['version'], '1.1.1')

    def test_default_signed_manual_install_enrolls_and_uninstall_removes_registration(self):
        load_module = self.installer._automatic_module
        def isolated_setup(filename, **options):
            module = load_module(filename, **options)
            if filename == 'update_setup.py':
                module.state_directory = lambda archive: self.state
            if filename == 'steam_setup.py':
                module.configure = mock.Mock(side_effect=lambda archive, state, line, **kw: mock.Mock(launch_options=line))
                module.remove = mock.Mock()
            return module
        args = ['--game', str(self.archive), '--profile', str(self.profile), '--yes']
        with mock.patch.object(self.installer, '_automatic_module', side_effect=isolated_setup), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.installer.main(args), 0)
            config_path = self.state / 'config.json'
            self.assertEqual(json.loads(config_path.read_bytes())['products'],
                             [{'product': 'transport-autopilot', 'package_root': str(self.package)}])
            self.assertTrue((self.state / 'runtime/update_launcher.py').is_file())
            self.assertEqual(self.installer.main(['uninstall'] + args), 0)
        self.assertEqual(json.loads(config_path.read_bytes())['products'], [])
        self.assertFalse((self.profile / 'mods/transport-autopilot/mod.json').exists())

    def test_pending_operation_demands_original_recovery(self):
        pending = self.installer.state_root(self.archive) / 'operations/test/files/pending.json'
        pending.parent.mkdir(parents=True); pending.write_text('{}')
        self.assertFalse(engine.fast_check(self.installer, self.archive, self.profile))

    def test_real_isolated_worker_installs_then_noops_and_callback_holds_lock(self):
        calls = []
        def launch(result):
            calls.append(result.executable)
            with self.assertRaises(engine.UpdateError):
                with engine.exclusive(self.state):
                    pass
        args = (self.state, [{'product': 'transport-autopilot', 'package_root': self.package}], self.archive, self.profile)
        first = engine.run_updates(*args, public_key=self.signer.public_key, python=Path(sys.executable).resolve(), on_ready=launch)
        second = engine.run_updates(*args, public_key=self.signer.public_key, python=Path(sys.executable).resolve())
        self.assertTrue(first.changed)
        self.assertFalse(second.changed)
        self.assertEqual(calls, [self.exe])
        self.assertEqual(second.executable, self.exe)
        self.assertTrue((self.profile / 'mods/transport-autopilot/mod.json').is_file())

    def test_isolated_workers_use_explicit_utf8_for_non_ascii_diagnostics(self):
        def failing_worker(command, **options):
            self.assertEqual(command[1:6], ['-I', '-S', '-X', 'utf8', str(engine.RUNTIME)])
            self.assertEqual(options['encoding'], 'utf-8')
            # Run an isolated real Python child: Windows otherwise uses its
            # ANSI code page because -I ignores a parent's PYTHONUTF8 setting.
            child = command[:5] + ['-c', 'import sys; sys.stderr.write("blocked \u2014 test"); sys.exit(1)']
            return subprocess.run(child, **options)
        with self.assertRaisesRegex(engine.UpdateError, 'blocked \u2014 test'):
            engine.run_updates(self.state, [{'product': 'transport-autopilot', 'package_root': self.package}],
                               self.archive, self.profile, public_key=self.signer.public_key,
                               python=Path(sys.executable).resolve(), runner=failing_worker)

    def test_digest_identity_updates_same_semver_and_rejects_rollback(self):
        args = (self.state, [{'product': 'transport-autopilot', 'package_root': self.package}], self.archive, self.profile)
        engine.run_updates(*args, public_key=self.signer.public_key, python=Path(sys.executable).resolve())
        original = self.payload.copy()
        self.payload = {**self.payload, 'app': section('APP', 'new'), 'sim': section('SIM', 'new')}
        (self.package / 'transport-payload.json').write_text(json.dumps(self.payload))
        (self.package / 'legacy_payloads.json').write_text(json.dumps({'schema': 1, 'payloads': [original]}))
        self.sign(sequence=2)
        result = engine.run_updates(*args, public_key=self.signer.public_key, python=Path(sys.executable).resolve())
        self.assertTrue(result.changed)
        self.sign(sequence=1)
        with self.assertRaisesRegex(engine.UpdateError, 'rollback'):
            engine.run_updates(*args, public_key=self.signer.public_key, python=Path(sys.executable).resolve())
        self.sign(sequence=2, mutate=lambda m: m.update(version='1.1.1'))
        with self.assertRaisesRegex(engine.UpdateError, 'conflicting'):
            engine.run_updates(*args, public_key=self.signer.public_key, python=Path(sys.executable).resolve())

    def test_two_independent_products_share_one_verified_game_and_noop(self):
        tourism = self.tourism_package()
        self.make_game(app=b'FOREIGN;APP_ANCHOR;TOURX_ANCHOR;TAIL',
                       sim=b'FOREIGN;SIM_ANCHOR;TOURY_ANCHOR;TAIL')
        registrations = [{'product': 'transport-autopilot', 'package_root': self.package},
                         {'product': 'tourism-dashboard', 'package_root': tourism}]
        result = engine.run_updates(self.state, registrations, self.archive, self.profile,
                                    public_key=self.signer.public_key, python=Path(sys.executable).resolve())
        self.assertEqual(result.products, ('transport-autopilot', 'tourism-dashboard'))
        self.assertTrue(result.changed)
        self.assertEqual(result.executable, self.exe)
        again = engine.run_updates(self.state, registrations, self.archive, self.profile,
                                   public_key=self.signer.public_key, python=Path(sys.executable).resolve())
        self.assertFalse(again.changed)
        parsed = self.installer.AsarArchive.from_file(self.archive)
        self.assertEqual(parsed.read('foreign.bin'), b'KEEP FOREIGN')
        app = parsed.read(self.installer.APP_SCRIPT)
        self.assertIn(b'__geoTransport', app)
        self.assertIn(b'__geoTourism', app)

    def test_bad_second_native_ownership_stops_before_first_mutation(self):
        tourism = self.tourism_package()
        self.make_game(app=b'APP_ANCHOR;TOURX_ANCHOR', sim=b'SIM_ANCHOR;TOURY_ANCHOR')
        conflict = self.profile / 'mods/tourism-dashboard'
        conflict.mkdir(parents=True); (conflict / 'unowned').write_bytes(b'KEEP')
        before = self.archive.read_bytes()
        with self.assertRaisesRegex(engine.UpdateError, 'unowned'):
            engine.run_updates(self.state, [{'product': 'transport-autopilot', 'package_root': self.package},
                                           {'product': 'tourism-dashboard', 'package_root': tourism}],
                               self.archive, self.profile, public_key=self.signer.public_key,
                               python=Path(sys.executable).resolve())
        self.assertEqual(before, self.archive.read_bytes())
        self.assertFalse((self.profile / 'mods/transport-autopilot').exists())

    def test_three_products_install_noop_and_preserve_unrelated_files(self):
        tourism, vaccination = self.tourism_package(), self.vaccination_package()
        self.make_game(app=b'FOREIGN;APP_ANCHOR;TOURX_ANCHOR;VACX_ANCHOR;TAIL',
                       sim=b'FOREIGN;SIM_ANCHOR;TOURY_ANCHOR;VACY_ANCHOR;TAIL')
        registrations = [{'product': p, 'package_root': root} for p, root in
                         [('transport-autopilot', self.package), ('tourism-dashboard', tourism),
                          ('vaccination-autopilot', vaccination)]]
        kwargs = dict(public_key=self.signer.public_key, python=Path(sys.executable).resolve())
        result = engine.run_updates(self.state, registrations, self.archive, self.profile, **kwargs)
        self.assertEqual(result.products, tuple(r['product'] for r in registrations))
        self.assertTrue(result.changed)
        self.assertFalse(engine.run_updates(self.state, registrations, self.archive, self.profile, **kwargs).changed)
        parsed = self.installer.AsarArchive.from_file(self.archive)
        self.assertEqual(parsed.read('foreign.bin'), b'KEEP FOREIGN')
        for marker in (b'__geoTransport', b'__geoTourism', b'__geoVaccination'):
            self.assertIn(marker, parsed.read(self.installer.APP_SCRIPT))

    def test_bad_third_product_stops_before_any_other_mutation(self):
        tourism, vaccination = self.tourism_package(), self.vaccination_package()
        self.make_game(app=b'APP_ANCHOR;TOURX_ANCHOR;VACX_ANCHOR', sim=b'SIM_ANCHOR;TOURY_ANCHOR;VACY_ANCHOR')
        before = self.archive.read_bytes()
        (vaccination / 'core.py').write_text('raise RuntimeError("unsigned must not execute")')
        with self.assertRaisesRegex(engine.UpdateError, 'verification'):
            engine.run_updates(self.state, [{'product': 'transport-autopilot', 'package_root': self.package},
                                           {'product': 'tourism-dashboard', 'package_root': tourism},
                                           {'product': 'vaccination-autopilot', 'package_root': vaccination}],
                               self.archive, self.profile, public_key=self.signer.public_key,
                               python=Path(sys.executable).resolve())
        self.assertEqual(self.archive.read_bytes(), before)
        self.assertFalse((self.profile / 'mods').exists())

    def test_late_second_failure_preserves_first_successful_sequence_floor(self):
        tourism = self.tourism_package()
        self.make_game(app=b'APP_ANCHOR;TOURX_ANCHOR', sim=b'SIM_ANCHOR;TOURY_ANCHOR')
        registrations = [{'product': 'transport-autopilot', 'package_root': self.package},
                         {'product': 'tourism-dashboard', 'package_root': tourism}]
        kwargs = {'public_key': self.signer.public_key, 'python': Path(sys.executable).resolve()}
        engine.run_updates(self.state, registrations, self.archive, self.profile, **kwargs)
        saved = {path.name: path.read_bytes() for path in self.package.iterdir()}
        original = self.payload.copy()
        self.payload = {**self.payload, 'app': section('APP', 'new'), 'sim': section('SIM', 'new')}
        (self.package / 'transport-payload.json').write_text(json.dumps(self.payload))
        (self.package / 'legacy_payloads.json').write_text(json.dumps({'schema': 1, 'payloads': [original]}))
        self.sign(sequence=2)
        def late_failure(command, **options):
            if '--worker-apply' in command and command[command.index('--product') + 1] == 'tourism-dashboard':
                return subprocess.CompletedProcess(command, 1, '', 'synthetic late second-product failure')
            return subprocess.run(command, **options)
        with self.assertRaisesRegex(engine.UpdateError, 'late second-product failure'):
            engine.run_updates(self.state, registrations, self.archive, self.profile, runner=late_failure, **kwargs)
        self.assertEqual(json.loads((self.state / 'releases.json').read_bytes())['transport-autopilot']['sequence'], 2)
        for name, data in saved.items():
            (self.package / name).write_bytes(data)
        with self.assertRaisesRegex(engine.UpdateError, 'rollback'):
            engine.run_updates(self.state, registrations, self.archive, self.profile, **kwargs)

    def test_changed_enrollment_callback_aborts_under_lock_before_import(self):
        def reject():
            with self.assertRaises(engine.UpdateError):
                with engine.exclusive(self.state):
                    pass
            raise engine.UpdateError('Enrollment changed')
        runner = mock.Mock()
        with self.assertRaisesRegex(engine.UpdateError, 'Enrollment changed'):
            engine.run_updates(self.state, [{'product': 'transport-autopilot', 'package_root': self.package}],
                               self.archive, self.profile, public_key=self.signer.public_key,
                               python=Path(sys.executable).resolve(), runner=runner, before_updates=reject)
        runner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
