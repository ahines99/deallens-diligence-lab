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

from src.services import evidence_service
from src.services.common import get_workspace_or_404
from src.services.memo_generation_service import get_ic_memo

SCHEMA_VERSION = "1.0"
MANIFEST_NAME = "manifest.json"
MEMO_NAME = "ic-memo.pdf"
EVIDENCE_NAME = "evidence-appendix.csv"
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


def _bundle_digest(entries: list[tuple[str, str]]) -> str:
    """Roll up ordered ``(filename, file_sha256)`` pairs into one stable digest.

    Excludes wall-clock time and raw ZIP framing, so it is deterministic for identical inputs and
    still changes if any file's bytes change (whichever file is tampered).
    """
    ordered = sorted(({"name": name, "sha256": digest} for name, digest in entries),
                     key=lambda item: item["name"])
    canonical = json.dumps(ordered, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


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


def build_bundle(session: Session, workspace_id: str) -> WorkspaceBundle:
    """Assemble the verifiable ZIP for a workspace (IC memo PDF + evidence appendix + manifest)."""
    get_workspace_or_404(session, workspace_id)
    memo = get_ic_memo(session, workspace_id)  # raises NotFound if not generated yet
    evidence = evidence_service.list_evidence(session, workspace_id)

    memo_pdf = _render_memo_pdf(memo.title or "Investment Committee Memo", memo.markdown_content)
    evidence_csv = _render_evidence_csv(evidence)

    rendered = [(MEMO_NAME, memo_pdf), (EVIDENCE_NAME, evidence_csv)]
    files = [
        {"name": name, "sha256": _sha256(data), "bytes": len(data)}
        for name, data in rendered
    ]
    memo_sha256 = files[0]["sha256"]
    bundle_sha256 = _bundle_digest([(item["name"], item["sha256"]) for item in files])

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "workspace_id": workspace_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "files": files,
        "bundle_sha256": bundle_sha256,
        "memo_sha256": memo_sha256,
        "evidence_count": len(evidence),
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
