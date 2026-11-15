"""Synthetic-only native registration tests; never target a real profile."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zlib
import struct

import registration as reg


def png():
    def chunk(kind, value):
        data = kind + value
        return struct.pack('>I', len(value)) + data + struct.pack('>I', zlib.crc32(data) & 0xffffffff)
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0)) +
            chunk(b'IDAT', zlib.compress(b'\0\0\0\0\xff')) + chunk(b'IEND', b''))


class RegistrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.package = self.root / 'package'
        self.package.mkdir()
        manifest = dict(id=reg.MOD_ID, name='Transport Autopilot', version='1.0.0', contentApi=1,
                        tier='presentation', thumbnail='thumbnail.png', requires=[], provides={},
                        description='Test content pack', authors=['Little Guy Dev'])
        (self.package / 'mod.json').write_text(json.dumps(manifest))
        (self.package / 'thumbnail.png').write_bytes(png())
        self.profile = self.root / 'profile'
        self.archive = self.root / 'steamapps/common/Geoplanetical/resources/app.asar'
        self.archive.parent.mkdir(parents=True)
        self.archive.write_bytes(b'fixture')

    def tearDown(self):
        self.temp.cleanup()

    def plan(self):
        return reg.plan_registration(self.package, self.archive, profile_dir=self.profile, libraries=[])

    def removal(self):
        return reg.plan_removal(self.package, self.archive, profile_dir=self.profile, libraries=[])

    def apply(self, plan):
        for path, data in plan.items():
            if data is None:
                if path.exists(): path.unlink()
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)

    def workshop(self):
        path = self.root / 'steamapps/workshop/content/940230/3839206386'
        shutil.copytree(self.package, path)
        return path

    def test_plan_idempotency_and_uninstall_preserves_preferences_and_extras(self):
        changes, status = self.plan()
        self.assertFalse(self.profile.exists())
        self.assertEqual(len(changes), 4)
        self.assertEqual(status['source'], 'local')
        self.apply(changes)
        self.assertEqual(self.plan()[0], {})
        preferences = self.profile / 'mods.json'
        preferences.write_bytes(b'user preferences')
        extra = self.profile / 'mods/transport-autopilot/extra.txt'
        extra.write_bytes(b'user extra')
        self.apply(self.removal()[0])
        self.assertEqual(preferences.read_bytes(), b'user preferences')
        self.assertEqual(extra.read_bytes(), b'user extra')
        self.assertEqual(self.removal()[0], {})

    def test_workshop_migrates_owned_fallback_only(self):
        self.apply(self.plan()[0])
        workshop = self.workshop()
        changes,status = self.plan()
        self.assertEqual(status['source'], 'workshop')
        self.assertEqual(len(changes), 4)
        self.assertTrue(all(value is None for value in changes.values()))
        self.apply(changes)
        self.assertTrue((workshop / 'mod.json').is_file())
        self.assertEqual(self.plan()[0], {})

    def test_changed_owned_file_blocks_removal_and_migration(self):
        self.apply(self.plan()[0])
        (self.profile / 'mods/transport-autopilot/mod.json').write_text('changed')
        with self.assertRaises(ValueError): self.removal()
        self.workshop()
        with self.assertRaises(ValueError): self.plan()

    def test_duplicate_id_and_symlink_parent_refused(self):
        self.apply(self.plan()[0])
        shutil.copytree(self.package, self.profile / 'mods/duplicate')
        with self.assertRaises(ValueError): self.plan()
        shutil.rmtree(self.profile / 'mods/duplicate')
        alias = self.root / 'alias'
        try: alias.symlink_to(self.profile, target_is_directory=True)
        except OSError: self.skipTest('Symlinks unavailable')
        with self.assertRaises(ValueError):
            reg.plan_registration(self.package, self.archive, profile_dir=alias, libraries=[])

    def test_unowned_folder_requires_exact_files_before_adoption(self):
        changes,_ = self.plan()
        receipt = self.profile / '.transport-autopilot-installer/native-registration.json'
        self.apply({p:b for p,b in changes.items() if p != receipt})
        self.assertEqual(set(self.plan()[0]), {receipt})
        (self.profile / 'mods/transport-autopilot/workshop.json').write_text('{}')
        with self.assertRaises(ValueError): self.plan()

    def test_empty_folder_is_reusable_after_uninstall(self):
        self.apply(self.plan()[0])
        self.apply(self.removal()[0])
        folder = self.profile / 'mods' / reg.FOLDER
        self.assertTrue(folder.is_dir())
        self.assertEqual(list(folder.iterdir()), [])
        changes, status = self.plan()
        self.assertEqual(status['source'], 'local')
        self.assertEqual(len(changes), 4)
        self.assertEqual(list(folder.iterdir()), [])  # Planning remains read-only.
        self.apply(changes)
        self.assertEqual(self.plan()[0], {})

    def test_nonempty_unowned_folder_is_still_refused(self):
        folder = self.profile / 'mods' / reg.FOLDER
        folder.mkdir(parents=True)
        extra = folder / 'user-file.txt'
        extra.write_bytes(b'KEEP USER FILE')
        with self.assertRaisesRegex(ValueError, 'unowned'):
            self.plan()
        self.assertEqual(list(folder.iterdir()), [extra])
        self.assertEqual(extra.read_bytes(), b'KEEP USER FILE')

    def test_workshop_without_native_manifest_uses_local_fallback(self):
        path = self.root / 'steamapps/workshop/content/940230/3839206386'
        path.mkdir(parents=True)
        (path / 'readme.txt').write_text('Existing non-native Workshop download')
        changes,status = self.plan()
        self.assertEqual(status['source'], 'local')
        self.assertEqual(len(changes), 4)
        self.assertTrue((path / 'readme.txt').is_file())

    def test_corrupt_png_refused(self):
        (self.package / 'thumbnail.png').write_bytes(png()[:-5])
        with self.assertRaises(ValueError): self.plan()

    def test_wrong_receipt_refused(self):
        self.apply(self.plan()[0])
        receipt = self.profile / '.transport-autopilot-installer/native-registration.json'
        data = json.loads(receipt.read_text())
        data['files']['../../bad'] = '0' * 64
        receipt.write_text(json.dumps(data))
        with self.assertRaises(ValueError): self.removal()


if __name__ == '__main__':
    unittest.main()
