"""Future game releases remain gated by exact source hooks, not version equality."""
import unittest
from core import InstallError, allows_game_version, apply_section, validate_compatibility_policy

class CompatibilityPolicy(unittest.TestCase):
    def setUp(self):
        self.payload = {'gameVersions':['1.1.4'], 'compatibilityPolicy': {'mode':'exact-hooks','minimumGameVersion':'1.1.4'}}
    def test_newer_stable_versions(self):
        for version in ('1.1.4','1.1.5','1.2.0','2.0.0'):
            self.assertTrue(allows_game_version({'version':version}, self.payload))
    def test_older_and_malformed_versions(self):
        for version in ('1.1.3','1.0.0','1.1.5-beta','01.1.5','1.2','99999.0.0',None,123):
            self.assertFalse(allows_game_version({'version':version},self.payload))
    def test_legacy_remains_exact(self):
        self.assertFalse(allows_game_version({'version':'1.1.5'}, {'gameVersions':['1.1.4']}))
    def test_unknown_or_malformed_policy(self):
        for policy in ({}, {'mode':'permissive','minimumGameVersion':'1.1.4'}, {'mode':'exact-hooks','minimumGameVersion':'x'}, {'mode':'exact-hooks','minimumGameVersion':'1.1.4','extra':True}):
            with self.assertRaises(InstallError): validate_compatibility_policy({'compatibilityPolicy':policy})
    def test_future_version_still_requires_exact_hooks(self):
        self.assertTrue(allows_game_version({'version':'1.2.0'}, self.payload))
        with self.assertRaises(InstallError): apply_section(b'native changed', {'patches':[['known hook','patched hook']]})

if __name__ == '__main__': unittest.main()
