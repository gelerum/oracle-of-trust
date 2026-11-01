from eth_account import Account
from jsonschema import Draft202012Validator

from oracle.chain import ContractClient
from oracle.configuration import SourceDefinition, WorkflowDefinition, BusinessProcessDefinition, IntegrationDefinition, context_data
from oracle.models import OracleRequest
from oracle.registry import Registry
from oracle.rules import RulesEngine
from oracle.service import Gateway
from oracle.validation import ConfiguredAccessPolicy, EvidenceValidator
from oracle.proofs import ProofVerifiers


class GatewayRouter:
    """Route requests to independent, persisted contract consumer profiles.

    Args:
        store: PostgreSQL persistence.
        networks: Trusted network identifier to RPC configuration mapping.
        key: Gateway signer key.
        factories: Installed trusted adapter factories.
        client_factory: Injectable ABI contract client factory.
        verifiers: Installed origin verification extensions.
    """

    def __init__(self, store, networks: dict, key: str, factories, client_factory=ContractClient, verifiers=None):
        self.store, self.networks, self.key = store, networks, key
        self.factories, self.client_factory = factories, client_factory
        self.signer = Account.from_key(key).address
        self.verifiers = verifiers or ProofVerifiers()

    def registry(self) -> Registry:
        """Build a fresh source catalogue so edits apply across workers.

        Returns:
            Immutable registry snapshot for one evaluation.
        """
        return Registry(self.store.configurations("source"), factories=self.factories)

    def workflow(self, workflow_id: str) -> dict:
        """Find a registered consumer profile.

        Args:
            workflow_id: Requested profile identifier.

        Returns:
            Persisted profile definition.
        """
        for item in self.store.configurations("workflow"):
            if item["workflowId"] == workflow_id:
                return item
        raise ValueError("unknown consumer profile")

    def save_source(self, definition: dict, seed: bool = False) -> dict:
        """Validate and persist an adapter instance through one common interface.

        Args:
            definition: Source configuration, including public verification key.
            seed: Preserve operator configuration during demo bootstrap.

        Returns:
            Validated source definition.
        """
        if seed:
            existing = next((item for item in self.store.configurations("source")
                             if item["sourceId"] == definition.get("sourceId")), None)
            if existing:
                return existing
        source = SourceDefinition.model_validate(definition).model_dump()
        if source["signer"].lower() == self.signer.lower():
            raise ValueError("source and gateway signing keys must differ")
        method = "eip191" if source["verification"] == "EIP-191 test signature" else source["verification"]
        if method not in self.verifiers.verifiers:
            raise ValueError("proof verification method is not installed")
        for workflow in self.store.configurations("process"):
            for fact, rule in workflow["requirements"].items():
                if source["sourceId"] in rule["sources"] and fact not in source["factTypes"]:
                    raise ValueError(f"source is still required for {workflow['processId']}:{fact}")
        self.factories.create(source)
        self.store.configure("source", source["sourceId"], source, seed=seed)
        return source

    def process(self, process_id: str) -> dict:
        """Find a business process independently of its integrations.

        Args:
            process_id: Registered business process identifier.

        Returns:
            Persisted business definition.
        """
        for item in self.store.configurations("process"):
            if item["processId"] == process_id:
                return item
        raise ValueError("unknown business process")

    def composed(self, integration: dict, process: dict | None = None) -> dict:
        """Resolve business rules into a validated runtime consumer snapshot.

        Args:
            integration: Technical integration definition.
            process: Optional proposed business definition for validation.

        Returns:
            Complete evaluator configuration with effective availability.
        """
        process = process or self.process(integration["processId"])
        technical = {key: value for key, value in integration.items() if key != "processId"}
        return WorkflowDefinition.model_validate(dict(technical,
            requirements=process["requirements"], contextSchema=process["contextSchema"],
            enabled=technical["enabled"] and process["enabled"])).model_dump()

    def save_process(self, definition: dict, seed: bool = False) -> dict:
        """Validate business authorities and all existing integration bindings.

        Args:
            definition: Business facts and context schema.
            seed: Preserve an existing process during bootstrap.

        Returns:
            Persisted business definition.
        """
        process = BusinessProcessDefinition.model_validate(definition).model_dump()
        if seed:
            existing = next((p for p in self.store.configurations("process") if p["processId"] == process["processId"]), None)
            if existing:
                return existing
        self.validate_authorities(process)
        for integration in self.store.configurations("workflow"):
            if integration["processId"] == process["processId"]:
                self.composed(integration, process)
        self.store.configure("process", process["processId"], process, seed=seed)
        return process

    def validate_authorities(self, process: dict):
        """Require an authorized registered source for every selected fact.

        Args:
            process: Proposed business definition.
        """
        sources = self.registry().sources
        for fact, rule in process["requirements"].items():
            for sid in rule["sources"]:
                if sid not in sources or fact not in sources[sid]["factTypes"]:
                    raise ValueError(f"source {sid} is not registered for {fact}")

    def save_workflow(self, definition: dict, seed: bool = False) -> dict:
        """Register a technical integration referencing a business process.

        Args:
            definition: Integration or legacy combined consumer profile.
            seed: Preserve existing configuration on restart.

        Returns:
            Technical integration without duplicated business rules.
        """
        if seed:
            existing = next((w for w in self.store.configurations("workflow") if w["workflowId"] == definition.get("workflowId")), None)
            if existing:
                return existing
        legacy_process = None
        if "requirements" in definition:
            legacy = WorkflowDefinition.model_validate(definition).model_dump()
            legacy_process = BusinessProcessDefinition.model_validate({
                "processId": legacy["workflowId"], "name": legacy["name"],
                "requirements": legacy.pop("requirements"), "contextSchema": legacy.pop("contextSchema")}).model_dump()
            existing_process = next((p for p in self.store.configurations("process")
                                     if p["processId"] == legacy_process["processId"]), None)
            if existing_process:
                legacy_process["enabled"] = existing_process["enabled"]
            self.validate_authorities(legacy_process)
            definition = dict(legacy, processId=legacy_process["processId"])
            for linked in self.store.configurations("workflow"):
                if linked["processId"] == legacy_process["processId"]:
                    self.composed(linked, legacy_process)
        integration = IntegrationDefinition.model_validate(definition).model_dump()
        workflow = self.composed(integration, legacy_process)
        if workflow["network"] not in self.networks:
            raise ValueError("network is not configured")
        self.client_factory(self.networks[workflow["network"]], workflow, self.signer)
        if legacy_process:
            self.store.configure_profiles(legacy_process, integration, seed=seed)
        else:
            self.store.configure("workflow", integration["workflowId"], integration, seed=seed)
        return integration

    def gateway(self, workflow_id: str) -> Gateway:
        """Construct a consumer-specific gateway from current catalogue snapshots.

        Args:
            workflow_id: Registered consumer identifier.

        Returns:
            Generic evaluator with that consumer's contract and signing profile.
        """
        workflow = self.composed(self.workflow(workflow_id))
        client = self.client_factory(self.networks[workflow["network"]], workflow, self.signer)
        return Gateway(self.registry(), RulesEngine({workflow_id: workflow["requirements"]}),
                       self.store, client, self.key, policy=ConfiguredAccessPolicy(),
                       validator=EvidenceValidator(verifiers=self.verifiers),
                       profiles={workflow_id: workflow["attestation"]})

    def register_request(self, definition: dict) -> dict:
        """Register immutable context against a consumer's declared schema.

        Args:
            definition: Request identity, consumer profile and parameters.

        Returns:
            Validated request metadata.
        """
        request = OracleRequest.model_validate(definition)
        workflow = self.composed(self.workflow(request.dealType))
        if list(Draft202012Validator(workflow["contextSchema"]).iter_errors(context_data(request))):
            raise ValueError("request does not match the consumer context schema")
        with self.store.transaction(request.dealId) as db:
            existing = db.execute("SELECT data FROM deals WHERE id=%s", (request.dealId,)).fetchone()
            if existing:
                if existing["data"] != request.model_dump():
                    raise ValueError("request identifier already has different immutable context")
                return existing["data"]
            import json
            db.execute("INSERT INTO deals VALUES (%s,%s::jsonb)", (request.dealId, json.dumps(request.model_dump(), allow_nan=False)))
        return request.model_dump()

    async def evaluate(self, request_id: str) -> dict:
        """Evaluate a request using its own contract, sources and EIP-712 domain.

        Args:
            request_id: Registered request identifier.

        Returns:
            Decision and optional signed consumer-specific attestation.
        """
        import asyncio
        with self.store.transaction(request_id) as db:
            row = db.execute("SELECT data FROM deals WHERE id=%s", (request_id,)).fetchone()
        if not row:
            return {"status": "REJECTED", "reasons": ["unknown request"]}
        gateway = await asyncio.to_thread(self.gateway, row["data"]["dealType"])
        result = await gateway.evaluate(request_id)
        if result["status"] == "READY":
            profile = gateway.attestor.profiles[row["data"]["dealType"]]
            result["typedData"] = {
                "domain": {"name": profile["name"], "version": profile["version"],
                           "chainId": gateway.chain.chain_id, "verifyingContract": gateway.chain.address},
                "primaryType": profile["typeName"],
                "types": {"EIP712Domain": [{"name": "name", "type": "string"},
                           {"name": "version", "type": "string"}, {"name": "chainId", "type": "uint256"},
                           {"name": "verifyingContract", "type": "address"}],
                          **profile.get("types", {}), profile["typeName"]: profile["fields"]},
                "message": result["payload"],
            }
        return result
