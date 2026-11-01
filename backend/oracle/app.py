import asyncio
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from jsonschema.exceptions import SchemaError
from requests.exceptions import RequestException
from web3.exceptions import Web3Exception

from oracle.configuration import SourceDefinition
from oracle.dashboard import deal_snapshot, source_statuses
from oracle.examples import demo_factories, factoring_workflow
from oracle.registry import load_json
from oracle.router import GatewayRouter
from oracle.proofs import ProofVerifiers, load_extensions
from oracle.store import Store


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load persisted catalogues and seed the optional factoring example.

    Args:
        app: FastAPI application.

    Yields:
        Initialized gateway lifetime.
    """
    runtime = Path(os.getenv("RUNTIME_DIR", "/runtime"))
    store = Store(os.environ["DATABASE_URL"])
    store.initialize()
    key = Path(os.getenv("ORACLE_KEY_FILE", str(runtime / "oracle.key"))).read_text().strip()
    networks = load_json(os.environ["NETWORKS_CONFIG"]) if os.getenv("NETWORKS_CONFIG") else {
        "local": {"rpcUrl": os.environ["RPC_URL"], "chainId": 31337}}
    factories, verifiers = demo_factories(), ProofVerifiers()
    load_extensions(factories, verifiers)
    router = GatewayRouter(store, networks, key, factories, verifiers=verifiers)
    app.state.router = router
    # Example bootstrap uses the same registration interface as all other consumers.
    if os.getenv("SEED_DEMO", "1") == "1":
        signers = load_json(str(runtime / "source-signers.json"))
        for source in load_json(os.getenv("SOURCES_CONFIG", "config/sources.json")):
            router.save_source(dict(source, signer=signers[source["sourceId"]]), seed=True)
        deployment = load_json(str(runtime / "deployment.json"))
        artifact = load_json(str(Path(os.getenv("ARTIFACTS_DIR", "/artifacts")) / "Factoring.sol/Factoring.json"))
        definition = factoring_workflow(deployment, artifact, load_json("config/rules.json")["FACTORING"],
                                        load_json("config/attestations.json")["FACTORING"])
        # A restart must not require a demo contract whose profile the operator replaced.
        if not any(w["workflowId"] == definition["workflowId"] for w in store.configurations("workflow")):
            process_id = definition["workflowId"]
            router.save_process({"processId": process_id, "name": definition["name"],
                                 "requirements": definition.pop("requirements"),
                                 "contextSchema": definition.pop("contextSchema")}, seed=True)
            router.save_workflow(dict(definition, processId=process_id), seed=True)
        app.state.deployment = deployment
    yield


app = FastAPI(title="ВТБ Оракул Доверия", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory="frontend"), name="assets")


@app.middleware("http")
async def security_headers(request, call_next):
    """Add same-origin browser protections and disable caching of operator data.

    Args:
        request: Incoming HTTP request.
        call_next: Next application handler.

    Returns:
        Response with browser security headers.
    """
    response = await call_next(request)
    # Swagger supplies its own external assets; the operator application is buildless.
    if not request.url.path.startswith(("/docs", "/redoc")):
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    return response


def checked(function, *args):
    """Convert invalid operator configuration into an actionable API response.

    Args:
        function: Registration operation.
        *args: Validated JSON inputs.

    Returns:
        Operation result.
    """
    try:
        return function(*args)
    except RequestException as exc:
        raise HTTPException(503, "RPC endpoint unavailable") from exc
    except (ValueError, KeyError, TypeError, SchemaError, Web3Exception) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/capabilities")
def capabilities():
    """Describe installed extensions and configured networks without secrets."""
    router = app.state.router
    return {"adapters": list(router.factories.factories), "verificationMethods": list(router.verifiers.verifiers),
            "networks": {name: {"chainId": n["chainId"]} for name, n in router.networks.items()},
            "oracleSigner": router.signer, "sourceSchema": SourceDefinition.model_json_schema()}


@app.get("/sources")
def sources():
    """Return persisted source definitions and supported facts."""
    return app.state.router.registry().catalogue()


@app.post("/sources", status_code=201)
def add_source(body: dict):
    """Register an installed adapter instance.

    Args:
        body: Source metadata and transport configuration.

    Returns:
        Persisted source definition.
    """
    router = app.state.router
    if any(s["sourceId"] == body.get("sourceId") for s in router.store.configurations("source")):
        raise HTTPException(409, "source identifier already exists")
    return checked(router.save_source, body)


@app.put("/sources/{source_id}")
def edit_source(source_id: str, body: dict):
    """Replace a registered source definition with audit.

    Args:
        source_id: Immutable source identifier.
        body: Full replacement definition.

    Returns:
        Persisted source definition.
    """
    router = app.state.router
    if source_id not in router.registry().sources:
        raise HTTPException(404, "unknown source")
    if body.get("sourceId") != source_id:
        raise HTTPException(422, "source identifier cannot change")
    return checked(router.save_source, body)


@app.get("/sources/status")
async def statuses():
    """Probe configured health endpoints without collecting factual evidence."""
    return await source_statuses(app.state.router.registry())


@app.get("/sources/{source_id}/audit")
def source_audit(source_id: str):
    """Read recent source evidence attempts and configuration revisions.

    Args:
        source_id: Registered source identifier.

    Returns:
        Evidence and configuration audit records.
    """
    store = app.state.router.store
    with store.transaction("configuration") as db:
        changes = db.execute("SELECT id,previous,current,created_at FROM configuration_audit WHERE kind='source' AND entity_id=%s ORDER BY id DESC LIMIT 50", (source_id,)).fetchall()
    return {"evidence": store.source_history(source_id), "changes": changes}


@app.post("/sources/{source_id}/probe")
async def probe(source_id: str):
    """Probe one source without treating availability as a verified fact.

    Args:
        source_id: Source to check.

    Returns:
        Transport availability.
    """
    registry = app.state.router.registry()
    if source_id not in registry.sources:
        raise HTTPException(404, "unknown source")
    registry.sources = {source_id: registry.sources[source_id]}
    return (await source_statuses(registry))[0]


@app.get("/processes")
def processes():
    """List business rules without ABI or network configuration."""
    return app.state.router.store.configurations("process")


@app.post("/processes", status_code=201)
def add_process(body: dict):
    """Register a business process.

    Args:
        body: Process identity, facts, authorities and conditions.

    Returns:
        Persisted business definition.
    """
    router = app.state.router
    if any(p["processId"] == body.get("processId") for p in router.store.configurations("process")):
        raise HTTPException(409, "business process identifier already exists")
    return checked(router.save_process, body)


@app.put("/processes/{process_id}")
def edit_process(process_id: str, body: dict):
    """Replace business rules without rewriting technical integrations.

    Args:
        process_id: Immutable process identifier.
        body: Replacement business definition.

    Returns:
        Persisted business definition.
    """
    if body.get("processId") != process_id:
        raise HTTPException(422, "business process identifier cannot change")
    checked(app.state.router.process, process_id)
    return checked(app.state.router.save_process, body)


@app.get("/workflows")
def workflows():
    """List registered contract consumer profiles."""
    return app.state.router.store.configurations("workflow")


@app.post("/workflows", status_code=201)
def add_workflow(body: dict):
    """Register a contract, source authorities and typed attestation bindings.

    Args:
        body: Consumer definition.

    Returns:
        Persisted consumer profile.
    """
    router = app.state.router
    if any(w["workflowId"] == body.get("workflowId") for w in router.store.configurations("workflow")):
        raise HTTPException(409, "consumer identifier already exists")
    return checked(router.save_workflow, body)


@app.put("/workflows/{workflow_id}")
def edit_workflow(workflow_id: str, body: dict):
    """Replace an audited consumer profile.

    Args:
        workflow_id: Immutable consumer identifier.
        body: Replacement definition.

    Returns:
        Persisted consumer profile.
    """
    if body.get("workflowId") != workflow_id:
        raise HTTPException(422, "consumer identifier cannot change")
    checked(app.state.router.workflow, workflow_id)
    return checked(app.state.router.save_workflow, body)


@app.get("/responses")
def responses(sourceId: str | None = None, requestId: str | None = None,
              factType: str | None = None, before: int | None = Query(default=None, ge=1),
              limit: int = Query(default=50, ge=1, le=200)):
    """Read source replies without initiating any external queries.

    Args:
        sourceId: Optional registered source filter.
        requestId: Optional request filter.
        factType: Optional requested fact filter.
        before: Pagination cursor.
        limit: Maximum page size.

    Returns:
        Source response audit with validation and rule results.
    """
    return app.state.router.store.responses(sourceId, requestId, factType, before, limit)


@app.get("/requests")
def requests():
    """Read registered requests and their latest backend decisions."""
    return app.state.router.store.requests()


@app.post("/requests", status_code=201)
def add_request(body: dict):
    """Register immutable request context without executing a transaction.

    Args:
        body: Request identity, profile and parameters.

    Returns:
        Persisted request.
    """
    return checked(app.state.router.register_request, body)


@app.post("/requests/{deal_id}/evaluate")
@app.post("/deals/{deal_id}/evaluate")
async def evaluate(deal_id: str):
    """Evaluate verified facts through the request's configured contract profile.

    Args:
        deal_id: Registered request identifier.

    Returns:
        READY, WAITING or REJECTED and an optional typed attestation.
    """
    if re.fullmatch(r"0x[0-9a-fA-F]{64}", deal_id) is None:
        raise HTTPException(422, "invalid request identifier")
    try:
        return await app.state.router.evaluate(deal_id)
    except RequestException as exc:
        raise HTTPException(503, "RPC endpoint unavailable") from exc
    except (ValueError, KeyError, TypeError, Web3Exception) as exc:
        raise HTTPException(422, str(exc)) from exc


@app.get("/requests/{deal_id}/evidence")
@app.get("/deals/{deal_id}/evidence")
def evidence(deal_id: str):
    """Return accepted and rejected evidence attempts.

    Args:
        deal_id: Registered request identifier.

    Returns:
        Chronological source audit records.
    """
    return app.state.router.store.history(deal_id)


@app.get("/health")
def health():
    """Return process readiness."""
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
def panel():
    """Serve the source management panel."""
    return FileResponse("frontend/index.html")


@app.get("/dashboard")
async def dashboard():
    """Read the legacy demonstration status without evaluating or paying.

    Returns:
        Source status and optional factoring example snapshot.
    """
    if hasattr(app.state, "router"):
        registry = app.state.router.registry()
        statuses = await source_statuses(registry)
        if not hasattr(app.state, "deployment"):
            return {"sources": statuses, "status": "UNAVAILABLE", "evaluationStatus": "WAITING", "checks": {}, "local": False, "dealId": ""}
        try:
            gateway = await asyncio.to_thread(app.state.router.gateway, app.state.deployment.get("workflowId", "FACTORING"))
            snapshot = await asyncio.to_thread(deal_snapshot, gateway, app.state.deployment)
        except (ValueError, RequestException, Web3Exception):
            snapshot = {"status": "UNAVAILABLE", "evaluationStatus": "WAITING", "checks": {}, "local": False, "dealId": app.state.deployment["dealId"], "error": "Демо-контракт недоступен"}
        return dict(snapshot, sources=statuses)
    # Direct service injection is retained for isolated read-only panel tests.
    gateway = app.state.gateway
    statuses, snapshot = await asyncio.gather(source_statuses(gateway.registry),
        asyncio.to_thread(deal_snapshot, gateway, app.state.deployment))
    return dict(snapshot, sources=statuses)
