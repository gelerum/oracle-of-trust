import json


def migrate_demo_claim(store, deal_id: str, claim_id: str):
    """Move an existing demo's documented claim reference into its request context.

    Args:
        store: PostgreSQL store.
        deal_id: Existing demo request identifier.
        claim_id: Claim reference from the original mock assignment document.
    """
    if not isinstance(claim_id, str) or not claim_id.strip():
        raise ValueError("mock assignment document has no claim reference")
    with store.transaction("configuration") as db:
        db.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (deal_id,))
        row = db.execute("SELECT data FROM deals WHERE id=%s", (deal_id,)).fetchone()
        if not row:
            return
        data = row["data"]
        if "claimId" in data and data["claimId"] != claim_id:
            raise ValueError("documented claim reference differs from immutable request")
        if "claimId" not in data:
            data["claimId"] = claim_id
            db.execute("UPDATE deals SET data=%s::jsonb WHERE id=%s", (json.dumps(data), deal_id))
        integration = db.execute("SELECT data FROM workflow_configs WHERE id=%s", (data["dealType"],)).fetchone()
        if not integration:
            return
        process_id = integration["data"]["processId"]
        row = db.execute("SELECT data FROM process_configs WHERE id=%s", (process_id,)).fetchone()
        if not row:
            return
        previous = row["data"]
        process = json.loads(json.dumps(previous))
        rule = process.get("requirements", {}).get("CLAIM_ASSIGNED", {})
        # Only replace the original demo constant; operator-defined conditions survive.
        if rule.get("required", {}).get("claimId") != "test-claim-001":
            return
        rule["required"].pop("claimId")
        rule.setdefault("attributes", {})["claimId"] = "claimId"
        schema = process.setdefault("contextSchema", {"type": "object"})
        schema.setdefault("properties", {})["claimId"] = {"type": "string", "minLength": 1, "maxLength": 128}
        if "claimId" not in schema.setdefault("required", []):
            schema["required"].append("claimId")
        db.execute("UPDATE process_configs SET data=%s::jsonb WHERE id=%s", (json.dumps(process), process_id))
        db.execute("INSERT INTO configuration_audit(kind,entity_id,previous,current) VALUES ('process',%s,%s::jsonb,%s::jsonb)",
                   (process_id, json.dumps(previous), json.dumps(process)))
