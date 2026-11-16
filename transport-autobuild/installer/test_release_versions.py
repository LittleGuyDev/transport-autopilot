"""Release numbers may be reused; ownership must still match exact payload bytes."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

import core

PRODUCT = Path(__file__).resolve().parents[1]
TARGET = '1.1.1'
PRIOR = '1.1.1'
PAYLOAD = 'transport-payload.json'
PRIOR_FINGERPRINT = 'b565291cebe98dc2e0d2db0f78ec09e55ae2f049a00307c369a60ff939bb1cd0'


class ReleaseVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.dist = Path(cls.temporary.name) / 'dist'
        spec = importlib.util.spec_from_file_location('release_version_builder', PRODUCT / 'build.py')
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        builder.DIST = cls.dist
        with contextlib.redirect_stdout(io.StringIO()):
            builder.build()
        cls.payload = json.loads((cls.dist / PAYLOAD).read_text())
        cls.legacy = json.loads((cls.dist / 'legacy_payloads.json').read_text())['payloads']

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def test_built_versions_and_runtime_agree(self):
        self.assertEqual(json.loads((self.dist / 'mod.json').read_text())['version'], TARGET)
        self.assertEqual(self.payload['version'], TARGET)
        for filename, literal in [('manager-ui.js', "VERSION = '1.1.1'"), ('ui.js', "version: '1.1.1'")]:
            self.assertIn(literal, (PRODUCT / filename).read_text())
        if (self.dist / 'geotransport.json').exists():
            data = json.loads((self.dist / 'geotransport.json').read_text())
            self.assertEqual(data['version'], TARGET)
            self.assertEqual(data['nativePack']['manifest']['version'], TARGET)

    def test_private_candidate_is_preserved_exactly(self):
        match = next(p for p in self.legacy if PRIOR + ' private pre-release' in p.get('labels', []))
        fingerprint = hashlib.sha256(json.dumps({key: match[key] for key in ('app', 'sim')}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(fingerprint, PRIOR_FINGERPRINT)
        self.assertEqual(match['fingerprint'], PRIOR_FINGERPRINT)

    def test_reused_number_keeps_distinct_original_payload(self):
        old = next(p for p in self.legacy if p.get('version') == '1.1.0' or '1.1.0' in p.get('labels', []))
        fingerprint = lambda p: hashlib.sha256(json.dumps({key: p[key] for key in ('app', 'sim')}, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        self.assertEqual(fingerprint(old), '27c72f7618fee597cffebe36791d054495799cd84f13ffb318bb87ebd086b028')
        self.assertNotEqual(fingerprint(old), PRIOR_FINGERPRINT)
        self.assertNotEqual(fingerprint(old), fingerprint(self.payload))
        self.assertNotEqual(PRIOR_FINGERPRINT, fingerprint(self.payload))

    def test_real_previous_shapes_upgrade_and_remove_with_other_wrappers(self):
        previous = [next(p for p in self.legacy if p.get('version') == '1.1.0' or '1.1.0' in p.get('labels', [])),
                    next(p for p in self.legacy if PRIOR + ' private pre-release' in p.get('labels', []))]
        for old in previous:
            with self.subTest(old=old.get('labels', old.get('version'))):
                bases = {}
                installed = {}
                for key in ('app', 'sim'):
                    anchors = list(dict.fromkeys(pair[0] for section in (old[key], self.payload[key]) for pair in section['patches']))
                    maximal = [a for a in anchors if not any(a != b and a in b for b in anchors)]
                    bases[key] = ('FOREIGN_HEAD;\n' + ';\n'.join(maximal) + '\n;FOREIGN_TAIL').encode()
                    installed[key] = b'FOREIGN_OUTER;\n' + core.apply_section(bases[key], old[key]) + b'\n;FOREIGN_AFTER'
                base_app, base_sim, detected = core.reverse_own_payload(installed['app'], installed['sim'], [self.payload] + self.legacy)
                self.assertEqual(base_app, b'FOREIGN_OUTER;\n' + bases['app'] + b'\n;FOREIGN_AFTER')
                self.assertEqual(base_sim, b'FOREIGN_OUTER;\n' + bases['sim'] + b'\n;FOREIGN_AFTER')
                new_app = core.transition_section(installed['app'], detected['app'], self.payload['app'])
                new_sim = core.transition_section(installed['sim'], detected['sim'], self.payload['sim'])
                restored_app, restored_sim, _ = core.reverse_own_payload(new_app, new_sim, [self.payload] + self.legacy)
                self.assertEqual((restored_app, restored_sim), (base_app, base_sim))


if __name__ == '__main__':
    unittest.main()
