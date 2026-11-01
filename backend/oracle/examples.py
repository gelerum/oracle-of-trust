import os

from oracle.adapters import AlternateEdoAdapter, MockEdoAdapter, MockVtbFactoringAdapter
from oracle.models import FactoringDeal, FactoringStatus
from oracle.registry import AdapterFactories


def demo_factories() -> AdapterFactories:
    """Install demo transports through the same public adapter registration API.

    Returns:
        Trusted factories including the generic HTTP and file transports.
    """
    factories = AdapterFactories()
    edo = AlternateEdoAdapter if os.getenv("EDO_ADAPTER") == "alternate" else MockEdoAdapter
    factories.register("edo", lambda config: edo(config["baseUrl"]))
    factories.register("edo-alternate", lambda config: AlternateEdoAdapter(config["baseUrl"]))
    factories.register("vtb", lambda config: MockVtbFactoringAdapter(config["baseUrl"]))
    return factories


def factoring_workflow(deployment: dict, artifact: dict, requirements: dict, profile: dict) -> dict:
    """Describe factoring as an ordinary configurable consumer profile.

    Args:
        deployment: Local demo contract addresses.
        artifact: Compiled factoring ABI.
        requirements: Factoring fact requirements.
        profile: Original typed attestation schema.

    Returns:
        Generic workflow definition without executable deal logic.
    """
    profile = dict(profile)
    profile["bindings"] = {"dealId": {"context": "dealId"}, "documentHash": {"context": "documentHash"},
                           **{name: {"runtime": name} for name in ("issuedAt", "expiresAt", "nonce")},
                           **{name: {"fact": fact} for name, fact in profile.pop("facts").items()}}
    assertions = [{"path": str(i), "context": name, "encoding": "address"}
                  for i, name in enumerate(("supplier", "buyer", "factor"))]
    assertions += [{"path": "3", "equals": deployment["token"], "encoding": "address"}]
    assertions += [{"path": str(i), "context": name} for i, name in enumerate(
        ("financingAmount", "claimAmount", "dueDate"), start=4)]
    assertions += [{"path": "9", "context": "commissionAmount"},
                   {"path": "7", "context": "documentHash", "encoding": "bytes32"},
                   {"path": "8", "equals": int(FactoringStatus.RESERVED)}]
    return {"workflowId": deployment.get("workflowId", "FACTORING"), "name": "Факторинг · расчёты и комиссия" if deployment.get("contractVersion", 0) >= 3 else "Факторинг · демонстрация", "network": "local",
            "verifyingContract": deployment["contract"], "abi": artifact["abi"],
            "signerFunction": "oracle", "requirements": requirements, "attestation": profile,
            "contextSchema": FactoringDeal.model_json_schema(),
            "checks": [{"function": "deals", "arguments": [{"context": "dealId", "encoding": "bytes32"}],
                        "assertions": assertions}]}
