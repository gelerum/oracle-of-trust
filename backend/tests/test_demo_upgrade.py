from scripts.deploy import remove_old_demo


def test_old_demo_removed_without_losing_sources_or_other_requests(store, deal):
    old = deal.model_copy(update={"subjectRef": "test-supply-742"}).model_dump()
    other = deal.model_copy(update={"dealId": "0x" + "cc" * 32, "dealType": "OTHER", "subjectRef": "another-request"}).model_dump()
    store.put_deal(old)
    store.put_deal(other)
    store.configure("source", "keep", {"sourceId": "keep", "name": "My source"})
    store.configure("process", "FACTORING", {"processId": "FACTORING"})
    store.configure("workflow", "FACTORING", {"workflowId": "FACTORING", "processId": "FACTORING"})
    store.configure("workflow", "OTHER", {"workflowId": "OTHER"})
    with store.transaction(old["dealId"]) as db:
        store.audit(db, old["dealId"], "mock-edo", "DELIVERY_ACCEPTED", {}, True, "VERIFIED")
        db.execute("INSERT INTO evaluations(deal_id,result) VALUES (%s,'{}'::jsonb)", (old["dealId"],))
        db.execute("INSERT INTO attestations(deal_id) VALUES (%s)", (old["dealId"],))
    remove_old_demo(store, {"dealId": old["dealId"]})
    assert [item["id"] for item in store.requests()] == [other["dealId"]]
    assert store.history(old["dealId"]) == []
    assert store.configurations("source")[0]["sourceId"] == "keep"
    assert [item["workflowId"] for item in store.configurations("workflow")] == ["OTHER"]
    assert store.configurations("process") == []
