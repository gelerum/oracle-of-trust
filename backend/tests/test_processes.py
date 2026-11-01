import copy
import json

from eth_account import Account
import httpx
import pytest

from oracle.configuration import BusinessProcessDefinition, WorkflowDefinition
from oracle.app import app
from test_universal import universal, profile


def split_profile(identifier="SHARED"):
    combined = profile(identifier, "sensor", Account.create().address)
    process = {"processId": "READINGS", "name": "Показания датчика", "requirements": combined.pop("requirements"),
               "contextSchema": combined.pop("contextSchema"), "enabled": True}
    return process, dict(combined, processId=process["processId"])


async def test_one_process_drives_two_integrations_without_rewriting_them(universal):
    router, readings = universal
    process, first = split_profile("ONE")
    router.save_process(process)
    router.save_workflow(first)
    second = dict(first, workflowId="TWO", verifyingContract=Account.create().address)
    router.save_workflow(second)
    assert all("requirements" not in w and "contextSchema" not in w for w in router.store.configurations("workflow"))
    before = copy.deepcopy(router.store.configurations("workflow"))
    for index, integration in enumerate((first, second)):
        request_id = "0x" + f"{index + 1:02x}" * 32
        router.register_request({"requestId": request_id, "workflowId": integration["workflowId"], "subjectRef": str(index), "parameters": {"minimum": 40}})
        assert (await router.evaluate(request_id))["status"] == "READY"
    process["requirements"]["READING"]["condition"] = {"op": "gte", "value": 100}
    router.save_process(process)
    assert router.store.configurations("workflow") == before
    for index in range(2):
        assert (await router.evaluate("0x" + f"{index + 1:02x}" * 32))["status"] == "REJECTED"


def test_incompatible_rule_edit_does_not_change_process_or_integrations(universal):
    router, _ = universal
    process, integration = split_profile()
    router.save_process(process)
    router.save_workflow(integration)
    before = copy.deepcopy(router.process("READINGS"))
    bad = copy.deepcopy(process)
    bad["requirements"] = {"UNKNOWN": {"sources": ["sensor"], "equals": True}}
    with pytest.raises(ValueError, match="not registered"):
        router.save_process(bad)
    assert router.process("READINGS") == before
    bad = copy.deepcopy(process)
    bad["requirements"] = {"OTHER": {"sources": ["sensor"], "equals": True}}
    source = router.store.configurations("source")[0]
    source["factTypes"].append("OTHER")
    source["factSchemas"]["OTHER"] = {"type": "integer"}
    router.save_source(source)
    with pytest.raises(ValueError, match="absent from rules"):
        router.save_process(bad)
    assert router.process("READINGS") == before
    assert router.workflow(integration["workflowId"])["processId"] == "READINGS"


def test_disabled_process_disables_composed_integrations(universal):
    router, _ = universal
    process, integration = split_profile()
    router.save_process(process)
    router.save_workflow(integration)
    process["enabled"] = False
    router.save_process(process)
    assert router.composed(router.workflow("SHARED"))["enabled"] is False
    assert router.workflow("SHARED")["enabled"] is True
    process["enabled"] = True
    router.save_process(process)
    assert router.composed(router.workflow("SHARED"))["enabled"] is True


def test_unknown_process_and_mixed_technical_business_configuration_rejected(universal):
    router, _ = universal
    process, integration = split_profile()
    with pytest.raises(ValueError, match="unknown business"):
        router.save_workflow(integration)
    with pytest.raises(ValueError):
        BusinessProcessDefinition.model_validate(dict(process, abi=[]))
    router.save_process(process)
    with pytest.raises(ValueError):
        router.save_workflow(dict(integration, requirements=process["requirements"]))


def test_legacy_database_migration_preserves_context_identity_and_audit(universal):
    router, _ = universal
    legacy = profile("LEGACY", "sensor", Account.create().address)
    legacy["enabled"] = False
    router.store.configure("workflow", "LEGACY", legacy)
    with router.store.transaction("migration") as db:
        router.store.audit(db, "request", "sensor", "READING", {"value": 42}, True, "VERIFIED")
    router.store.initialize()
    technical = router.workflow("LEGACY")
    assert technical["processId"] == "LEGACY"
    assert "requirements" not in technical and "contextSchema" not in technical
    assert router.process("LEGACY")["requirements"] == legacy["requirements"]
    assert router.composed(technical) == WorkflowDefinition.model_validate(legacy).model_dump()
    assert len(router.store.history("request")) == 1
    router.store.initialize()
    assert len(router.store.configurations("process")) == 1
    assert len(router.store.configurations("workflow")) == 1


async def test_separate_open_api_and_link_validation(universal):
    router, _ = universal
    process, integration = split_profile()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        assert (await client.get("/processes")).json() == []
        assert (await client.post("/processes", json=process)).status_code == 201
        assert (await client.post("/processes", json=process)).status_code == 409
        response = await client.post("/workflows", json=integration)
        assert response.status_code == 201
        assert "requirements" not in response.json()
        process["name"] = "Обновлённый процесс"
        assert (await client.put("/processes/READINGS", json=process)).status_code == 200
        assert (await client.put("/processes/OTHER", json=process)).status_code == 422
        assert (await client.get("/workflows")).json()[0]["processId"] == "READINGS"
        assert (await client.get("/processes")).json()[0]["name"] == process["name"]
        assert (await client.get("/assets/processes.js")).status_code == 200
