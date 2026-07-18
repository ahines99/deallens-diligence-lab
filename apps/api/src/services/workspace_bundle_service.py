"""G45 — Workspace export bundle: IC memo PDF + evidence appendix + hash manifest.

Assembles a single, self-describing ZIP that a reviewer can verify **offline**: the embedded
``manifest.json`` records a SHA-256 for every file plus a ``bundle_sha256`` rollup, and
:func:`verify_bundle` re-reads the archive, recomputes each digest, and confirms it matches the
manifest — the same recompute-and-compare tamper-detection contract as the IC packet verifier
(``deal_workflow_service.verify_export_manifest`` / ``ic_export_service``).

Determinism: the rendered PDF is built in reportlab's ``invariant`` mode (fixed document date and
id) and the evidence appendix is CSV, so identical inputs render byte-identical files. The
``bundle_sha256`` is computed purely over the ordered ``(name, sha256)`` file digests and therefore
**excludes** the wall-clock ``generated_at`` — it is stable across rebuilds of the same workspace.

G74 — attestation: when ``EXPORT_SIGNING_KEY`` is configured the manifest additionally carries an
Ed25519 ``attestation`` block whose signature covers **exactly** the canonical file-digest JSON
whose SHA-256 is ``bundle_sha256`` (see :func:`_canonical_file_digests`), so hash and signature
attest the same bytes. The bundled ``VERIFY.md`` documents the full offline recipe (hashlib +
plain ``cryptography``) and embeds the public key; when the key is unset the block honestly reads
``{"status": "unsigned", "note": ...}`` and hash discipline is unchanged.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy.orm import Session

from src.services import evidence_service, export_signing_service
from src.services.common import get_workspace_or_404
from src.services.memo_generation_service import get_ic_memo

SCHEMA_VERSION = "1.0"
MANIFEST_NAME = "manifest.json"
MEMO_NAME = "ic-memo.pdf"
EVIDENCE_NAME = "evidence-appendix.csv"
VERIFY_NAME = "VERIFY.md"
# Fixed archive member timestamp so the raw ZIP is reproducible too (bundle_sha256 does not
# depend on it, but a stable archive is friendlier to byte-level diffs).
_FIXED_ZIP_DATE = (1980, 1, 1, 0, 0, 0)

_EVIDENCE_COLUMNS = [
    "ref",
    "claim_type",
    "claim",
    "source_name",
    "source_type",
    "source_url",
    "source_date",
    "evidence_text",
    "confidence",
    "agent_name",
]


@dataclass(frozen=True)
class WorkspaceBundle:
    content: bytes
    filename: str
    bundle_sha256: str
    manifest: dict


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_file_digests(entries: list[tuple[str, str]]) -> bytes:
    """Canonical JSON bytes of the ordered ``(filename, file_sha256)`` pairs.

    These exact bytes are BOTH hashed into ``bundle_sha256`` and signed by the G74 attestation,
    so the hash and the signature always attest the same content.
    """
    ordered = sorted(({"name": name, "sha256": digest} for name, digest in entries),
                     key=lambda item: item["name"])
    return json.dumps(ordered, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _bundle_digest(entries: list[tuple[str, str]]) -> str:
    """Roll up ordered ``(filename, file_sha256)`` pairs into one stable digest.

    Excludes wall-clock time and raw ZIP framing, so it is deterministic for identical inputs and
    still changes if any file's bytes change (whichever file is tampered).
    """
    return hashlib.sha256(_canonical_file_digests(entries)).hexdigest()


def _render_memo_pdf(title: str, markdown: str) -> bytes:
    """Render the IC memo markdown to a deterministic PDF (reportlab invariant mode)."""
    styles = getSampleStyleSheet()
    story: list = [Paragraph(escape(title or "IC Memo"), styles["Title"]), Spacer(1, 0.15 * inch)]
    for raw_line in (markdown or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            story.append(Spacer(1, 0.08 * inch))
            continue
        if stripped.startswith("#"):
            hashes = len(stripped) - len(stripped.lstrip("#"))
            text = stripped.lstrip("#").strip()
            style = styles[f"Heading{min(max(hashes, 1), 3)}"]
            story.append(Paragraph(escape(text) or "&nbsp;", style))
        else:
            story.append(Paragraph(escape(stripped), styles["BodyText"]))
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=LETTER,
        leftMargin=0.6 * inch,
        rightMargin=0.6 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title=title or "IC Memo",
        invariant=1,
    )
    document.build(story)
    return buffer.getvalue()


def _render_evidence_csv(evidence: list) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(_EVIDENCE_COLUMNS)
    for item in evidence:
        writer.writerow([
            item.ref,
            item.claim_type,
            item.claim,
            item.source_name,
            item.source_type,
            item.source_url or "",
            item.source_date or "",
            item.evidence_text,
            f"{item.confidence:.3f}",
            item.agent_name,
        ])
    return buffer.getvalue().encode("utf-8")


def _render_verify_readme() -> bytes:
    """Deterministic offline-verification note shipped inside the bundle (G74).

    Documents the existing hash recipe plus the Ed25519 attestation and embeds the public key,
    so a recipient needs only ``manifest.json``, ``hashlib``, and plain ``cryptography``. The
    content is deterministic for a given signing configuration, so it never perturbs the
    bundle's reproducibility guarantees.
    """
    signed, note = export_signing_service.available()
    if signed:
        public_key_b64 = export_signing_service.public_key_b64()
        attestation_section = (
            "This bundle's `manifest.json` carries a signed `attestation` block "
            "(`status: \"signed\"`).\n\n"
            "The Ed25519 signature covers EXACTLY the canonical file-digest JSON described in "
            "step 2 —\nthe same bytes whose SHA-256 is `bundle_sha256` — so the hash and the "
            "signature attest the\nsame content. Verify with plain `cryptography`:\n\n"
            "```python\n"
            "import base64, hashlib, json\n"
            "from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey\n"
            "\n"
            "manifest = json.load(open(\"manifest.json\"))\n"
            "entries = sorted(\n"
            "    ({\"name\": f[\"name\"], \"sha256\": f[\"sha256\"]} for f in manifest[\"files\"]),\n"
            "    key=lambda e: e[\"name\"],\n"
            ")\n"
            "payload = json.dumps(entries, sort_keys=True, separators=(\",\", \":\")).encode()\n"
            "assert hashlib.sha256(payload).hexdigest() == manifest[\"bundle_sha256\"]\n"
            "att = manifest[\"attestation\"]\n"
            "Ed25519PublicKey.from_public_bytes(base64.b64decode(att[\"public_key_b64\"])).verify(\n"
            "    base64.b64decode(att[\"signature_b64\"]), payload\n"
            ")  # raises InvalidSignature if ANY listed digest was altered\n"
            "```\n\n"
            "Recompute each file's SHA-256 first (step 1); the signature then proves the digest "
            "list\nitself was not rewritten. Expected signer public key (base64):\n\n"
            f"    {public_key_b64}\n"
        )
    else:
        attestation_section = (
            "This bundle is NOT signed. `manifest.json` records "
            "`attestation: {\"status\": \"unsigned\"}`\nwith this note:\n\n"
            f"    {note}\n\n"
            "Hash verification (steps 1-2) still detects any tampering of the bundled files, but "
            "a\nrewritten manifest cannot be distinguished from a re-generated one without a "
            "signature.\n"
        )
    text = (
        "# Verifying this bundle offline\n"
        "\n"
        "## 1. File hashes\n"
        "\n"
        "`manifest.json` lists every bundled file under `files` with its SHA-256. Recompute each\n"
        "digest (`hashlib.sha256(open(name, \"rb\").read()).hexdigest()`) and compare.\n"
        "\n"
        "## 2. Bundle roll-up\n"
        "\n"
        "`bundle_sha256` is the SHA-256 of the canonical JSON array of the ordered file digests:\n"
        "`json.dumps(sorted([{\"name\": ..., \"sha256\": ...}], key=name), sort_keys=True,`\n"
        "`separators=(\",\", \":\"))`. It deliberately excludes the wall-clock `generated_at`.\n"
        "\n"
        "## 3. Ed25519 attestation\n"
        "\n" + attestation_section
    )
    return text.encode("utf-8")


def build_bundle(session: Session, workspace_id: str) -> WorkspaceBundle:
    """Assemble the verifiable ZIP for a workspace (IC memo PDF + evidence appendix + manifest)."""
    get_workspace_or_404(session, workspace_id)
    memo = get_ic_memo(session, workspace_id)  # raises NotFound if not generated yet
    evidence = evidence_service.list_evidence(session, workspace_id)

    memo_pdf = _render_memo_pdf(memo.title or "Investment Committee Memo", memo.markdown_content)
    evidence_csv = _render_evidence_csv(evidence)
    verify_readme = _render_verify_readme()

    rendered = [(MEMO_NAME, memo_pdf), (EVIDENCE_NAME, evidence_csv), (VERIFY_NAME, verify_readme)]
    files = [
        {"name": name, "sha256": _sha256(data), "bytes": len(data)}
        for name, data in rendered
    ]
    memo_sha256 = files[0]["sha256"]
    digest_entries = [(item["name"], item["sha256"]) for item in files]
    bundle_sha256 = _bundle_digest(digest_entries)
    # G74: the signature covers the SAME canonical bytes bundle_sha256 hashes, so signing adds
    # provenance without changing the hash discipline; unset key => honest "unsigned" block.
    attestation = export_signing_service.build_attestation(
        _canonical_file_digests(digest_entries),
        signed_payload=(
            "canonical ordered file-digest JSON (the exact bytes whose SHA-256 is bundle_sha256)"
        ),
    )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "workspace_id": workspace_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "bundle_sha256": bundle_sha256,
        "memo_sha256": memo_sha256,
        "evidence_count": len(evidence),
        "attestation": attestation,
    }
    manifest_bytes = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in [*rendered, (MANIFEST_NAME, manifest_bytes)]:
            info = zipfile.ZipInfo(name, date_time=_FIXED_ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data)

    filename = f"workspace-{workspace_id}-bundle.zip"
    return WorkspaceBundle(
        content=buffer.getvalue(),
        filename=filename,
        bundle_sha256=bundle_sha256,
        manifest=manifest,
    )


def verify_bundle(zip_bytes: bytes) -> dict:
    """Re-read a bundle, recompute every file's SHA-256, and confirm the manifest still binds.

    Returns ``{valid, checks:[{name, expected, actual, passed}]}`` — ``valid`` is True only when
    every check passes. Mirrors the IC packet verifier's recompute-and-compare contract so a
    tampered file (or a rewritten manifest hash) is caught offline.
    """
    checks: list[dict] = []

    def add(name: str, expected, actual, passed: bool) -> None:
        checks.append(
            {
                "name": name,
                "expected": None if expected is None else str(expected),
                "actual": None if actual is None else str(actual),
                "passed": passed,
            }
        )

    try:
        archive = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        add("zip_readable", "zip archive", "unreadable", False)
        return {"valid": False, "checks": checks}

    with archive:
        names = set(archive.namelist())
        if MANIFEST_NAME not in names:
            add("manifest_present", MANIFEST_NAME, "missing", False)
            return {"valid": False, "checks": checks}
        try:
            manifest = json.loads(archive.read(MANIFEST_NAME).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            add("manifest_readable", "json object", "unparseable", False)
            return {"valid": False, "checks": checks}

        manifest_files = manifest.get("files") if isinstance(manifest, dict) else None
        if not isinstance(manifest_files, list):
            add("manifest_files", "list of file entries", type(manifest_files).__name__, False)
            return {"valid": False, "checks": checks}

        actual_entries: list[tuple[str, str]] = []
        for entry in manifest_files:
            name = entry.get("name")
            expected = entry.get("sha256")
            if name not in names:
                add(f"sha256:{name}", expected, "missing", False)
                continue
            actual = _sha256(archive.read(name))
            actual_entries.append((name, actual))
            add(f"sha256:{name}", expected, actual, actual == expected)

        recomputed_bundle = _bundle_digest(actual_entries)
        expected_bundle = manifest.get("bundle_sha256")
        add(
            "bundle_sha256",
            expected_bundle,
            recomputed_bundle,
            recomputed_bundle == expected_bundle,
        )

        # G74: a signed attestation must bind the ACTUAL recomputed file digests — verifying
        # against the recomputed canonical bytes means a rewritten manifest (hashes AND
        # bundle_sha256 recomputed by an attacker) still fails on the signature, by name.
        attestation = manifest.get("attestation")
        if isinstance(attestation, dict) and attestation.get("status") == "signed":
            signature_ok = export_signing_service.verify(
                _canonical_file_digests(actual_entries),
                str(attestation.get("signature_b64") or ""),
                str(attestation.get("public_key_b64") or ""),
            )
            add(
                "attestation_signature",
                "Ed25519 signature binds the canonical file digests",
                "verified" if signature_ok else "signature does not verify",
                signature_ok,
            )

        expected_memo = manifest.get("memo_sha256")
        actual_memo = _sha256(archive.read(MEMO_NAME)) if MEMO_NAME in names else None
        add(
            "memo_sha256",
            expected_memo,
            actual_memo,
            actual_memo is not None and actual_memo == expected_memo,
        )

    valid = all(check["passed"] for check in checks)
    return {"valid": valid, "checks": checks}
