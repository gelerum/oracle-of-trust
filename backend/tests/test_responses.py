import json
from types import SimpleNamespace

import httpx
import pytest

from oracle.app import app
from oracle.responses import ResponseFormatError, SourceReply, http_reply, response_details


def test_original_response_is_not_signed_evidence():
    response = httpx.Response(200, json={"payload": {"value": False}}, request=httpx.Request("POST", "http://mock"))
    reply = http_reply(response, lambda body: body["payload"])
    assert reply == {"value": False}
    assert json.loads(reply.original) == {"payload": {"value": False}}
    assert response_details(reply)["transport"]["httpStatus"] == 200
    assert "transport" not in json.dumps(reply)


def test_malformed_and_failed_http_responses_are_preserved():
    response = httpx.Response(200, text="broken json", request=httpx.Request("POST", "http://mock"))
    with pytest.raises(ResponseFormatError) as error:
        http_reply(response, lambda body: body)
    details = response_details({}, error.value)
    assert details["originalResponse"] == "broken json"
    assert details["transport"]["httpStatus"] == 200
    response = httpx.Response(503, text="maintenance", request=httpx.Request("POST", "http://mock", headers={"Authorization": "secret"}))
    with pytest.raises(httpx.HTTPStatusError) as error:
        http_reply(response, lambda body: body)
    details = response_details({}, error.value)
    assert details["transport"]["httpStatus"] == 503
    assert details["originalResponse"] == "maintenance"
    assert "secret" not in json.dumps(details)
    assert response_details({}, httpx.ReadTimeout("timeout"))["transport"] == {"status": "timeout"}


async def test_verified_negative_response_has_rejected_business_rule(gateway, deal, evidence_factory):
    class Negative:
        async def fetch_fact(self, query, fact):
            evidence = evidence_factory(fact, value=False)
            return SourceReply(evidence, {"status": "received", "httpStatus": 200}, json.dumps({"result": evidence}))
    gateway.registry.adapters["mock-vtb"] = Negative()
    assert (await gateway.evaluate(deal.dealId))["status"] == "REJECTED"
    page = gateway.store.responses(source_id="mock-vtb", request_id=deal.dealId)
    assert page["total"] == 2
    for row in page["items"]:
        assert row["accepted"] is True
        assert row["raw"]["value"] is False
        assert row["details"]["validation"]["status"] == "VERIFIED"
        assert row["details"]["validation"]["checks"]["origin"] == "VERIFIED"
        assert row["details"]["rules"]["status"] == "REJECTED"
        assert json.loads(row["details"]["originalResponse"])["result"] == row["raw"]


async def test_timeout_and_invalid_signature_have_distinct_reports(gateway, deal, evidence_factory):
    class Unavailable:
        async def fetch_fact(self, query, fact):
            raise httpx.ReadTimeout("timeout")
    class Invalid:
        async def fetch_fact(self, query, fact):
            raw = evidence_factory(fact)
            raw["value"] = False
            return raw
    gateway.registry.adapters["mock-edo"] = Unavailable()
    gateway.registry.adapters["mock-vtb"] = Invalid()
    assert (await gateway.evaluate(deal.dealId))["status"] == "REJECTED"
    timeout = gateway.store.responses(source_id="mock-edo")["items"][0]
    assert timeout["details"]["transport"]["status"] == "timeout"
    assert timeout["details"]["validation"]["status"] == "NOT_CHECKED"
    invalid = gateway.store.responses(source_id="mock-vtb")["items"][0]
    assert invalid["details"]["validation"]["checks"]["origin"] == "REJECTED"
    assert invalid["details"]["validation"]["checks"]["document"] == "NOT_CHECKED"


async def test_responses_api_filters_cursor_and_readonly(gateway, deal):
    await gateway.evaluate(deal.dealId)
    with gateway.store.transaction(deal.dealId) as db:
        gateway.store.audit(db, deal.dealId, "gateway", "CHAIN_STATE", {}, False, "test")
    app.state.router = SimpleNamespace(store=gateway.store)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        first = (await client.get("/responses", params={"limit": 2})).json()
        second = (await client.get("/responses", params={"limit": 2, "before": first["nextCursor"]})).json()
        assert first["total"] == second["total"] == 4
        assert len({row["id"] for row in first["items"] + second["items"]}) == 4
        assert second["nextCursor"] is None
        filtered = (await client.get("/responses", params={"sourceId": "mock-edo", "requestId": deal.dealId, "factType": "DELIVERY_ACCEPTED"})).json()
        assert filtered["total"] == 1
        assert filtered["items"][0]["raw"]["factType"] == "DELIVERY_ACCEPTED"
        assert (await client.get("/responses?limit=0")).status_code == 422
        assert (await client.get("/responses?before=-1")).status_code == 422
        assert (await client.get("/responses?sourceId=absent")).json()["items"] == []
        assert (await client.get("/assets/responses.js")).status_code == 200
    assert len(gateway.store.history(deal.dealId)) == 5
