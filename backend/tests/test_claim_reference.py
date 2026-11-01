import json

import pytest

from oracle.migrations import migrate_demo_claim
from oracle.models import Evidence


def test_claim_reference_is_bound_to_each_request(rules, deal, evidence_factory):
    evidence = {fact: Evidence.model_validate(evidence_factory(fact)) for fact in rules.rules["FACTORING"]}
    assert rules.evaluate(deal, evidence)[0] == "READY"
    another = deal.model_copy(update={"dealId": "0x" + "aa" * 32, "claimId": "another-claim"})
    other_evidence = {fact: item.model_copy(update={"dealId": another.dealId}) for fact, item in evidence.items()}
    assert rules.evaluate(another, other_evidence)[0] == "REJECTED"
    other_evidence["CLAIM_ASSIGNED"].attributes["claimId"] = another.claimId
    assert rules.evaluate(another, other_evidence)[0] == "READY"
    assert "claimId" not in rules.rules["FACTORING"]["CLAIM_ASSIGNED"]["required"]


def test_original_demo_claim_migration_is_scoped_audited_and_idempotent(store, deal):
    previous = deal.model_dump()
    previous.pop("claimId")
    store.put_deal(previous)
    other = dict(previous, dealId="0x" + "bb" * 32, subjectRef="unrelated")
    store.put_deal(other)
    process = {"processId": "PROCESS", "name": "Original demo", "requirements": {"CLAIM_ASSIGNED": {
        "sources": ["mock-vtb"], "equals": True, "attributes": {"factor": "factor"}, "required": {"claimId": "test-claim-001"}}},
        "contextSchema": {"type": "object", "additionalProperties": False, "properties": {"factor": {"type": "string"}}, "required": ["factor"]}}
    store.configure("process", "PROCESS", process)
    store.configure("workflow", deal.dealType, {"workflowId": deal.dealType, "processId": "PROCESS"})
    migrate_demo_claim(store, deal.dealId, "test-claim-001")
    migrate_demo_claim(store, deal.dealId, "test-claim-001")
    rows = {row["id"]: row["data"] for row in store.requests()}
    assert rows[deal.dealId] == dict(previous, claimId="test-claim-001")
    assert rows[other["dealId"]] == other
    updated = store.configurations("process")[0]
    assert updated["requirements"]["CLAIM_ASSIGNED"]["attributes"]["claimId"] == "claimId"
    assert "claimId" not in updated["requirements"]["CLAIM_ASSIGNED"]["required"]
    assert "claimId" in updated["contextSchema"]["required"]
    with store.transaction("check") as db:
        assert db.execute("SELECT count(*) AS count FROM configuration_audit WHERE kind='process'").fetchone()["count"] == 2
    with pytest.raises(ValueError, match="immutable"):
        migrate_demo_claim(store, deal.dealId, "different-claim")
