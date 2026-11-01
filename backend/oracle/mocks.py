import os
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException

from oracle.crypto import sign_source
from oracle.models import Deal
from oracle.registry import load_json

app = FastAPI(title="Mock sources — no real bank or EDO integration")


@app.post("/{kind}/facts")
@app.post("/{kind}/alternate/facts")
def fetch(kind: str, body: dict):
    """Return independently signed facts from the fixed mock document.

    Args:
        kind: EDO or factoring endpoint.
        body: Requested deal and fact type.

    Returns:
        Source-specific signed response envelope.
    """
    runtime = Path(os.getenv("RUNTIME_DIR", "/runtime"))
    requested = Deal.model_validate(body["deal"])
    document = load_json(str(runtime / "mock-document.json"))
    deal = Deal.model_validate(document["deal"])
    fact = body["factType"]
    allowed = {"edo": ["DELIVERY_ACCEPTED", "BUYER_NOTIFIED"],
               "vtb": ["CLAIM_ASSIGNED", "FINANCING_APPROVED"]}
    if kind not in allowed or fact not in allowed[kind] or requested.dealId != deal.dealId:
        raise HTTPException(404, "mock fact not found")
    keys = load_json(os.getenv("SOURCE_KEYS_FILE", str(runtime / "source-keys.json")))
    source = "mock-edo" if kind == "edo" else "mock-vtb"
    now = int(time.time())
    evidence = {
        "schemaVersion": "1", "evidenceId": str(uuid.uuid4()), "sourceId": source,
        "factType": fact, "dealId": deal.dealId, "subjectRef": deal.subjectRef,
        "value": True, "retrievalStatus": "present", "documentHash": deal.documentHash,
        "observedAt": now, "expiresAt": now + 300, "attributes": document["facts"][fact],
    }
    evidence["sourceProof"] = sign_source(evidence, keys[source])
    return {"documentEvidence" if kind == "edo" else "bankDecision": evidence}


@app.get("/health")
@app.get("/edo/health")
@app.get("/vtb/health")
def health():
    """Return mock process readiness."""
    return {"status": "ok", "mode": "mock"}
