import json
import os
import time
from pathlib import Path

import pytest
from web3 import Web3
from web3.exceptions import ContractLogicError

from oracle.chain import ContractClient
from test_universal import profile, universal


@pytest.mark.skipif(not os.getenv("INTEGRATION_RPC_URL"), reason="integration RPC is not configured")
async def test_real_independent_contract_consumers(universal):
    router, _ = universal
    rpc = os.environ["INTEGRATION_RPC_URL"]
    artifacts = Path(os.getenv("INTEGRATION_ARTIFACTS_DIR", "/artifacts"))
    artifact = json.loads((artifacts / "ObservationConsumer.t.sol/ObservationConsumer.json").read_text())
    w3 = Web3(Web3.HTTPProvider(rpc, request_kwargs={"timeout": 5}))
    assert w3.eth.chain_id == 31337
    account = w3.eth.accounts[0]
    router.networks["local"] = {"rpcUrl": rpc, "chainId": 31337}
    router.client_factory = ContractClient
    contracts, results = [], []
    for index in (1, 2):
        factory = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"]["object"])
        tx = factory.constructor(router.signer).transact({"from": account, "gas": 5_000_000})
        receipt = w3.eth.wait_for_transaction_receipt(tx)
        assert receipt.status == 1
        contract = w3.eth.contract(address=receipt.contractAddress, abi=artifact["abi"])
        definition = profile(f"INDEPENDENT_{index}", "sensor", contract.address)
        definition.update(abi=artifact["abi"], signerFunction="oracle")
        router.save_workflow(definition)
        request_id = "0x" + f"{index + 16:02x}" * 32
        router.register_request({"requestId": request_id, "workflowId": definition["workflowId"],
                                 "subjectRef": f"independent-{index}", "parameters": {"minimum": 20}})
        result = await router.evaluate(request_id)
        assert result["status"] == "READY", result
        contracts.append(contract)
        results.append(result["attestation"])
    for index, (contract, signed) in enumerate(zip(contracts, results)):
        values = (bytes.fromhex(signed["requestId"][2:]), signed["value"], signed["issuedAt"], signed["expiresAt"], signed["nonce"])
        signature = bytes.fromhex(signed["signature"][2:])
        timestamp = max(int(time.time()), w3.eth.get_block("latest").timestamp + 1)
        w3.provider.make_request("evm_setNextBlockTimestamp", [timestamp])
        w3.provider.make_request("evm_mine", [])
        with pytest.raises(ContractLogicError):
            contracts[1 - index].functions.accept(values, signature).call({"from": account})
        tx = contract.functions.accept(values, signature).transact({"from": account, "gas": 500_000})
        assert w3.eth.wait_for_transaction_receipt(tx).status == 1
        assert contract.functions.readings(values[0]).call() == 42
        with pytest.raises(ContractLogicError):
            contract.functions.accept(values, signature).call({"from": account})
