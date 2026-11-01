from typing import Literal
from urllib.parse import urlsplit

from eth_utils import is_address
from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SourceDefinition(BaseModel):
    """Describe a source independently of consumers and contracts.

    Args:
        sourceId: Stable identifier used in rules and audit.
        name: Operator-facing name.
        type: Transport category.
        adapter: Trusted adapter factory identifier.
        factTypes: Facts this source is authorized to provide.
        signer: Public EIP-191 source signer.
        baseUrl: HTTP endpoint without embedded credentials.
        responsePath: Path to the signed evidence in a response envelope.
        fieldMap: Canonical field to raw response path mapping.
        factSchemas: JSON schemas for individual fact values.
        access: Operator access decision and reference; not legal consent.
        **data: Transport, version and availability settings.
    """

    model_config = ConfigDict(extra="forbid")
    sourceId: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    name: str = Field(min_length=1, max_length=160)
    type: Literal["mock", "http", "file", "webhook", "custom"]
    adapter: str
    adapterConfig: dict = Field(default_factory=dict)
    factTypes: list[str] = Field(min_length=1)
    signer: str = ""
    baseUrl: str = ""
    healthUrl: str = ""
    responsePath: str = ""
    fieldMap: dict[str, str] = Field(default_factory=dict)
    requestBindings: dict[str, dict] = Field(default_factory=dict)
    factSchemas: dict[str, dict] = Field(default_factory=dict)
    method: Literal["GET", "POST"] = "POST"
    timeout: float = Field(default=3, ge=0.1, le=30)
    headersEnv: dict[str, str] = Field(default_factory=dict)
    filePath: str = ""
    schemaVersion: Literal["1"] = "1"
    adapterVersion: str = "1"
    enabled: bool = False
    verification: str = "eip191"
    verificationConfig: dict = Field(default_factory=dict)
    accessConditions: str = ""
    access: dict = Field(default_factory=dict)

    @field_validator("signer")
    @classmethod
    def validate_signer(cls, value):
        """Validate a public signer address.

        Args:
            value: Configured signer address.

        Returns:
            Validated public address.
        """
        if value and (not is_address(value) or int(value, 16) == 0):
            raise ValueError("source signer must be a nonzero Ethereum address")
        return value

    @model_validator(mode="after")
    def validate_transport(self):
        """Validate transport endpoints and declared value schemas.

        Returns:
            Validated source definition.
        """
        if self.verification in ("eip191", "EIP-191 test signature") and not self.signer:
            raise ValueError("EIP-191 requires a public source signer")
        for value in (self.baseUrl, self.healthUrl):
            if value:
                url = urlsplit(value)
                if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password:
                    raise ValueError("HTTP URLs must not contain credentials")
        if self.type in ("http", "mock") and not self.baseUrl:
            raise ValueError("HTTP sources require baseUrl")
        if self.type == "webhook":
            raise ValueError("push transport is not enabled; register a pull adapter")
        if self.type == "file" and not self.filePath:
            raise ValueError("file sources require filePath")
        if len(set(self.factTypes)) != len(self.factTypes):
            raise ValueError("duplicate fact types")
        for binding in self.requestBindings.values():
            validate_binding(binding)
            if "fact" in binding:
                raise ValueError("outbound queries cannot bind uncollected facts")
        for fact in self.factTypes:
            self.factSchemas.setdefault(fact, {"type": "boolean"})
            Draft202012Validator.check_schema(self.factSchemas[fact])
        if set(self.factSchemas) != set(self.factTypes):
            raise ValueError("fact schemas must match authorized fact types")
        return self


class BusinessProcessDefinition(BaseModel):
    """Describe required facts without a contract or network dependency.

    Args:
        processId: Stable business process identifier.
        name: Operator-facing process name.
        requirements: Facts, trusted sources and business conditions.
        contextSchema: Schema of business terms supplied with a request.
        enabled: Whether linked integrations may authorize requests.
    """

    model_config = ConfigDict(extra="forbid")
    processId: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    name: str = Field(min_length=1, max_length=160)
    requirements: dict[str, dict] = Field(min_length=1)
    contextSchema: dict = Field(default_factory=lambda: {"type": "object"})
    enabled: bool = True

    @model_validator(mode="after")
    def validate_process(self):
        """Validate business context and declarative fact conditions.

        Returns:
            Validated business process.
        """
        Draft202012Validator.check_schema(self.contextSchema)
        validate_requirements(self.requirements)
        return self


class IntegrationDefinition(BaseModel):
    """Bind a business process to a technical contract integration.

    Args:
        workflowId: Stable integration identifier used by existing requests.
        processId: Referenced business process.
        name: Operator-facing integration name.
        network: Configured network identifier.
        verifyingContract: Attestation consumer address.
        abi: Contract ABI for state reads.
        attestation: EIP-712 types and business fact bindings.
        checks: Contract state assertions.
        enabled: Integration availability.
        signerFunction: Optional contract getter for the trusted oracle address.
    """

    model_config = ConfigDict(extra="forbid")
    workflowId: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    processId: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    name: str = Field(min_length=1, max_length=160)
    network: str = "local"
    verifyingContract: str
    abi: list[dict]
    attestation: dict
    checks: list[dict] = Field(default_factory=list)
    enabled: bool = True
    signerFunction: str | None = None


def validate_requirements(requirements: dict):
    """Check conditions independently of contract bindings.

    Args:
        requirements: Fact types mapped to allowed sources and conditions.
    """
    for fact, rule in requirements.items():
        if not isinstance(rule.get("sources"), list) or not rule["sources"] or any(
                not isinstance(sid, str) for sid in rule["sources"]) or not ("equals" in rule or "condition" in rule):
            raise ValueError(f"incomplete requirement: {fact}")
        for key in ("attributes", "required"):
            if key in rule and not isinstance(rule[key], dict):
                raise ValueError(f"{key} must be an object")
        if any(not isinstance(path, str) or not path for path in rule.get("attributes", {}).values()):
            raise ValueError("business attribute bindings require context paths")
        condition = rule.get("condition", {})
        if not isinstance(condition, dict):
            raise ValueError("condition must be an object")
        if condition and condition.get("op") not in ("eq", "ne", "gt", "gte", "lt", "lte", "in"):
            raise ValueError("unsupported condition operator")
        if "condition" in rule and (("context" in condition) == ("value" in condition)):
            raise ValueError("condition requires exactly one expected context or value")
        if "context" in condition and (not isinstance(condition["context"], str) or not condition["context"]):
            raise ValueError("condition requires a nonempty context path")


class WorkflowDefinition(BaseModel):
    """Bind source facts, rules and typed data to one contract.

    Args:
        workflowId: Stable consumer profile identifier.
        name: Operator-facing profile name.
        network: Configured RPC network identifier.
        verifyingContract: Contract receiving the signed attestation.
        abi: Contract ABI used only for read operations.
        requirements: Fact requirements and allowed source authorities.
        attestation: EIP-712 domain, types and payload bindings.
        checks: Declarative on-chain assertions before signing.
        contextSchema: JSON schema for registered request context.
        **data: Profile availability and optional signer getter.
    """

    model_config = ConfigDict(extra="forbid")
    workflowId: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,80}$")
    name: str = Field(min_length=1, max_length=160)
    network: str = "local"
    verifyingContract: str
    abi: list[dict]
    requirements: dict[str, dict] = Field(min_length=1)
    attestation: dict
    checks: list[dict] = Field(default_factory=list)
    contextSchema: dict = Field(default_factory=lambda: {"type": "object"})
    enabled: bool = True
    signerFunction: str | None = None

    @model_validator(mode="after")
    def validate_profile(self):
        """Reject incomplete rules and unsigned payload fields.

        Returns:
            Validated consumer definition.
        """
        if not is_address(self.verifyingContract) or int(self.verifyingContract, 16) == 0:
            raise ValueError("invalid verifying contract")
        Draft202012Validator.check_schema(self.contextSchema)
        p = self.attestation
        for key in ("name", "version", "typeName", "fields", "ttl", "bindings"):
            if key not in p:
                raise ValueError(f"attestation is missing {key}")
        if type(p["ttl"]) is not int or not 1 <= p["ttl"] <= 3600:
            raise ValueError("attestation ttl must be 1..3600 seconds")
        if not isinstance(p["fields"], list) or not p["fields"] or not isinstance(p["bindings"], dict):
            raise ValueError("attestation fields and bindings have invalid types")
        if any(not isinstance(field, dict) or not isinstance(field.get("name"), str)
               or not isinstance(field.get("type"), str) for field in p["fields"]):
            raise ValueError("every typed field needs a name and Solidity type")
        if any(not isinstance(p[key], str) or not p[key] for key in ("name", "version", "typeName")):
            raise ValueError("attestation domain and type names must be nonempty strings")
        if "types" in p and not isinstance(p["types"], dict):
            raise ValueError("nested EIP-712 types must be an object")
        if p["typeName"] == "EIP712Domain" or "EIP712Domain" in p.get("types", {}):
            raise ValueError("EIP712Domain is controlled by the gateway")
        names = [field["name"] for field in p["fields"]]
        if len(set(names)) != len(names) or set(names) != set(p["bindings"]):
            raise ValueError("every EIP-712 field requires exactly one binding")
        # Gateway replay protection and expiry must be signed by every consumer profile.
        fields = {field["name"]: field["type"] for field in p["fields"]}
        for semantic in ("nonce", "expiresAt"):
            matches = [name for name, binding in p["bindings"].items() if binding == {"runtime": semantic}]
            if len(matches) != 1 or fields[matches[0]] != "uint256":
                raise ValueError(f"exactly one signed uint256 runtime binding required: {semantic}")
        validate_requirements(self.requirements)
        for binding in p["bindings"].values():
            validate_binding(binding)
            if "runtime" in binding and binding["runtime"] not in ("nonce", "issuedAt", "expiresAt"):
                raise ValueError("unknown attestation runtime value")
            if "fact" in binding and binding["fact"] not in self.requirements:
                raise ValueError("payload refers to a fact absent from rules")
        for check in self.checks:
            if not isinstance(check.get("function"), str) or not isinstance(check.get("assertions"), list) or not check["assertions"]:
                raise ValueError("contract reads require assertions")
            for assertion in check["assertions"]:
                if not isinstance(assertion, dict) or ("context" in assertion) == ("equals" in assertion):
                    raise ValueError("contract assertion requires exactly one expected context or value")
                if assertion.get("encoding") not in (None, "address", "bytes32"):
                    raise ValueError("unsupported contract assertion encoding")
            for argument in check.get("arguments", []):
                validate_binding(argument)
        return self


def validate_binding(binding: dict):
    """Accept only declarative lookups, never executable expressions.

    Args:
        binding: Context, fact, runtime or constant binding.
    """
    if not isinstance(binding, dict) or len(set(binding) & {"context", "fact", "runtime", "constant"}) != 1:
        raise ValueError("binding must select exactly one value origin")
    if binding.get("encoding") not in (None, "bytes32", "address"):
        raise ValueError("unsupported binding encoding")
    if set(binding) - {"context", "fact", "runtime", "constant", "path", "encoding"}:
        raise ValueError("unknown binding option")


def at_path(value, path: str):
    """Resolve a dotted key or numeric list index.

    Args:
        value: JSON object or contract result.
        path: Dotted path, empty for the entire value.

    Returns:
        Selected value.
    """
    for part in path.split(".") if path else []:
        value = value[int(part)] if isinstance(value, (list, tuple)) else value[part]
    return value


def context_data(request) -> dict:
    """Flatten request parameters while protecting canonical identity fields.

    Args:
        request: Registered oracle request.

    Returns:
        Context available to declarative rules and bindings.
    """
    data = request.model_dump()
    return {**data.get("parameters", {}), **data}


def resolve(binding: dict, context: dict, facts: dict | None = None, runtime: dict | None = None):
    """Resolve a value without evaluating user code.

    Args:
        binding: Validated declarative value binding.
        context: Registered request context.
        facts: Verified evidence indexed by fact type.
        runtime: Gateway-controlled nonce and timestamps.

    Returns:
        Selected and optionally ABI-encoded value.
    """
    validate_binding(binding)
    if "context" in binding:
        path = {"requestId": "dealId", "workflowId": "dealType"}.get(binding["context"], binding["context"])
        value = at_path(context, path)
    elif "fact" in binding:
        evidence = facts[binding["fact"]]
        value = at_path(evidence.model_dump(), binding.get("path", "value"))
    elif "runtime" in binding:
        value = runtime[binding["runtime"]]
    else:
        value = binding["constant"]
    if binding.get("encoding") == "bytes32":
        if not isinstance(value, str):
            raise ValueError("bytes32 binding requires a hexadecimal string")
        value = bytes.fromhex(value.removeprefix("0x"))
        if len(value) != 32:
            raise ValueError("bytes32 binding has invalid length")
    elif binding.get("encoding") == "address":
        from eth_utils import to_checksum_address
        if not is_address(value):
            raise ValueError("invalid address binding")
        value = to_checksum_address(value)
    return value
