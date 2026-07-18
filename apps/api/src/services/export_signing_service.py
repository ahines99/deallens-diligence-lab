"""G74 — Ed25519 export attestation over the canonical export/bundle manifest bytes.

Extends the existing hash discipline (``workspace_bundle_service`` / ``ic_export_service``) to
cryptographic provenance: when ``EXPORT_SIGNING_KEY`` is configured, exports carry an
``attestation`` block whose Ed25519 signature covers **exactly the canonical bytes the content
hash already covers**, so hash and signature attest the same thing:

* IC file exports — the canonical manifest JSON *excluding* the ``attestation`` block itself
  (``sort_keys=True, separators=(",", ":")``); its SHA-256 is the stored ``manifest_hash``.
* Workspace bundles — the canonical ordered file-digest JSON whose SHA-256 is ``bundle_sha256``.

The key is a base64url-encoded 32-byte private seed. When it is unset or malformed, exports
degrade honestly to hash-only verification (``attestation: {"status": "unsigned", "note": ...}``)
— never a silent skip and never a crash. :func:`verify` is pure and fully offline: it needs only
the manifest bytes, the signature, and the embedded public key (plain ``cryptography``), so a
recipient can verify a bundle with no access to this server.
"""
from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from src.config import settings

ALGORITHM = "Ed25519"


def canonical_manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """Canonical JSON bytes of a manifest EXCLUDING its ``attestation`` block.

    These are the exact bytes both hashed (``manifest_hash``) and signed, so the signature can
    never be self-referential and hash + signature always attest the same content.
    """
    core = {key: value for key, value in manifest.items() if key != "attestation"}
    return json.dumps(core, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _load_private_key() -> tuple[Ed25519PrivateKey | None, str]:
    raw = (settings.export_signing_key or "").strip()
    if not raw:
        return None, (
            "EXPORT_SIGNING_KEY is not configured; exports are hash-verified only"
        )
    try:
        seed = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except (ValueError, binascii.Error):
        return None, (
            "EXPORT_SIGNING_KEY is not valid base64url; exports are hash-verified only"
        )
    if len(seed) != 32:
        return None, (
            f"EXPORT_SIGNING_KEY must decode to a 32-byte Ed25519 seed (got {len(seed)} bytes); "
            "exports are hash-verified only"
        )
    return Ed25519PrivateKey.from_private_bytes(seed), ""


def available() -> tuple[bool, str]:
    """Whether export signing is usable, with an honest note when it is not."""
    key, note = _load_private_key()
    return (key is not None), note


def public_key_b64() -> str | None:
    """Base64 of the raw 32-byte Ed25519 public key, or ``None`` when signing is unavailable."""
    key, _ = _load_private_key()
    if key is None:
        return None
    raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def sign(manifest_bytes: bytes) -> dict[str, str]:
    """Sign canonical manifest bytes; raises ``RuntimeError`` when no usable key is configured."""
    key, note = _load_private_key()
    if key is None:
        raise RuntimeError(note)
    public_raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return {
        "signature_b64": base64.b64encode(key.sign(manifest_bytes)).decode("ascii"),
        "public_key_b64": base64.b64encode(public_raw).decode("ascii"),
        "algorithm": ALGORITHM,
    }


def verify(manifest_bytes: bytes, signature_b64: str, public_key_b64: str) -> bool:
    """Pure, offline signature check: True only if the signature binds these exact bytes.

    Never raises — a malformed signature/key or any single flipped byte simply returns False.
    Deliberately independent of ``settings`` so third parties can mirror it with plain
    ``cryptography``.
    """
    try:
        signature = base64.b64decode(signature_b64 or "", validate=True)
        public_raw = base64.b64decode(public_key_b64 or "", validate=True)
        Ed25519PublicKey.from_public_bytes(public_raw).verify(signature, manifest_bytes)
        return True
    except Exception:  # noqa: BLE001 - any failure is exactly "does not verify"
        return False


def build_attestation(payload: bytes, *, signed_payload: str) -> dict[str, str]:
    """The manifest ``attestation`` block: signed when the key is usable, else honestly unsigned.

    ``signed_payload`` documents (for the offline verifier) exactly which bytes the signature
    covers — always the same canonical bytes the manifest's content hash covers.
    """
    ok, note = available()
    if not ok:
        return {"status": "unsigned", "note": note}
    block = sign(payload)
    return {
        "status": "signed",
        "signed_payload": signed_payload,
        **block,
    }


__all__ = [
    "ALGORITHM",
    "available",
    "build_attestation",
    "canonical_manifest_bytes",
    "public_key_b64",
    "sign",
    "verify",
]
