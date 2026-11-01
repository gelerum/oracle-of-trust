import asyncio
import time

import httpx

from oracle.models import Deal, Evidence, FactoringStatus
from oracle.rules import RulesEngine
from oracle.execution import financing_execution


async def source_statuses(registry) -> list[dict]:
    """Probe registered mock endpoints without requesting or validating facts.

    Args:
        registry: Trusted source catalogue.

    Returns:
        Source metadata with current transport availability.
    """
    async def probe(source):
        available = getattr(registry.adapters.get(source["sourceId"]), "available", True)
        connected = None if source["enabled"] and available else False
        if source["enabled"] and available and source.get("healthUrl"):
            connected = False
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.get(source["healthUrl"], timeout=1)
                    connected = 200 <= response.status_code < 300
            except (httpx.HTTPError, ValueError, AttributeError):
                pass
        return {"sourceId": source["sourceId"], "name": source["name"],
                "enabled": source["enabled"], "connected": connected, "adapterAvailable": available}
    return list(await asyncio.gather(*(probe(source) for source in registry.sources.values())))


def deal_snapshot(gateway, deployment: dict) -> dict:
    """Read a deal snapshot without collecting evidence or executing payments.

    Args:
        gateway: Initialized gateway services.
        deployment: Local deployment metadata.

    Returns:
        Chain status and latest backend validation results.
    """
    deal_id = deployment["dealId"]
    checks = {fact: "WAITING" for fact in gateway.rules.rules[deployment.get("workflowId", "FACTORING")]}
    result = {"displayId": 742, "dealId": deal_id, "local": False,
              "workflowId": deployment.get("workflowId", "FACTORING"),
              "status": "UNAVAILABLE", "evaluationStatus": "WAITING", "checks": checks, "error": None,
              "execution": {"status": "UNAVAILABLE", "signatureStatus": "NOT_CHECKED", "transactionHash": None}}
    web3 = gateway.chain.web3
    try:
        result["local"] = web3.eth.chain_id == 31337
        chain_deal = gateway.chain.contract.functions.deals(bytes.fromhex(deal_id[2:])).call()
        result["status"] = FactoringStatus(chain_deal[8]).name
    except Exception:
        result["error"] = "Нет связи с локальной сетью Anvil."
        return result
    result["execution"] = financing_execution(gateway.chain, deal_id, chain_deal)
    history = gateway.store.history(deal_id)
    latest = {}
    for row in history:
        if row["fact_type"] in checks:
            latest[row["fact_type"]] = row
    with gateway.store.transaction(deal_id) as db:
        row = db.execute("SELECT data FROM deals WHERE id=%s", (deal_id,)).fetchone()
        evaluation = db.execute("SELECT result FROM evaluations WHERE deal_id=%s ORDER BY id DESC LIMIT 1", (deal_id,)).fetchone()
        issued = db.execute("SELECT payload,evidence_ids FROM attestations WHERE deal_id=%s AND signature IS NOT NULL ORDER BY nonce DESC LIMIT 1", (deal_id,)).fetchone()
    if row is None:
        result["error"] = "Сделка не зарегистрирована в backend."
        return result
    deal = Deal.model_validate(row["data"])
    result["claimId"] = getattr(deal, "claimId", None)
    now = int(time.time())
    financed = chain_deal[8] in (FactoringStatus.FINANCED, FactoringStatus.SETTLED)
    if chain_deal[8] != FactoringStatus.NONE:
        result["settlement"] = {"financingAmount": chain_deal[4], "claimAmount": chain_deal[5],
                                "commissionAmount": chain_deal[9],
                                "factorPayment": chain_deal[4] + chain_deal[9],
                                "supplierRemainder": chain_deal[5] - chain_deal[4] - chain_deal[9],
                                "dueDate": chain_deal[6], "decimals": deployment.get("tokenDecimals", 6),
                                "paid": chain_deal[8] == FactoringStatus.SETTLED}
    execution = result["execution"]
    if execution["status"] == "CONFIRMED":
        with gateway.store.transaction(deal_id) as db:
            executed = db.execute("SELECT evidence_ids FROM attestations WHERE deal_id=%s AND nonce=%s",
                                  (deal_id, execution["attestation"]["nonce"])).fetchone()
        if executed:
            linked = executed["evidence_ids"] or {}
            for fact, identity in linked.items():
                selected = next((entry for entry in reversed(history) if entry["fact_type"] == fact
                    and entry["source_id"] == identity["sourceId"] and entry["raw"].get("evidenceId") == identity["evidenceId"]), None)
                if selected:
                    latest[fact] = selected
            issued = dict(evidence_ids=linked)
    for fact, entry in latest.items():
        if not entry["accepted"]:
            checks[fact] = "WAITING" if "unavailable" in entry["reason"] or "enabled source" in entry["reason"] else "REJECTED"
            continue
        evidence = Evidence.model_validate(entry["raw"])
        if financed and issued and issued["evidence_ids"].get(fact, {}).get("evidenceId") == evidence.evidenceId:
            checks[fact] = "VERIFIED"
        elif evidence.expiresAt <= now:
            checks[fact] = "WAITING"
        else:
            single = gateway.rules.rules[deal.dealType][fact]
            state, _ = RulesEngine({deal.dealType: {fact: single}}).evaluate(deal, {fact: evidence})
            checks[fact] = {"READY": "VERIFIED", "WAITING": "WAITING", "REJECTED": "REJECTED"}[state]
    if evaluation:
        result["evaluationStatus"] = evaluation["result"]["status"]
        if not financed and result["evaluationStatus"] == "READY" and (
                not issued or issued["payload"]["expiresAt"] <= now):
            result["evaluationStatus"] = "WAITING"
    result["confirmedFacts"] = sum(value == "VERIFIED" for value in checks.values())
    result["requiredFacts"] = len(checks)
    return result
