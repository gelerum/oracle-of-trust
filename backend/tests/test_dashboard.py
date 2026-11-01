from types import SimpleNamespace

import httpx
import pytest

from oracle.app import app
from oracle.dashboard import deal_snapshot
from oracle.models import FactoringStatus


def chain_values(deal, status):
    return [deal.supplier, deal.buyer, deal.factor, "token", deal.financingAmount,
            deal.claimAmount, deal.dueDate, bytes.fromhex(deal.documentHash[2:]), int(status), 200_000 * 10**6]


@pytest.mark.parametrize("status", [FactoringStatus.ACTIVE, FactoringStatus.RESERVED])
async def test_waiting_without_evidence(gateway, deal, status):
    gateway.chain.web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=31337))
    gateway.chain.contract.functions.deals = lambda _: SimpleNamespace(call=lambda: chain_values(deal, status))
    snapshot = deal_snapshot(gateway, {"dealId": deal.dealId})
    assert snapshot["status"] == status.name
    assert snapshot["evaluationStatus"] == "WAITING"
    assert snapshot["local"] is True
    assert set(snapshot["checks"].values()) == {"WAITING"}
    assert snapshot["execution"]["status"] == "NOT_EXECUTED"
    assert snapshot["confirmedFacts"] == 0
    assert not gateway.store.history(deal.dealId)


async def test_financed_reads_chain(gateway, deal):
    await gateway.evaluate(deal.dealId)
    gateway.chain.web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=31337))
    gateway.chain.contract.functions.deals = lambda _: SimpleNamespace(call=lambda: chain_values(deal, FactoringStatus.FINANCED))
    snapshot = deal_snapshot(gateway, {"dealId": deal.dealId})
    assert snapshot["status"] == "FINANCED"
    assert snapshot["execution"]["status"] == "UNVERIFIED"
    assert snapshot["execution"]["signatureStatus"] == "NOT_CHECKED"
    assert set(snapshot["checks"].values()) == {"VERIFIED"}
    assert len(gateway.store.history(deal.dealId)) == 4


async def test_panel_http_read_only(gateway, deal):
    gateway.chain.web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=31337))
    gateway.chain.contract.functions.deals = lambda _: SimpleNamespace(call=lambda: chain_values(deal, FactoringStatus.RESERVED))
    app.state.gateway = gateway
    app.state.deployment = {"dealId": deal.dealId}
    for source in gateway.registry.sources.values():
        source["enabled"] = False
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        page = await client.get("/")
        assert page.status_code == 200
        assert 'id="deal-title"' in page.text
        assert 'id="page-sources"' in page.text
        assert "/execute" not in page.text
        assert (await client.get("/assets/app.js")).status_code == 200
        data = (await client.get("/dashboard")).json()
        assert data["status"] == "RESERVED"
        assert data["evaluationStatus"] == "WAITING"
        assert all(not source["connected"] for source in data["sources"])
    assert gateway.store.history(deal.dealId) == []


async def test_ready_and_rejected(gateway, deal, evidence_factory):
    gateway.chain.web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=31337))
    gateway.chain.contract.functions.deals = lambda _: SimpleNamespace(call=lambda: chain_values(deal, FactoringStatus.RESERVED))
    await gateway.evaluate(deal.dealId)
    assert deal_snapshot(gateway, {"dealId": deal.dealId})["evaluationStatus"] == "READY"
    with gateway.store.transaction(deal.dealId) as db:
        db.execute("UPDATE attestations SET payload=jsonb_set(payload, '{expiresAt}', '1'::jsonb), expires_at=1")
    class Negative:
        async def fetch_fact(self, query, fact):
            return evidence_factory(fact, value=False)
    gateway.registry.adapters["mock-edo"] = Negative()
    await gateway.evaluate(deal.dealId)
    snapshot = deal_snapshot(gateway, {"dealId": deal.dealId})
    assert snapshot["status"] == "RESERVED"
    assert snapshot["evaluationStatus"] == "REJECTED"
    assert snapshot["checks"]["DELIVERY_ACCEPTED"] == "REJECTED"


async def test_settled_snapshot_and_payment_allocation(gateway, deal):
    await gateway.evaluate(deal.dealId)
    gateway.chain.web3 = SimpleNamespace(eth=SimpleNamespace(chain_id=31337))
    values = [deal.supplier, deal.buyer, deal.factor, "token", deal.financingAmount,
              deal.claimAmount, deal.dueDate, bytes.fromhex(deal.documentHash[2:]), int(FactoringStatus.SETTLED), 200_000 * 10**6]
    gateway.chain.contract.functions.deals = lambda _: SimpleNamespace(call=lambda: values)
    snapshot = deal_snapshot(gateway, {"dealId": deal.dealId})
    assert snapshot["status"] == "SETTLED"
    assert set(snapshot["checks"].values()) == {"VERIFIED"}
    assert snapshot["settlement"]["factorPayment"] == 9_200_000 * 10**6
    assert snapshot["settlement"]["supplierRemainder"] == 800_000 * 10**6
    assert snapshot["settlement"]["paid"] is True
