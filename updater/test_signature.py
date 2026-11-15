"""OpenSSL interoperability and hostile-input tests using ephemeral test keys.

OpenSSL is a development/test dependency only. No publisher key is used, stored
in this file, or emitted to test output. TestSigner is shared by updater tests.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from updater.signature import (
    ALGORITHM,
    MAX_MANIFEST_BYTES,
    RSA_BYTES,
    SignatureError,
    verify_manifest,
)

TEST_SIGNING_AVAILABLE = bool(shutil.which('openssl') or os.environ.get('LGD_TEST_RSA_KEY'))


class TestSigner:
    """Throwaway publisher for tests; close() removes its private key."""

    __test__ = False
    _fixture_index = 0

    def __init__(self) -> None:
        self._temp = None
        self._n = self._d = None
        fixture = os.environ.get('LGD_TEST_RSA_KEY')
        if fixture:
            # Explicit test-only fallback for isolated Windows VMs without
            # OpenSSL. The two throwaway host-generated keys are transported
            # separately, ignored by Git, and never use the publisher key.
            path = Path(fixture)
            if path.stat().st_size > 32768:
                raise ValueError('Oversized ephemeral RSA test fixture')
            value = json.loads(path.read_text(encoding='utf-8'))
            keys = value.get('keys') if isinstance(value, dict) else None
            if not isinstance(keys, list) or len(keys) < 2:
                raise ValueError('Ephemeral RSA test fixture requires two distinct test keys')
            if len({item.get('n') for item in keys if isinstance(item, dict)}) < 2:
                raise ValueError('Ephemeral RSA test fixture keys must be distinct')
            key = keys[TestSigner._fixture_index % len(keys)]
            TestSigner._fixture_index += 1
            self._n, self._d = int(key['n'], 16), int(key['d'], 16)
            if self._n.bit_length() != RSA_BYTES * 8 or not self._n % 2 or self._d <= 0 or key.get('e') != 65537:
                raise ValueError('Invalid ephemeral RSA test fixture key')
            self.public_key = {'algorithm': ALGORITHM, 'modulus': format(self._n, '0768x'), 'exponent': 65537}
            self.private_key = None  # Fixture mode cannot invoke publisher tooling.
            return
        self._temp = tempfile.TemporaryDirectory(prefix="updater-signature-test-")
        self.private_key = Path(self._temp.name) / "ephemeral.pem"
        try:
            subprocess.run(
                ["openssl", "genpkey", "-algorithm", "RSA", "-pkeyopt", "rsa_keygen_bits:3072",
                 "-pkeyopt", "rsa_keygen_pubexp:65537", "-out", str(self.private_key)],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            modulus = subprocess.check_output(
                ["openssl", "rsa", "-in", str(self.private_key), "-noout", "-modulus"],
                stderr=subprocess.DEVNULL,
            ).decode("ascii").strip().split("=", 1)[1].lower()
            self.public_key = {"algorithm": ALGORITHM, "modulus": modulus, "exponent": 65537}
        except BaseException:
            self.close()
            raise

    def sign(self, data: bytes) -> bytes:
        if self._n is not None:
            digest_info = bytes.fromhex('3031300d060960864801650304020105000420') + hashlib.sha256(data).digest()
            encoded = b'\x00\x01' + b'\xff' * (RSA_BYTES - len(digest_info) - 3) + b'\x00' + digest_info
            return self.raw_sign(encoded)
        return subprocess.check_output(
            ["openssl", "dgst", "-sha256", "-sign", str(self.private_key)],
            input=data, stderr=subprocess.DEVNULL,
        )

    def raw_sign(self, encoded: bytes) -> bytes:
        if self._n is not None:
            if len(encoded) != RSA_BYTES or int.from_bytes(encoded, 'big') >= self._n:
                raise ValueError('Invalid encoded block for ephemeral RSA test key')
            return pow(int.from_bytes(encoded, 'big'), self._d, self._n).to_bytes(RSA_BYTES, 'big')
        return subprocess.check_output(
            ["openssl", "pkeyutl", "-decrypt", "-inkey", str(self.private_key),
             "-pkeyopt", "rsa_padding_mode:none"],
            input=encoded, stderr=subprocess.DEVNULL,
        )

    def close(self) -> None:
        if self._temp is not None:
            self._temp.cleanup()

    def __enter__(self) -> "TestSigner":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


@unittest.skipUnless(TEST_SIGNING_AVAILABLE, "OpenSSL or explicit ephemeral RSA test fixture required")
class SignatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.signer = TestSigner()
        cls.other_signer = TestSigner()
        cls.data = b'{"schema":1,"product":"transport","files":{}}\n'
        cls.signature = cls.signer.sign(cls.data)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.signer.close()
        cls.other_signer.close()

    def verify(self, data: bytes, signature: bytes | None = None, key: dict | None = None) -> dict:
        return verify_manifest(data, self.signature if signature is None else signature,
                               self.signer.public_key if key is None else key)

    def test_valid_openssl_signature(self) -> None:
        self.assertEqual(self.verify(self.data), {"schema": 1, "product": "transport", "files": {}})

    def test_exact_bytes_are_signed(self) -> None:
        for data in (self.data[:-1], self.data + b" ", self.data.replace(b"transport", b"tourism")):
            with self.subTest(data=data), self.assertRaises(SignatureError):
                self.verify(data)

    def test_wrong_key_and_signature_bit_flip(self) -> None:
        with self.assertRaises(SignatureError):
            self.verify(self.data, key=self.other_signer.public_key)
        changed = bytes([self.signature[0] ^ 1]) + self.signature[1:]
        with self.assertRaises(SignatureError):
            self.verify(self.data, changed)

    def test_signature_length_and_range(self) -> None:
        modulus = int(self.signer.public_key["modulus"], 16)
        for signature in (b"", self.signature[:-1], b"\x00" + self.signature,
                          bytes(RSA_BYTES), modulus.to_bytes(RSA_BYTES, "big"), b"\xff" * RSA_BYTES):
            with self.subTest(length=len(signature)), self.assertRaises(SignatureError):
                self.verify(self.data, signature)

    def test_manifest_bounds_and_immutable_byte_types(self) -> None:
        for data in (b"", b" " * (MAX_MANIFEST_BYTES + 1), bytearray(self.data), "{}", None):
            with self.subTest(type=type(data)), self.assertRaises(SignatureError):
                self.verify(data)
        with self.assertRaises(SignatureError):
            self.verify(self.data, bytearray(self.signature))

    def test_invalid_pinned_key_configuration(self) -> None:
        cases = [None, {}, {**self.signer.public_key, "exponent": 3},
                 {**self.signer.public_key, "exponent": True},
                 {**self.signer.public_key, "algorithm": "rsa-pss-sha256"},
                 {**self.signer.public_key, "extra": "downloaded-key"},
                 {**self.signer.public_key, "modulus": self.signer.public_key["modulus"].upper()},
                 {**self.signer.public_key, "modulus": "f" * 767},
                 {**self.signer.public_key, "modulus": "7" + "f" * 767},
                 {**self.signer.public_key, "modulus": "f" * 767 + "e"}]
        for key in cases:
            with self.subTest(key=key), self.assertRaises(SignatureError):
                verify_manifest(self.data, self.signature, key)

    def test_authenticated_json_must_be_unambiguous_object(self) -> None:
        cases = [b'[]', b'null', b'42', b'{"a":1,"a":2}', b'{"a":{"b":1,"b":2}}',
                 b'{"a":NaN}', b'{"a":Infinity}', b'{"a":-Infinity}', b'{"a":1e999}', b'{"a":1,}',
                 b'{} trailing', b'\xff{}', b'\xef\xbb\xbf{}', b'{"a":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}']
        for data in cases:
            with self.subTest(prefix=data[:30]), self.assertRaises(SignatureError):
                self.verify(data, self.signer.sign(data))

    def test_json_is_parsed_only_after_authentication(self) -> None:
        # Both are failures, but an unauthenticated JSON body never reaches parsing.
        with self.assertRaisesRegex(SignatureError, "does not match"):
            self.verify(b'{"a":1,"a":2}')

    def test_strict_emsa_encoding_rejects_hostile_blocks(self) -> None:
        digest_info = bytes.fromhex("3031300d060960864801650304020105000420") + hashlib.sha256(self.data).digest()
        valid = b"\x00\x01" + b"\xff" * (RSA_BYTES - len(digest_info) - 3) + b"\x00" + digest_info
        # Raw RSA signing makes each malformed encoded message mathematically
        # valid under the test key. Only strict EMSA verification rejects it.
        blocks = [
            b"\x00\x02" + valid[2:],                         # encryption block type
            valid[:20] + b"\xfe" + valid[21:],                # non-FF padding
            valid[:20] + b"\x00" + valid[21:],                # premature separator
            b"\x00\x01" + b"\xff" * 7 + b"\x00" + digest_info + bytes(RSA_BYTES - len(digest_info) - 10),
            valid[:-1] + bytes([valid[-1] ^ 1]),                 # altered digest
            valid[:-len(digest_info)] + b"\x30\x32" + digest_info[2:],  # altered DER length
            valid[:-1] + b"\x00",                             # trailing garbage in digest
            (b"\x00\x01" + b"\xff" * (RSA_BYTES - len(digest_info) - 4)
             + b"\x00" + digest_info + b"\x00"),              # DigestInfo suffix
        ]
        self.assertEqual(self.verify(self.data, self.signer.raw_sign(valid))["schema"], 1)
        for index, block in enumerate(blocks):
            self.assertEqual(len(block), RSA_BYTES)
            with self.subTest(block=index), self.assertRaises(SignatureError):
                self.verify(self.data, self.signer.raw_sign(block))


if __name__ == "__main__":
    unittest.main()
