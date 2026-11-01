import json
from pathlib import Path

from web3 import Web3

from oracle.models import Deal, FactoringDeal, FactoringStatus


class FactoringChain:
    """Read immutable deal state from the configured contract.

    Args:
        rpc_url: Ethereum JSON-RPC endpoint.
        deployment: Contract address and chain identifier.
        artifacts: Compiled Foundry artifact directory.
    """

    def __init__(self, rpc_url: str, deployment: dict, artifacts: str = "/artifacts"):
        self.web3 = Web3(Web3.HTTPProvider(rpc_url, request_kwargs={"timeout": 5}))
        self.chain_id = deployment["chainId"]
        self.address = deployment["contract"]
        artifact = json.loads((Path(artifacts) / "Factoring.sol/Factoring.json").read_text())
        self.contract = self.web3.eth.contract(address=self.address, abi=artifact["abi"])
        self.oracle_address = self.contract.functions.oracle().call()
        if self.web3.eth.chain_id != self.chain_id:
            raise ValueError("chain mismatch")

    def validate_deal(self, deal: Deal):
        """Compare backend metadata with the immutable funded on-chain deal.

        Args:
            deal: Local metadata to verify.

        Raises:
            ValueError: Metadata differs or funding is unavailable.
        """
        deal = FactoringDeal.model_validate(deal.model_dump())
        d = self.contract.functions.deals(bytes.fromhex(deal.dealId[2:])).call()
        expected = [deal.supplier, deal.buyer, deal.factor]
        if [x.lower() for x in d[:3]] != [x.lower() for x in expected]:
            raise ValueError("on-chain parties mismatch")
        if list(d[4:7]) != [deal.financingAmount, deal.claimAmount, deal.dueDate]:
            raise ValueError("on-chain amounts or date mismatch")
        if d[9] != deal.commissionAmount:
            raise ValueError("on-chain commission mismatch")
        if "0x" + d[7].hex() != deal.documentHash or d[8] != FactoringStatus.RESERVED:
            raise ValueError("deal is not reserved or document-bound")


class ContractClient:
    """Validate any registered consumer using ABI view calls and assertions.

    Args:
        network: Trusted RPC endpoint and chain identifier.
        workflow: Validated consumer definition.
        signer: Gateway public signer address.
    """

    def __init__(self, network: dict, workflow: dict, signer: str):
        self.workflow = workflow
        self.web3 = Web3(Web3.HTTPProvider(network["rpcUrl"], request_kwargs={"timeout": 5}))
        self.chain_id = network["chainId"]
        self.address = Web3.to_checksum_address(workflow["verifyingContract"])
        self.oracle_address = signer
        self.contract = self.web3.eth.contract(address=self.address, abi=workflow["abi"])
        if self.web3.eth.chain_id != self.chain_id or not self.web3.eth.get_code(self.address):
            raise ValueError("consumer network mismatch or contract has no code")
        if workflow.get("signerFunction"):
            actual = getattr(self.contract.functions, workflow["signerFunction"])().call()
            if not isinstance(actual, str) or actual.lower() != signer.lower():
                raise ValueError("consumer trusts a different oracle signer")

    def validate_deal(self, deal: Deal):
        """Apply profile schema and on-chain state assertions.

        Args:
            deal: Registered oracle request.

        Raises:
            ValueError: Context, network or contract assertions do not match.
        """
        from jsonschema import Draft202012Validator
        from oracle.configuration import at_path, context_data, resolve

        if not self.workflow["enabled"] or deal.dealType != self.workflow["workflowId"]:
            raise ValueError("consumer profile disabled or mismatched")
        context = context_data(deal)
        if list(Draft202012Validator(self.workflow["contextSchema"]).iter_errors(context)):
            raise ValueError("request does not match consumer context schema")
        if self.web3.eth.chain_id != self.chain_id:
            raise ValueError("chain changed")
        for check in self.workflow["checks"]:
            args = [resolve(binding, context) for binding in check.get("arguments", [])]
            response = getattr(self.contract.functions, check["function"])(*args).call()
            for assertion in check["assertions"]:
                actual = at_path(response, assertion.get("path", ""))
                expected = resolve({"context": assertion["context"]}, context) if "context" in assertion else assertion["equals"]
                encoding = assertion.get("encoding")
                if encoding == "address":
                    matches = isinstance(actual, str) and actual.lower() == expected.lower()
                elif encoding == "bytes32":
                    matches = bytes(actual) == bytes.fromhex(expected.removeprefix("0x"))
                else:
                    matches = type(actual) is type(expected) and actual == expected
                if not matches:
                    raise ValueError(f"contract assertion failed: {check['function']}:{assertion.get('path', '')}")
