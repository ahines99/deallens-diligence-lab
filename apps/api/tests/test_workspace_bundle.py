"""G45 — Workspace export bundle: round-trip verification + tamper detection.

The bundle assembles the IC memo (PDF), an evidence appendix (CSV) and a hash manifest into a
single ZIP that a reviewer can verify offline. These tests exercise the same recompute-and-compare
tamper-detection contract as the IC packet verifier.
"""
from __future__ import annotations

import io
import json
import zipfile

from src.db.session import SessionLocal
from src.services import workspace_bundle_service


def _private_workspace_with_memo(client, name: str) -> str:
    """Create a private-target workspace and run analysis so a memo + evidence exist."""
    workspace_id = client.post(
        "/api/workspaces", json={"name": name, "deal_type": "buyout"}
    ).json()["id"]
    target = client.post(
        f"/api/workspaces/{workspace_id}/target",
        json={
            "name": f"{name} Target",
            "target_type": "private_company",
            "revenue": 90_000_000,
            "revenue_growth": 0.07,
            "gross_margin": 0.52,
            "operating_margin": 0.11,
            "net_income": 6_000_000,
            "cash": 4_500_000,
            "total_debt": 22_000_000,
            "fiscal_year_end": "2025-12-31",
        },
    )
    assert target.status_code == 200, target.text
    generated = client.post(f"/api/workspaces/{workspace_id}/risks/generate")
    assert generated.status_code == 200, generated.text
    return workspace_id


def test_build_bundle_round_trips_valid(client):
    workspace_id = _private_workspace_with_memo(client, "Bundle round trip")
    with SessionLocal() as session:
        bundle = workspace_bundle_service.build_bundle(session, workspace_id)

    archive = zipfile.ZipFile(io.BytesIO(bundle.content))
    names = set(archive.namelist())
    assert {"ic-memo.pdf", "evidence-appendix.csv", "manifest.json"} <= names

    memo_bytes = archive.read("ic-memo.pdf")
    assert memo_bytes.startswith(b"%PDF"), "IC memo file must be a PDF"

    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["workspace_id"] == workspace_id
    assert manifest["evidence_count"] >= 1
    assert manifest["bundle_sha256"] == bundle.bundle_sha256
    # Every manifest file digest matches the actual bytes in the archive.
    for entry in manifest["files"]:
        assert entry["sha256"] == workspace_bundle_service._sha256(archive.read(entry["name"]))

    result = workspace_bundle_service.verify_bundle(bundle.content)
    assert result["valid"] is True, [c for c in result["checks"] if not c["passed"]]
    assert {"bundle_sha256", "memo_sha256"} <= {c["name"] for c in result["checks"]}
    assert all(c["passed"] for c in result["checks"])


def test_verify_bundle_detects_tampering(client):
    workspace_id = _private_workspace_with_memo(client, "Bundle tamper")
    with SessionLocal() as session:
        bundle = workspace_bundle_service.build_bundle(session, workspace_id)

    # Rewrite one file's bytes inside the zip, leaving the manifest's recorded hash untouched.
    source = zipfile.ZipFile(io.BytesIO(bundle.content))
    mutated = io.BytesIO()
    with zipfile.ZipFile(mutated, "w", zipfile.ZIP_DEFLATED) as out:
        for info in source.infolist():
            data = source.read(info.filename)
            if info.filename == "evidence-appendix.csv":
                data = data + b"EV-999,fact,injected,forged,forged,,,tampered,1.000,attacker\n"
            out.writestr(info, data)

    result = workspace_bundle_service.verify_bundle(mutated.getvalue())
    assert result["valid"] is False
    checks = {c["name"]: c for c in result["checks"]}
    assert checks["sha256:evidence-appendix.csv"]["passed"] is False
    assert checks["sha256:evidence-appendix.csv"]["expected"] != (
        checks["sha256:evidence-appendix.csv"]["actual"]
    )
    # The bundle rollup also breaks because it is recomputed from the actual file bytes.
    assert checks["bundle_sha256"]["passed"] is False
    # The untouched memo still verifies, proving the failure is localized.
    assert checks["sha256:ic-memo.pdf"]["passed"] is True


def test_bundle_sha256_is_deterministic_for_identical_inputs(client):
    workspace_id = _private_workspace_with_memo(client, "Bundle deterministic")
    with SessionLocal() as session:
        first = workspace_bundle_service.build_bundle(session, workspace_id)
    with SessionLocal() as session:
        second = workspace_bundle_service.build_bundle(session, workspace_id)

    # bundle_sha256 excludes the wall-clock generated_at, so it is stable across rebuilds.
    assert first.bundle_sha256 == second.bundle_sha256
    assert first.manifest["memo_sha256"] == second.manifest["memo_sha256"]
    assert first.manifest["files"] == second.manifest["files"]
    # generated_at is the only field allowed to drift between builds.
    assert first.manifest["generated_at"] != "" and second.manifest["generated_at"] != ""


def test_export_and_verify_endpoints_contract(client):
    workspace_id = _private_workspace_with_memo(client, "Bundle endpoints")

    exported = client.get(f"/api/workspaces/{workspace_id}/export-bundle")
    assert exported.status_code == 200, exported.text
    assert exported.headers["content-type"] == "application/zip"
    assert "attachment" in exported.headers["content-disposition"]
    bundle_hash = exported.headers["x-bundle-sha256"]
    assert len(bundle_hash) == 64

    archive = zipfile.ZipFile(io.BytesIO(exported.content))
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["bundle_sha256"] == bundle_hash

    # Fresh regenerate-and-verify (no upload) reports valid.
    fresh = client.post(f"/api/workspaces/{workspace_id}/export-bundle/verify")
    assert fresh.status_code == 200, fresh.text
    body = fresh.json()
    assert body["valid"] is True
    assert all(check["passed"] for check in body["checks"])

    # Uploading the streamed bundle verifies valid too; uploading a tampered copy is rejected.
    upload = client.post(
        f"/api/workspaces/{workspace_id}/export-bundle/verify",
        files={"file": ("bundle.zip", exported.content, "application/zip")},
    )
    assert upload.status_code == 200, upload.text
    assert upload.json()["valid"] is True

    tampered = bytearray(exported.content)
    tampered[-20:] = b"\x00" * 20
    rejected = client.post(
        f"/api/workspaces/{workspace_id}/export-bundle/verify",
        files={"file": ("bundle.zip", bytes(tampered), "application/zip")},
    )
    assert rejected.status_code == 200, rejected.text
    assert rejected.json()["valid"] is False
