import pytest

from oracle.models import Evidence


def facts(rules, factory):
    return {fact: Evidence.model_validate(factory(fact)) for fact in rules.rules["FACTORING"]}


def test_ready(rules, deal, evidence_factory):
    assert rules.evaluate(deal, facts(rules, evidence_factory)) == ("READY", [])


@pytest.mark.parametrize("fact", ["DELIVERY_ACCEPTED", "BUYER_NOTIFIED", "CLAIM_ASSIGNED", "FINANCING_APPROVED"])
def test_each_fact_required(fact, rules, deal, evidence_factory):
    evidence = facts(rules, evidence_factory)
    del evidence[fact]
    assert rules.evaluate(deal, evidence)[0] == "WAITING"


@pytest.mark.parametrize("change", [
    {"value": False}, {"sourceId": "mock-vtb"},
    {"attributes": {"buyerAuthorized": True}},
    {"attributes": {"supplier": "forged", "buyerAuthorized": True}},
])
def test_negative_or_mismatch(change, rules, deal, evidence_factory):
    evidence = facts(rules, evidence_factory)
    evidence["DELIVERY_ACCEPTED"] = evidence["DELIVERY_ACCEPTED"].model_copy(update=change)
    assert rules.evaluate(deal, evidence)[0] == "REJECTED"


@pytest.mark.parametrize("fact,field,value", [
    ("DELIVERY_ACCEPTED", "buyerAuthorized", False),
    ("BUYER_NOTIFIED", "notificationStatus", "sent"),
    ("CLAIM_ASSIGNED", "claimAmount", 1),
    ("FINANCING_APPROVED", "financingAmount", 1),
    ("FINANCING_APPROVED", "dueDate", 1),
    ("FINANCING_APPROVED", "commissionAmount", 1),
])
def test_business_attributes(fact, field, value, rules, deal, evidence_factory):
    evidence = facts(rules, evidence_factory)
    evidence[fact].attributes[field] = value
    assert rules.evaluate(deal, evidence)[0] == "REJECTED"
