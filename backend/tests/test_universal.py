import copy
import json
import time
import uuid
from types import SimpleNamespace

from eth_account import Account
from eth_account.messages import encode_typed_data
import httpx
import pytest

from oracle.adapters import SignedHttpAdapter
from oracle.app import app
from oracle.configuration import WorkflowDefinition
from oracle.crypto import sign_source
from oracle.models import OracleRequest
from oracle.registry import AdapterFactories
from oracle.router import GatewayRouter


def profile(identifier, source, address):
    return {"workflowId": identifier, "name": identifier, "network": "local", "verifyingContract": address,
            "abi": [], "checks": [], "requirements": {"READING": {"sources": [source],
                "condition": {"op": "gte", "context": "minimum"}}},
            "contextSchema": {"type": "object", "required": ["minimum"], "properties": {"minimum": {"type": "integer"}}},
            "attestation": {"name": "Independent Consumer", "version": "7", "typeName": "Observation", "ttl": 90,
                "fields": [{"name": "requestId", "type": "bytes32"}, {"name": "value", "type": "int256"},
                           *[{"name": name, "type": "uint256"} for name in ("issuedAt", "expiresAt", "nonce")]],
                "bindings": {"requestId": {"context": "dealId"}, "value": {"fact": "READING"},
                             **{name: {"runtime": name} for name in ("issuedAt", "expiresAt", "nonce")}}}}


@pytest.fixture
def universal(store, monkeypatch):
    source_key = Account.create().key.hex()
    gateway_key = Account.create().key.hex()
    readings = {"READING": 42}
    def reply(request):
        query = json.loads(request.content)
        raw = {"schemaVersion": "1", "evidenceId": str(uuid.uuid4()), "sourceId": "sensor",
               "factType": query["factType"], "dealId": query["requestId"], "documentHash": query["documentHash"],
               "subjectRef": query["subjectRef"], "observedAt": int(time.time()), "expiresAt": int(time.time()) + 300,
               "retrievalStatus": "present", "value": readings[query["factType"]], "attributes": {}}
        raw["sourceProof"] = sign_source(raw, source_key)
        return httpx.Response(200, json={"data": raw})
    factories = AdapterFactories()
    factories.factories["signed-http"] = lambda config: SignedHttpAdapter(config, httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    def client(network, workflow, signer):
        return SimpleNamespace(chain_id=network["chainId"], address=workflow["verifyingContract"],
                               oracle_address=signer, workflow=workflow, validate_deal=lambda request: None)
    router = GatewayRouter(store, {"local": {"rpcUrl": "http://node", "chainId": 31337}}, gateway_key, factories, client)
    router.save_source({"sourceId": "sensor", "name": "Signed observations", "adapter": "signed-http", "type": "http",
                        "baseUrl": "http://sensor/facts", "responsePath": "data", "factTypes": ["READING"],
                        "factSchemas": {"READING": {"type": "integer"}}, "signer": Account.from_key(source_key).address,
                        "enabled": True, "access": {"approved": True, "reference": "demo-permission", "purpose": "test"}})
    monkeypatch.setattr(app.state, "router", router, raising=False)
    return router, readings


async def test_two_unrelated_contracts_and_numeric_facts(universal):
    router, readings = universal
    signatures = []
    for index in (1, 2):
        workflow = profile(f"CONSUMER_{index}", "sensor", Account.create().address)
        router.save_workflow(workflow)
        request_id = "0x" + f"{index:02x}" * 32
        router.register_request({"requestId": request_id, "workflowId": workflow["workflowId"],
                                 "subjectRef": f"reading-{index}", "parameters": {"minimum": 20}})
        result = await router.evaluate(request_id)
        assert result["status"] == "READY"
        assert Account.recover_message(encode_typed_data(full_message=result["typedData"]), signature=result["signature"]) == router.signer
        attestation = dict(result["attestation"])
        signature = attestation.pop("signature")
        assert attestation["value"] == 42
        assert "deliveryAccepted" not in attestation
        message = encode_typed_data(
            domain_data={"name": "Independent Consumer", "version": "7", "chainId": 31337,
                         "verifyingContract": workflow["verifyingContract"]},
            message_types={"Observation": workflow["attestation"]["fields"]}, message_data=attestation)
        assert Account.recover_message(message, signature=signature) == router.signer
        signatures.append(signature)
    assert signatures[0] != signatures[1]


async def test_changed_contract_cannot_reuse_cached_signature(universal):
    router, _ = universal
    workflow = profile("OBSERVE", "sensor", Account.create().address)
    router.save_workflow(workflow)
    request_id = "0x" + "cc" * 32
    router.register_request({"requestId": request_id, "workflowId": "OBSERVE", "subjectRef": "r", "parameters": {"minimum": 0}})
    first = await router.evaluate(request_id)
    workflow["verifyingContract"] = Account.create().address
    router.save_workflow(workflow)
    second = await router.evaluate(request_id)
    assert first["attestation"]["nonce"] != second["attestation"]["nonce"]
    assert first["attestation"]["signature"] != second["attestation"]["signature"]


async def test_schema_and_access_fail_closed(universal):
    router, readings = universal
    workflow = profile("OBSERVE", "sensor", Account.create().address)
    router.save_workflow(workflow)
    request = {"requestId": "0x" + "cc" * 32, "workflowId": "OBSERVE", "subjectRef": "r", "parameters": {"minimum": 0}}
    router.register_request(request)
    readings["READING"] = "true"
    assert (await router.evaluate(request["requestId"]))["status"] == "REJECTED"
    source = router.registry().sources["sensor"]
    source["access"] = {}
    router.save_source(source)
    assert (await router.evaluate(request["requestId"]))["status"] == "REJECTED"


async def test_open_api_and_persistence(universal):
    router, _ = universal
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        source = dict(router.registry().sources["sensor"], sourceId="other", name="Another source")
        response = await client.post("/sources", json=source)
        assert response.status_code == 201, response.text
        assert (await client.post("/sources", json=source)).status_code == 409
        source["enabled"] = False
        assert (await client.put("/sources/other", json=source)).status_code == 200
        assert router.registry().sources["other"]["enabled"] is False
        assert len((await client.get("/sources/other/audit")).json()["changes"]) == 2
        workflow = profile("OBSERVE", "other", Account.create().address)
        assert (await client.post("/workflows", json=workflow)).status_code == 201
        body = {"requestId": "0x" + "dd" * 32, "workflowId": "OBSERVE", "subjectRef": "observation", "parameters": {"minimum": 0}}
        assert (await client.post("/requests", json=body)).status_code == 201
        assert (await client.post(f"/requests/{body['requestId']}/evaluate")).json()["status"] == "WAITING"
        page = await client.get("/")
        assert 'id="page-sources"' in page.text
        assert 'id="page-processes"' in page.text
        assert "Технические интеграции" in page.text
        assert "Content-Security-Policy" in page.headers


def test_unknown_extensions_and_identity_immutability(universal):
    router, _ = universal
    source = dict(router.registry().sources["sensor"], sourceId="bad", adapter="arbitrary-code")
    with pytest.raises(ValueError, match="not installed"):
        router.save_source(source)
    source["adapter"] = "signed-http"
    source["verification"] = "trust-http-200"
    with pytest.raises(ValueError, match="not installed"):
        router.save_source(source)
    workflow = profile("OBSERVE", "sensor", Account.create().address)
    router.save_workflow(workflow)
    body = {"requestId": "0x" + "dd" * 32, "workflowId": "OBSERVE", "subjectRef": "r", "parameters": {"minimum": 0}}
    router.register_request(body)
    with pytest.raises(ValueError, match="immutable"):
        router.register_request(dict(body, subjectRef="changed"))
    with pytest.raises(ValueError, match="still required"):
        router.save_source(dict(router.registry().sources["sensor"], factTypes=["OTHER"], factSchemas={"OTHER": {"type": "boolean"}}))


@pytest.mark.parametrize("change", [{"nonce": {"constant": 1}}, {"value": {"fact": "UNAUTHORIZED"}}, {"value": {"python": "eval()"}}])
def test_incomplete_signing_profiles_rejected(change):
    workflow = profile("OBSERVE", "sensor", Account.create().address)
    workflow["attestation"]["bindings"].update(change)
    with pytest.raises(ValueError):
        WorkflowDefinition.model_validate(workflow)


async def test_arbitrary_payload_field_names_and_no_factoring_fields(universal):
    router, _ = universal
    workflow = profile("CUSTOM_NAMES", "sensor", Account.create().address)
    workflow["attestation"]["fields"] = [
        {"name": "readingId", "type": "bytes32"}, {"name": "reading", "type": "int256"},
        {"name": "sequence", "type": "uint256"}, {"name": "validUntil", "type": "uint256"}]
    workflow["attestation"]["bindings"] = {
        "readingId": {"context": "requestId", "encoding": "bytes32"}, "reading": {"fact": "READING"},
        "sequence": {"runtime": "nonce"}, "validUntil": {"runtime": "expiresAt"}}
    router.save_workflow(workflow)
    request_id = "0x" + "ee" * 32
    router.register_request({"requestId": request_id, "workflowId": "CUSTOM_NAMES", "subjectRef": "r", "parameters": {"minimum": 0}})
    first = await router.evaluate(request_id)
    assert first["status"] == "READY"
    assert set(first["attestation"]) == {"readingId", "reading", "sequence", "validUntil", "signature"}
    assert first["attestation"]["readingId"] == request_id
    assert (await router.evaluate(request_id)) == first


async def test_trusted_protocol_and_proof_extensions(universal):
    import hashlib
    import hmac
    from oracle.crypto import canonical
    router, _ = universal
    source_secret = b"test-only-independent-source-key"
    class Adapter:
        def __init__(self, config):
            self.config = config
        async def fetch_fact(self, query, fact):
            data = {"schemaVersion": "1", "sourceId": self.config["sourceId"], "evidenceId": str(uuid.uuid4()),
                    "factType": fact, "dealId": query.dealId, "documentHash": query.documentHash,
                    "subjectRef": query.subjectRef, "observedAt": int(time.time()), "expiresAt": int(time.time()) + 60,
                    "retrievalStatus": "present", "value": 42, "attributes": {}}
            data["sourceProof"] = hmac.new(source_secret, canonical(data).encode(), hashlib.sha256).hexdigest()
            return data
    def verify(evidence, source):
        expected = hmac.new(source_secret, canonical(evidence.signed_data()).encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, evidence.sourceProof)
    router.factories.register("trusted-custom-protocol", Adapter)
    router.verifiers.register("test-hmac", verify)
    router.save_source({"sourceId": "custom", "name": "Custom protocol", "adapter": "trusted-custom-protocol",
                        "adapterConfig": {"endpoint": "custom://test"}, "type": "custom", "verification": "test-hmac",
                        "factTypes": ["READING"], "factSchemas": {"READING": {"type": "integer"}}, "enabled": True,
                        "access": {"approved": True, "reference": "test-only", "purpose": "extension test"}})
    router.save_workflow(profile("CUSTOM", "custom", Account.create().address))
    request_id = "0x" + "fe" * 32
    router.register_request({"requestId": request_id, "workflowId": "CUSTOM", "subjectRef": "r", "parameters": {"minimum": 0}})
    assert (await router.evaluate(request_id))["status"] == "READY"


async def test_missing_adapter_waits_and_does_not_break_other_profiles(universal):
    router, _ = universal
    router.save_workflow(profile("OBSERVE", "sensor", Account.create().address))
    request_id = "0x" + "bb" * 32
    router.register_request({"requestId": request_id, "workflowId": "OBSERVE", "subjectRef": "r", "parameters": {"minimum": 0}})
    assert (await router.evaluate(request_id))["status"] == "READY"
    del router.factories.factories["signed-http"]
    assert router.registry().adapters["sensor"].available is False
    assert (await router.evaluate(request_id))["status"] == "WAITING"


def test_restart_seed_preserves_operator_changes(universal):
    router, _ = universal
    source = router.registry().sources["sensor"]
    old = copy.deepcopy(source)
    source["name"] = "Operator configuration"
    router.save_source(source)
    router.save_source(old, seed=True)
    assert router.registry().sources["sensor"]["name"] == "Operator configuration"
