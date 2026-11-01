import json

import httpx
import pytest
from eth_account import Account

from oracle.adapters import AlternateEdoAdapter, MockEdoAdapter, MockVtbFactoringAdapter
from oracle.mocks import app
from oracle.validation import EvidenceValidator


@pytest.fixture
def mock_document(tmp_path, monkeypatch, deal, keys, rules):
    monkeypatch.setenv("RUNTIME_DIR", str(tmp_path))
    facts = {fact: {**{k: getattr(deal, v) for k, v in rule["attributes"].items()}, **rule["required"]}
             for fact, rule in rules.rules["FACTORING"].items()}
    (tmp_path / "mock-document.json").write_text(json.dumps({"deal": deal.model_dump(), "facts": facts}))
    (tmp_path / "source-keys.json").write_text(json.dumps(keys))


@pytest.mark.parametrize("adapter_class,url,fact,source", [
    (MockEdoAdapter, "http://mock/edo", "DELIVERY_ACCEPTED", "mock-edo"),
    (AlternateEdoAdapter, "http://mock/edo", "BUYER_NOTIFIED", "mock-edo"),
    (MockVtbFactoringAdapter, "http://mock/vtb", "CLAIM_ASSIGNED", "mock-vtb"),
])
async def test_mock_http_interfaces(adapter_class, url, fact, source, mock_document, deal, registry):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        adapter = adapter_class(url, client)
        raw = await adapter.fetch_fact(deal, fact)
    assert EvidenceValidator().validate(raw, registry.sources[source], fact, deal).value is True


async def test_alternate_adapter_same_rules(mock_document, deal, registry, rules):
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as client:
        adapters = {"mock-edo": AlternateEdoAdapter("http://mock/edo", client),
                    "mock-vtb": MockVtbFactoringAdapter("http://mock/vtb", client)}
        evidence = {}
        for fact, rule in rules.rules["FACTORING"].items():
            source = rule["sources"][0]
            raw = await adapters[source].fetch_fact(deal, fact)
            evidence[fact] = EvidenceValidator().validate(raw, registry.sources[source], fact, deal)
    assert rules.evaluate(deal, evidence) == ("READY", [])


@pytest.mark.parametrize("status,body", [(200, {}), (200, {"documentEvidence": "true"}), (503, {})])
async def test_bad_http_response(status, body, deal):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status, json=body))) as client:
        with pytest.raises((ValueError, httpx.HTTPStatusError)):
            await MockEdoAdapter("http://mock/edo", client).fetch_fact(deal, "DELIVERY_ACCEPTED")


async def test_configured_http_query_and_response_mapping(deal, evidence_factory, registry, monkeypatch):
    from oracle.adapters import SignedHttpAdapter
    raw = evidence_factory()
    monkeypatch.setenv("TEST_PROVIDER_AUTH", "test-provider-token")
    def response(request):
        assert json.loads(request.content) == {"id": deal.dealId, "operation": "DELIVERY_ACCEPTED"}
        assert request.headers["Authorization"] == "test-provider-token"
        return httpx.Response(200, json={"data": {"original": raw}})
    config = {"baseUrl": "http://provider/check", "method": "POST", "responsePath": "data",
              "requestBindings": {"id": {"context": "requestId"}, "operation": {"runtime": "factType"}},
              "fieldMap": {name: "original." + name for name in raw},
              "headersEnv": {"Authorization": "TEST_PROVIDER_AUTH"}}
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        normalized = await SignedHttpAdapter(config, client).fetch_fact(deal, "DELIVERY_ACCEPTED")
    assert normalized == raw
    assert EvidenceValidator().validate(normalized, registry.sources["mock-edo"], "DELIVERY_ACCEPTED", deal).value is True


async def test_signed_file_and_path_confinement(tmp_path, monkeypatch, deal, evidence_factory):
    from oracle.adapters import SignedFileAdapter
    monkeypatch.setenv("SOURCE_FILES_DIR", str(tmp_path))
    raw = evidence_factory()
    (tmp_path / "evidence.json").write_text(json.dumps(raw))
    assert await SignedFileAdapter({"filePath": "evidence.json"}).fetch_fact(deal, "DELIVERY_ACCEPTED") == raw
    with pytest.raises(ValueError, match="inside"):
        await SignedFileAdapter({"filePath": "../outside.json"}).fetch_fact(deal, "DELIVERY_ACCEPTED")
