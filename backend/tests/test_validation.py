import time

import pytest
from eth_account import Account

from oracle.crypto import sign_source
from oracle.models import Evidence
from oracle.validation import EvidenceValidator, MockAccessPolicy


@pytest.mark.parametrize("fact", ["DELIVERY_ACCEPTED", "BUYER_NOTIFIED", "CLAIM_ASSIGNED", "FINANCING_APPROVED"])
def test_valid_fact(fact, deal, registry, evidence_factory):
    raw = evidence_factory(fact)
    evidence = EvidenceValidator().validate(raw, registry.sources[raw["sourceId"]], fact, deal)
    assert evidence.value is True


@pytest.mark.parametrize("changes", [
    {"schemaVersion": "2"}, {"sourceId": "unknown"}, {"factType": "CLAIM_ASSIGNED"},
    {"dealId": "0x" + "ff" * 32}, {"documentHash": "0x" + "ff" * 32},
    {"subjectRef": "other"}, {"expiresAt": 1}, {"observedAt": 9999999999},
    {"value": "true"}, {"value": 1}, {"value": None},
    {"retrievalStatus": "unavailable"}, {"verificationStatus": "VERIFIED"},
])
def test_invalid_evidence(changes, deal, registry, evidence_factory):
    raw = evidence_factory(**changes)
    with pytest.raises((ValueError, TypeError)):
        EvidenceValidator().validate(raw, registry.sources["mock-edo"], "DELIVERY_ACCEPTED", deal)


def test_signature_tampering(deal, registry, evidence_factory):
    raw = evidence_factory()
    raw["value"] = False
    with pytest.raises(ValueError, match="signature"):
        EvidenceValidator().validate(raw, registry.sources["mock-edo"], "DELIVERY_ACCEPTED", deal)


def test_wrong_source_key(deal, registry, evidence_factory):
    raw = evidence_factory()
    raw["sourceProof"] = sign_source({k: v for k, v in raw.items() if k != "sourceProof"}, Account.create().key.hex())
    with pytest.raises(ValueError, match="signature"):
        EvidenceValidator().validate(raw, registry.sources["mock-edo"], "DELIVERY_ACCEPTED", deal)


@pytest.mark.parametrize("result", [None, "true", {"valid": True}, False])
def test_invalid_ai_result(result, deal, registry, evidence_factory):
    class Check:
        def check(self, evidence, deal):
            return result
    with pytest.raises(ValueError, match="document check"):
        EvidenceValidator(Check()).validate(evidence_factory(), registry.sources["mock-edo"], "DELIVERY_ACCEPTED", deal)


@pytest.mark.parametrize("status", ["unknown", "unavailable", "rejected"])
def test_absence_distinct_from_false(status, deal, registry, evidence_factory):
    raw = evidence_factory(retrievalStatus=status, value=None)
    result = EvidenceValidator().validate(raw, registry.sources["mock-edo"], "DELIVERY_ACCEPTED", deal)
    assert result.value is None
    assert result.retrievalStatus == status


def test_real_source_denied(deal, registry):
    assert not MockAccessPolicy().allows(dict(registry.sources["mock-edo"], type="http"), deal)


@pytest.mark.parametrize("signature", ["", "0x1234", "0x" + "00" * 65, "0x" + "ff" * 65])
def test_malformed_signature_rejected(signature, deal, registry, evidence_factory):
    raw = evidence_factory()
    raw["sourceProof"] = signature
    with pytest.raises(ValueError):
        EvidenceValidator().validate(raw, registry.sources["mock-edo"], "DELIVERY_ACCEPTED", deal)
