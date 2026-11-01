import json
import os
import time
from pathlib import Path

import httpx
from eth_account import Account
from web3 import Web3

from oracle.crypto import FIELDS
from scripts.deploy import transact
from oracle.models import FactoringStatus


def demo():
    """Evaluate evidence, execute financing and verify replay is rejected."""
    if os.getenv("SEED_DEMO", "1") != "1":
        raise RuntimeError("factoring demo is disabled")
    root = Path(os.getenv("RUNTIME_DIR", "/runtime"))
    artifacts = Path(os.getenv("ARTIFACTS_DIR", "/artifacts"))
    deployment = json.loads((root / "deployment.json").read_text())
    document = json.loads((root / "mock-document.json").read_text())
    w3 = Web3(Web3.HTTPProvider(os.environ["RPC_URL"], request_kwargs={"timeout": 5}))
    if w3.eth.chain_id != 31337:
        raise RuntimeError("demo execution requires local chain 31337")
    # Restored Anvil snapshots may have a clock behind source observation time.
    timestamp = max(int(time.time()), w3.eth.get_block("latest").timestamp + 1)
    for method, params in (("evm_setNextBlockTimestamp", [timestamp]), ("evm_mine", [])):
        response = w3.provider.make_request(method, params)
        if "error" in response:
            raise RuntimeError("cannot synchronize the local demo clock")
    artifact = json.loads((artifacts / "Factoring.sol/Factoring.json").read_text())
    contract = w3.eth.contract(address=deployment["contract"], abi=artifact["abi"])
    token_abi = json.loads((artifacts / "MockRuble.sol/MockRuble.json").read_text())["abi"]
    token = w3.eth.contract(address=deployment["token"], abi=token_abi)
    deal_id = deployment["dealId"]
    chain_deal = contract.functions.deals(bytes.fromhex(deal_id[2:])).call()
    if chain_deal[8] == FactoringStatus.SETTLED:
        assert token.functions.balanceOf(document["deal"]["supplier"]).call() == chain_deal[5] - chain_deal[9]
        print("Расчёты завершены. Повторных платежей нет.")
        return
    if chain_deal[8] == FactoringStatus.FINANCED:
        assert token.functions.balanceOf(document["deal"]["supplier"]).call() == 9_000_000 * 10**6
        print("Аванс 9 000 000 ₽ уже выплачен. Повторного перевода нет.")
        return
    base = os.getenv("GATEWAY_URL", "http://gateway:8000")
    response = httpx.post(f"{base}/deals/{deal_id}/evaluate", timeout=30)
    response.raise_for_status()
    result = response.json()
    if result["status"] != "READY":
        raise RuntimeError(result)
    attestation = result["attestation"]
    values = tuple(bytes.fromhex(attestation[n][2:]) if t == "bytes32" else attestation[n] for n, t in FIELDS)
    signature = bytes.fromhex(attestation["signature"][2:])
    factor = Account.from_key(Path(os.getenv("FACTOR_KEY_FILE", str(root / "factor.key"))).read_text().strip())
    supplier = document["deal"]["supplier"]
    before = token.functions.balanceOf(supplier).call()
    transact(w3, contract.functions.execute(values, signature), factor)
    assert token.functions.balanceOf(supplier).call() - before == 9_000_000 * 10**6
    assert contract.functions.deals(bytes.fromhex(deal_id[2:])).call()[8] == FactoringStatus.FINANCED
    try:
        contract.functions.execute(values, signature).call({"from": factor.address})
    except Exception:
        pass
    else:
        raise AssertionError("replay accepted")
    audit = httpx.get(f"{base}/deals/{deal_id}/evidence", timeout=5).json()
    assert len([entry for entry in audit if entry["accepted"]]) >= 4
    print("Аванс выплачен: 9 000 000 ₽. Повторное исполнение отклонено. В аудите — четыре проверенных подтверждения.")


if __name__ == "__main__":
    demo()
