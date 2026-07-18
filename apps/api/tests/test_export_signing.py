"""G74 — Ed25519 export attestation over canonical manifest bytes.

Covers the signing service (round-trip, any-byte-flip failure, honest key-absent degradation),
the workspace bundle's signed manifest + offline verifier (including a forged-manifest attack
where every hash is rewritten but the signature cannot be), and the IC export manifest path
through ``verify_export_manifest``.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import zipfile

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from src.config import settings
from src.db.base import Base
from src.models.deal_workflow import ICPacketExport
from src.models.underwriting_model import UnderwritingCaseDecision, UnderwritingCaseVersion
from src.models.workspace import Workspace
from src.schemas.deal_workflow import (
    ActorContext,
    DealCreate,
    ExportRequest,
    FundCreate,
    ICPacketCreate,
    LedgerEntryCreate,
    OrganizationCreate,
    StageGateResolve,
    StageTransitionCreate,
)
from src.services import deal_workflow_service as workflow
from src.services import evidence_service, export_signing_service, workspace_bundle_service


def _seed_b64() -> str:
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")


@pytest.fixture()
def signing_key(monkeypatch) -> str:
    seed = _seed_b64()
    monkeypatch.setattr(settings, "export_signing_key", seed)
    return seed


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


# --- signing service ---------------------------------------------------------------------------


def test_sign_verify_round_trip(signing_key):
    ok, note = export_signing_service.available()
    assert ok is True and note == ""

    payload = b'{"files":[{"name":"a","sha256":"00"}]}'
    attestation = export_signing_service.sign(payload)
    assert attestation["algorithm"] == "Ed25519"
    assert export_signing_service.verify(
        payload, attestation["signature_b64"], attestation["public_key_b64"]
    )
    assert attestation["public_key_b64"] == export_signing_service.public_key_b64()


def test_any_flipped_payload_byte_fails_verification(signing_key):
    payload = b"canonical-manifest-bytes"
    attestation = export_signing_service.sign(payload)
    for index in range(len(payload)):
        flipped = bytearray(payload)
        flipped[index] ^= 0x01
        assert not export_signing_service.verify(
            bytes(flipped), attestation["signature_b64"], attestation["public_key_b64"]
        ), f"flipping byte {index} must break the signature"


def test_tampered_signature_or_wrong_key_fails(signing_key):
    payload = b"payload"
    attestation = export_signing_service.sign(payload)
    signature = bytearray(base64.b64decode(attestation["signature_b64"]))
    signature[0] ^= 0x01
    assert not export_signing_service.verify(
        payload, base64.b64encode(bytes(signature)).decode(), attestation["public_key_b64"]
    )
    # A different (valid) key does not verify this signature either.
    other_key = base64.b64encode(os.urandom(32)).decode()
    assert not export_signing_service.verify(payload, attestation["signature_b64"], other_key)
    # Garbage inputs are False, never an exception.
    assert not export_signing_service.verify(payload, "not-base64!", attestation["public_key_b64"])
    assert not export_signing_service.verify(payload, attestation["signature_b64"], "")


def test_key_absent_or_malformed_degrades_honestly(monkeypatch):
    monkeypatch.setattr(settings, "export_signing_key", "")
    ok, note = export_signing_service.available()
    assert ok is False and "not configured" in note and "hash-verified only" in note
    with pytest.raises(RuntimeError, match="not configured"):
        export_signing_service.sign(b"payload")
    assert export_signing_service.public_key_b64() is None
    block = export_signing_service.build_attestation(b"payload", signed_payload="x")
    assert block == {"status": "unsigned", "note": note}

    monkeypatch.setattr(settings, "export_signing_key", "!!!not-a-key!!!")
    ok, note = export_signing_service.available()
    assert ok is False and "hash-verified only" in note

    monkeypatch.setattr(
        settings, "export_signing_key", base64.urlsafe_b64encode(os.urandom(16)).decode()
    )
    ok, note = export_signing_service.available()
    assert ok is False and "32" in note and "hash-verified only" in note


# --- workspace bundle --------------------------------------------------------------------------


def _private_workspace_with_memo(client, name: str) -> str:
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


def _forge_bundle(bundle_bytes: bytes) -> bytes:
    """A capable attacker: mutate a file, then rewrite every hash in the manifest to match.

    Hash-only verification cannot catch this; only the Ed25519 signature (which the attacker
    cannot re-mint) still binds the original file digests.
    """
    source = zipfile.ZipFile(io.BytesIO(bundle_bytes))
    manifest = json.loads(source.read("manifest.json"))
    contents = {name: source.read(name) for name in source.namelist()}
    contents["evidence-appendix.csv"] += b"EV-999,fact,forged,forged,forged,,,forged,1.000,evil\n"
    files = [
        {
            "name": entry["name"],
            "sha256": hashlib.sha256(contents[entry["name"]]).hexdigest(),
            "bytes": len(contents[entry["name"]]),
        }
        for entry in manifest["files"]
    ]
    manifest["files"] = files
    manifest["bundle_sha256"] = workspace_bundle_service._bundle_digest(
        [(item["name"], item["sha256"]) for item in files]
    )
    contents["manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    forged = io.BytesIO()
    with zipfile.ZipFile(forged, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in contents.items():
            archive.writestr(name, data)
    return forged.getvalue()


def test_signed_bundle_manifest_and_offline_verifier(client, signing_key):
    from src.db.session import SessionLocal

    workspace_id = _private_workspace_with_memo(client, "Signed bundle")
    with SessionLocal() as session:
        bundle = workspace_bundle_service.build_bundle(session, workspace_id)

    attestation = bundle.manifest["attestation"]
    assert attestation["status"] == "signed"
    assert attestation["algorithm"] == "Ed25519"
    assert "bundle_sha256" in attestation["signed_payload"]

    # The signature covers EXACTLY the canonical bytes whose SHA-256 is bundle_sha256 —
    # reproduced here with plain stdlib + cryptography, exactly as VERIFY.md documents.
    entries = sorted(
        ({"name": f["name"], "sha256": f["sha256"]} for f in bundle.manifest["files"]),
        key=lambda item: item["name"],
    )
    payload = json.dumps(entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert hashlib.sha256(payload).hexdigest() == bundle.bundle_sha256
    assert export_signing_service.verify(
        payload, attestation["signature_b64"], attestation["public_key_b64"]
    )

    # The bundle ships its own offline recipe with the public key embedded.
    archive = zipfile.ZipFile(io.BytesIO(bundle.content))
    readme = archive.read("VERIFY.md").decode("utf-8")
    assert attestation["public_key_b64"] in readme
    assert "Ed25519PublicKey" in readme
    assert any(f["name"] == "VERIFY.md" for f in bundle.manifest["files"])

    result = workspace_bundle_service.verify_bundle(bundle.content)
    assert result["valid"] is True
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["attestation_signature"]["passed"] is True


def test_forged_manifest_fails_only_on_the_signature(client, signing_key):
    from src.db.session import SessionLocal

    workspace_id = _private_workspace_with_memo(client, "Forged bundle")
    with SessionLocal() as session:
        bundle = workspace_bundle_service.build_bundle(session, workspace_id)

    forged = _forge_bundle(bundle.content)
    result = workspace_bundle_service.verify_bundle(forged)
    checks = {check["name"]: check for check in result["checks"]}
    # The attacker rewrote every digest consistently, so hash-only checks all pass...
    assert checks["sha256:evidence-appendix.csv"]["passed"] is True
    assert checks["bundle_sha256"]["passed"] is True
    # ...but the Ed25519 signature still binds the ORIGINAL digests, and fails by name.
    assert checks["attestation_signature"]["passed"] is False
    assert result["valid"] is False


def test_verify_endpoint_catches_tampered_signed_bundle(client, signing_key):
    workspace_id = _private_workspace_with_memo(client, "Endpoint signed bundle")
    exported = client.get(f"/api/workspaces/{workspace_id}/export-bundle")
    assert exported.status_code == 200, exported.text

    upload = client.post(
        f"/api/workspaces/{workspace_id}/export-bundle/verify",
        files={"file": ("bundle.zip", exported.content, "application/zip")},
    )
    assert upload.status_code == 200, upload.text
    body = upload.json()
    assert body["valid"] is True
    assert any(c["name"] == "attestation_signature" and c["passed"] for c in body["checks"])

    rejected = client.post(
        f"/api/workspaces/{workspace_id}/export-bundle/verify",
        files={"file": ("bundle.zip", _forge_bundle(exported.content), "application/zip")},
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["valid"] is False
    failing = [c["name"] for c in body["checks"] if not c["passed"]]
    assert failing == ["attestation_signature"]


def test_unsigned_bundle_degrades_to_hash_only_honestly(client, monkeypatch):
    from src.db.session import SessionLocal

    monkeypatch.setattr(settings, "export_signing_key", "")
    workspace_id = _private_workspace_with_memo(client, "Unsigned bundle")
    with SessionLocal() as session:
        bundle = workspace_bundle_service.build_bundle(session, workspace_id)

    attestation = bundle.manifest["attestation"]
    assert attestation["status"] == "unsigned"
    assert "not configured" in attestation["note"]

    archive = zipfile.ZipFile(io.BytesIO(bundle.content))
    readme = archive.read("VERIFY.md").decode("utf-8")
    assert "NOT signed" in readme

    result = workspace_bundle_service.verify_bundle(bundle.content)
    assert result["valid"] is True
    # Hash discipline is unchanged and no phantom signature check appears.
    assert "attestation_signature" not in {check["name"] for check in result["checks"]}


# --- IC export manifests -----------------------------------------------------------------------


def _setup_frozen_packet(db: Session, *, suffix: str):
    creator = ActorContext(actor_id=f"lead-{suffix}", display_name="Deal Lead")
    organization = workflow.create_organization(
        db, OrganizationCreate(name=f"Signing Org {suffix}", slug=f"signing-{suffix}"), creator
    )
    actor = creator.model_copy(update={"organization_id": organization.id})
    fund = workflow.create_fund(db, organization.id, FundCreate(name="Fund I"), actor)
    deal = workflow.create_deal(
        db,
        fund.id,
        DealCreate(code=f"S-{suffix}", name=f"Signing {suffix}", target_company="Target"),
        actor,
    )
    # Advance to ic_review, satisfying gates along the way.
    next_stage = {
        "sourcing": "screening",
        "screening": "initial_review",
        "initial_review": "diligence",
        "diligence": "ic_review",
    }
    for stage, destination in next_stage.items():
        for gate in workflow.list_gates(db, deal.id, actor, stage):
            if gate.status == "pending":
                workflow.resolve_gate(
                    db,
                    gate.id,
                    StageGateResolve(status="satisfied", resolution_note="Reviewed"),
                    actor,
                )
        workflow.transition_deal(
            db,
            deal.id,
            StageTransitionCreate(to_stage=destination, rationale="Advance"),
            actor,
        )
    for gate in workflow.list_gates(db, deal.id, actor, "ic_review"):
        workflow.resolve_gate(
            db,
            gate.id,
            StageGateResolve(status="satisfied", resolution_note="Materials checked"),
            actor,
        )
    # Governed workspace + approved underwriting case + evidence-backed thesis.
    workspace = Workspace(
        name=f"{deal.name} workspace",
        organization_id=deal.organization_id,
        deal_type="buyout",
        status="draft",
    )
    db.add(workspace)
    db.flush()
    deal.workspace_id = workspace.id
    db.commit()
    digest = hashlib.sha256(f"{deal.id}:case".encode()).hexdigest()
    case = UnderwritingCaseVersion(
        workspace_id=workspace.id,
        case_key="base",
        label="Base case",
        version=1,
        schema_version="1.0",
        assumptions={"revenue": 100, "entry_enterprise_value": 250_000_000},
        result={"irr": 0.24, "moic": 2.5},
        input_hash=digest,
        output_hash=hashlib.sha256(f"result:{digest}".encode()).hexdigest(),
        created_by="model-analyst",
        change_note="Signing fixture",
    )
    db.add(case)
    db.commit()
    for decision, decision_actor in (("submitted", "model-analyst"), ("approved", "partner")):
        db.add(
            UnderwritingCaseDecision(
                workspace_id=workspace.id,
                case_version_id=case.id,
                decision=decision,
                actor=decision_actor,
                rationale="Fixture",
            )
        )
        db.commit()
    evidence = evidence_service.create(
        db,
        workspace.id,
        claim="Base-case revenue is 100.",
        claim_type="assumption",
        source_name="Underwriting model fixture",
        source_type="analyst_model",
        evidence_text="Base-case revenue assumption: 100.",
        confidence=0.9,
        agent_name="model-analyst",
    )
    db.commit()
    workflow.create_ledger_entry(
        db,
        deal.id,
        LedgerEntryCreate(
            entry_type="thesis",
            title="Governed base case",
            description="Approved case supports the thesis.",
            status="validated",
            evidence_refs=[evidence.ref],
        ),
        ActorContext(actor_id="model-analyst", organization_id=deal.organization_id),
    )
    packet = workflow.create_ic_packet(
        db,
        deal.id,
        ICPacketCreate(
            title="Investment Committee Memorandum",
            case_version_ids=[case.id],
            workspace_evidence_refs=[evidence.ref],
            decision_request={"ask": "Approve"},
        ),
        actor,
    )
    workflow.submit_ic_packet(db, packet.id, actor)
    return actor, packet


def test_ic_export_manifest_is_signed_and_verifier_names_signature_tamper(db, signing_key):
    from src.services import ic_export_service

    actor, packet = _setup_frozen_packet(db, suffix="signed")
    exported = ic_export_service.render_and_record_export(
        db, packet.id, ExportRequest(format="json"), actor
    )
    record = db.scalar(select(ICPacketExport).where(ICPacketExport.id == exported.export_id))
    attestation = record.manifest["attestation"]
    assert attestation["status"] == "signed"
    assert attestation["algorithm"] == "Ed25519"

    # Hash and signature attest the same canonical bytes (manifest minus its attestation block).
    core = export_signing_service.canonical_manifest_bytes(record.manifest)
    assert hashlib.sha256(core).hexdigest() == record.manifest_hash
    assert export_signing_service.verify(
        core, attestation["signature_b64"], attestation["public_key_b64"]
    )

    verified = workflow.verify_export_manifest(db, record.id, actor)
    checks = {item["code"]: item for item in verified["checks"]}
    assert verified["valid"] is True, [c for c in verified["checks"] if not c["passed"]]
    assert checks["attestation_signature"]["passed"] is True

    # A database attacker rewrites the manifest AND recomputes the stored hash; the canonical
    # hash check passes but the un-forgeable signature fails, named as such.
    tampered = json.loads(json.dumps(record.manifest))
    tampered["packet_content_hash"] = "f" * 64
    record.manifest = tampered
    record.manifest_hash = hashlib.sha256(
        export_signing_service.canonical_manifest_bytes(tampered)
    ).hexdigest()
    db.commit()

    result = workflow.verify_export_manifest(db, record.id, actor)
    checks = {item["code"]: item for item in result["checks"]}
    assert result["valid"] is False
    assert checks["canonical_manifest_hash"]["passed"] is True
    assert checks["attestation_signature"]["passed"] is False
    assert "signature" in checks["attestation_signature"]["message"]


def test_ic_export_without_key_is_unsigned_and_still_hash_verified(db, monkeypatch):
    from src.services import ic_export_service

    monkeypatch.setattr(settings, "export_signing_key", "")
    actor, packet = _setup_frozen_packet(db, suffix="unsigned")
    exported = ic_export_service.render_and_record_export(
        db, packet.id, ExportRequest(format="json"), actor
    )
    record = db.scalar(select(ICPacketExport).where(ICPacketExport.id == exported.export_id))
    assert record.manifest["attestation"]["status"] == "unsigned"
    assert "not configured" in record.manifest["attestation"]["note"]

    verified = workflow.verify_export_manifest(db, record.id, actor)
    assert verified["valid"] is True, [c for c in verified["checks"] if not c["passed"]]
    # No signature => no attestation check; the hash discipline alone still holds.
    assert "attestation_signature" not in {item["code"] for item in verified["checks"]}
