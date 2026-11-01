import asyncio

import httpx
import pytest
from eth_account import Account

from oracle.crypto import typed_message


async def test_success_and_history(gateway, deal):
    result = await gateway.evaluate(deal.dealId)
    assert result["status"] == "READY"
    attestation = dict(result["attestation"])
    signature = attestation.pop("signature")
    signer = Account.recover_message(typed_message(attestation, gateway.chain.chain_id, gateway.chain.address), signature=signature)
    assert signer == Account.from_key(gateway.key).address
    history = gateway.store.history(deal.dealId)
    assert len(history) == 4
    assert all(row["accepted"] and row["reason"] == "VERIFIED" for row in history)
    assert {row["source_id"] for row in history} == {"mock-edo", "mock-vtb"}
    assert await gateway.evaluate(deal.dealId) == result


async def test_unknown_deal(gateway):
    assert (await gateway.evaluate("0x" + "ff" * 32))["status"] == "REJECTED"


@pytest.mark.parametrize("error", [httpx.ReadTimeout("timeout"), httpx.ConnectError("offline")])
async def test_unavailable_waiting(error, gateway, deal):
    class Down:
        async def fetch_fact(self, query, fact):
            raise error
    gateway.registry.adapters["mock-edo"] = Down()
    assert (await gateway.evaluate(deal.dealId))["status"] == "WAITING"
    assert len([row for row in gateway.store.history(deal.dealId) if not row["accepted"]]) == 2


async def test_disabled_source(gateway, deal):
    gateway.registry.sources["mock-edo"]["enabled"] = False
    assert (await gateway.evaluate(deal.dealId))["status"] == "WAITING"


async def test_duplicate_event_rejected(gateway, deal, evidence_factory):
    repeated = evidence_factory()
    class Repeat:
        async def fetch_fact(self, query, fact):
            if fact == "DELIVERY_ACCEPTED":
                return repeated
            return evidence_factory(fact, value=False)
    gateway.registry.adapters["mock-edo"] = Repeat()
    assert (await gateway.evaluate(deal.dealId))["status"] == "REJECTED"
    assert (await gateway.evaluate(deal.dealId))["status"] == "REJECTED"
    assert any("duplicate" in row["reason"] for row in gateway.store.history(deal.dealId))


async def test_invalid_signature_audited(gateway, deal, evidence_factory):
    class Tampered:
        async def fetch_fact(self, query, fact):
            raw = evidence_factory(fact)
            raw["sourceId"] = "unknown"
            return raw
    gateway.registry.adapters["mock-edo"] = Tampered()
    assert (await gateway.evaluate(deal.dealId))["status"] == "REJECTED"
    assert any(not row["accepted"] and row["raw"]["sourceId"] == "unknown"
               for row in gateway.store.history(deal.dealId) if row["source_id"] == "mock-edo")


async def test_parallel_evaluation_reuses_one_nonce(gateway, deal):
    results = await asyncio.wait_for(asyncio.gather(
        gateway.evaluate(deal.dealId), gateway.evaluate(deal.dealId)), timeout=10)
    assert results[0] == results[1]
    with gateway.store.transaction(deal.dealId) as db:
        assert db.execute("SELECT count(*) AS count FROM attestations").fetchone()["count"] == 1


async def test_replayed_expired_request_cannot_reuse_evidence(gateway, deal, evidence_factory):
    fixed = {fact: evidence_factory(fact) for fact in gateway.rules.rules["FACTORING"]}
    class Fixed:
        async def fetch_fact(self, query, fact):
            return fixed[fact]
    gateway.registry.adapters = {sid: Fixed() for sid in gateway.registry.sources}
    first = await gateway.evaluate(deal.dealId)
    assert first["status"] == "READY"
    with gateway.store.transaction(deal.dealId) as db:
        db.execute("UPDATE attestations SET payload=jsonb_set(payload, '{expiresAt}', '1'::jsonb), expires_at=1")
    assert (await gateway.evaluate(deal.dealId))["status"] == "REJECTED"


async def test_business_refusal_recorded(gateway, deal, evidence_factory):
    class Refused:
        async def fetch_fact(self, query, fact):
            return evidence_factory(fact, value=False)
    gateway.registry.adapters["mock-vtb"] = Refused()
    assert (await gateway.evaluate(deal.dealId))["status"] == "REJECTED"
    with gateway.store.transaction(deal.dealId) as db:
        row = db.execute("SELECT result FROM evaluations ORDER BY id DESC LIMIT 1").fetchone()
        assert row["result"]["status"] == "REJECTED"
        assert any("negative" in reason for reason in row["result"]["reasons"])


@pytest.mark.parametrize("signature", ["", "0x1234", "0x" + "00" * 65, "0x" + "ff" * 65])
async def test_malformed_signatures_audited(signature, gateway, deal, evidence_factory):
    class BadProof:
        async def fetch_fact(self, query, fact):
            raw = evidence_factory(fact)
            raw["sourceProof"] = signature
            return raw
    gateway.registry.adapters["mock-edo"] = BadProof()
    assert (await gateway.evaluate(deal.dealId))["status"] == "REJECTED"
    history = gateway.store.history(deal.dealId)
    assert len(history) == 4
    assert sum(not row["accepted"] for row in history) == 2
