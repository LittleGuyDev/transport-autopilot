"""Pinned RSA-3072/SHA-256 verification for exact release-manifest bytes.

Only public-key operations live here. The publisher uses OpenSSL for signing;
client machines need only Python's standard library. This deliberately supports
one key size, exponent and encoding. RFC 8017 sections 8.2.2 and 9.2 specify
reconstructing the complete encoded message and comparing it with RSAVP1 output.
No ASN.1 parser, padding search, algorithm negotiation or downloaded key is used.

Authenticity is only one gate: callers must enforce schema, freshness, rollback,
package hashes, safe paths and the installer's existing write-safety checks.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import re
from collections.abc import Mapping
from typing import Any

ALGORITHM = "rsa-pkcs1v15-sha256"
RSA_BYTES = 384
MAX_MANIFEST_BYTES = 1_048_576
MAX_JSON_DEPTH = 64
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")
_MODULUS_RE = re.compile(r"[0-9a-f]{768}\Z")


class SignatureError(ValueError):
    """The manifest or detached signature cannot be authenticated safely."""


def _public_modulus(public_key: Mapping[str, Any]) -> int:
    if not isinstance(public_key, Mapping):
        raise SignatureError("Pinned public key must be a mapping.")
    if set(public_key) != {"algorithm", "modulus", "exponent"}:
        raise SignatureError("Pinned public key has unexpected fields.")
    if public_key["algorithm"] != ALGORITHM:
        raise SignatureError("Pinned public key uses an unsupported algorithm.")
    exponent = public_key["exponent"]
    if type(exponent) is not int or exponent != 65537:
        raise SignatureError("Pinned public key must use exponent 65537.")
    modulus_hex = public_key["modulus"]
    if not isinstance(modulus_hex, str) or not _MODULUS_RE.fullmatch(modulus_hex):
        raise SignatureError("Pinned public key modulus is malformed.")
    modulus = int(modulus_hex, 16)
    if modulus.bit_length() != RSA_BYTES * 8 or modulus % 2 == 0:
        raise SignatureError("Pinned public key must be an odd 3072-bit modulus.")
    return modulus


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise SignatureError("Manifest JSON contains duplicate object keys.")
        result[key] = value
    return result


def _reject_constant(_value: str) -> Any:
    raise SignatureError("Manifest JSON contains a non-finite number.")


def _finite_float(value: str) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise SignatureError("Manifest JSON contains a non-finite number.")
    return result


def _check_depth(value: Any) -> None:
    pending = [(value, 1)]
    while pending:
        item, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise SignatureError("Manifest JSON exceeds the maximum nesting depth.")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)


def verify_manifest(
    manifest_bytes: bytes,
    signature_bytes: bytes,
    public_key: Mapping[str, Any],
) -> dict[str, Any]:
    """Authenticate exact bytes, then parse unambiguous UTF-8 JSON.

    ``signature_bytes`` is a raw 384-byte RSA signature, not base64 or hex.
    ``public_key`` must come from trusted installed code, never the package.
    All expected input/decoding failures raise ``SignatureError``. A returned
    object is authenticated but its application-specific fields still need
    validation. Whitespace is covered by the signature; no canonicalization or
    newline conversion is performed.
    """
    if type(manifest_bytes) is not bytes or not 0 < len(manifest_bytes) <= MAX_MANIFEST_BYTES:
        raise SignatureError("Manifest must contain bounded, nonempty bytes.")
    if type(signature_bytes) is not bytes or len(signature_bytes) != RSA_BYTES:
        raise SignatureError("Detached signature must contain exactly 384 bytes.")
    modulus = _public_modulus(public_key)
    signature_int = int.from_bytes(signature_bytes, "big")
    if signature_int >= modulus:
        raise SignatureError("Detached signature is out of range.")

    digest_info = _SHA256_DIGEST_INFO + hashlib.sha256(manifest_bytes).digest()
    expected = b"\x00\x01" + b"\xff" * (RSA_BYTES - len(digest_info) - 3) + b"\x00" + digest_info
    actual = pow(signature_int, 65537, modulus).to_bytes(RSA_BYTES, "big")
    if not hmac.compare_digest(actual, expected):
        raise SignatureError("Manifest signature does not match the pinned publisher key.")

    try:
        manifest = json.loads(
            manifest_bytes.decode("utf-8", errors="strict"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
            parse_float=_finite_float,
        )
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, SignatureError):
            raise
        raise SignatureError("Authenticated manifest is not valid bounded UTF-8 JSON.") from exc
    if not isinstance(manifest, dict):
        raise SignatureError("Authenticated manifest must be a JSON object.")
    _check_depth(manifest)
    return manifest
