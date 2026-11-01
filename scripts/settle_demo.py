import json
import os
import time
from pathlib import Path

from eth_account import Account
from web3 import Web3
from web3.exceptions import ContractLogicError

from scripts.deploy import transact
from oracle.models import FactoringStatus


def settle():
    """Pay the full claim once and distribute the agreed fixed commission."""
    if os.getenv("SEED_DEMO", "1") != "1":
        raise RuntimeError("factoring demo is disabled")
    root = Path(os.getenv("RUNTIME_DIR", "/runtime"))
    artifacts = Path(os.getenv("ARTIFACTS_DIR", "/artifacts"))
    deployment = json.loads((root / "deployment.json").read_text())
    w3 = Web3(Web3.HTTPProvider(os.environ["RPC_URL"], request_kwargs={"timeout": 5}))
    if w3.eth.chain_id != 31337:
        raise RuntimeError("settlement demo requires local chain 31337")
    contract = w3.eth.contract(address=deployment["contract"],
        abi=json.loads((artifacts / "Factoring.sol/Factoring.json").read_text())["abi"])
    token = w3.eth.contract(address=deployment["token"],
        abi=json.loads((artifacts / "MockRuble.sol/MockRuble.json").read_text())["abi"])
    request_id = bytes.fromhex(deployment["dealId"][2:])
    deal = contract.functions.deals(request_id).call()
    if deal[8] == FactoringStatus.SETTLED:
        print("Расчёты уже завершены. Повторного платежа нет.")
        return
    if deal[8] != FactoringStatus.FINANCED:
        raise RuntimeError("run the financing demo before settlement")
    buyer = Account.from_key(Path(os.getenv("BUYER_KEY_FILE", str(root / "buyer.key"))).read_text().strip())
    if buyer.address.lower() != deal[1].lower():
        raise RuntimeError("buyer key does not match the registered debtor")
    timestamp = max(int(time.time()), w3.eth.get_block("latest").timestamp + 1)
    for method, params in (("evm_setNextBlockTimestamp", [timestamp]), ("evm_mine", [])):
        if "error" in w3.provider.make_request(method, params):
            raise RuntimeError("cannot synchronize the local demo clock")
    before = {party: token.functions.balanceOf(party).call() for party in deal[:3]}
    transact(w3, token.functions.approve(contract.address, deal[5]), buyer)
    transact(w3, contract.functions.repay(request_id), buyer)
    factor_payment = deal[4] + deal[9]
    remainder = deal[5] - factor_payment
    assert before[deal[1]] - token.functions.balanceOf(deal[1]).call() == deal[5]
    assert token.functions.balanceOf(deal[2]).call() - before[deal[2]] == factor_payment
    assert token.functions.balanceOf(deal[0]).call() - before[deal[0]] == remainder
    assert contract.functions.deals(request_id).call()[8] == FactoringStatus.SETTLED
    try:
        contract.functions.repay(request_id).call({"from": buyer.address})
    except ContractLogicError:
        pass
    else:
        raise AssertionError("repeated settlement accepted")
    print("Расчёты завершены: покупатель оплатил 10 000 000 ₽; фактор получил 9 200 000 ₽, включая комиссию 200 000 ₽; остаток поставщику — 800 000 ₽. Повторный платёж отклонён.")


if __name__ == "__main__":
    settle()
