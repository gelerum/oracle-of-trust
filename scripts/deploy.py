import json
import os
from pathlib import Path

from eth_account import Account
from web3 import Web3
from psycopg import sql
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from oracle.models import FactoringDeal as Deal, FactoringStatus
from oracle.store import Store
from oracle.migrations import migrate_demo_claim

CONTRACT_VERSION = 5
TOKEN_NAME = "Цифровой рубль (тестовый)"


def transact(web3, function, account):
    """Sign and confirm a local Ethereum transaction.

    Args:
        web3: Ethereum client.
        function: Contract transaction builder.
        account: Acting participant account.

    Returns:
        Successful receipt.
    """
    tx = function.build_transaction({"from": account.address,
                                     "nonce": web3.eth.get_transaction_count(account.address),
                                     "chainId": web3.eth.chain_id, "gas": 5_000_000})
    signed = account.sign_transaction(tx)
    receipt = web3.eth.wait_for_transaction_receipt(web3.eth.send_raw_transaction(signed.raw_transaction))
    if receipt.status != 1:
        raise RuntimeError("transaction reverted")
    return receipt


def remove_old_demo(store: Store, prior: dict | None):
    """Remove superseded demo requests while preserving source configuration.

    Args:
        store: PostgreSQL persistence.
        prior: Previous local deployment metadata.
    """
    with store.transaction("demo-upgrade") as db:
        rows = db.execute("""SELECT id,data FROM deals WHERE data->>'subjectRef' LIKE 'test-supply-742%'
                             AND data->>'dealType' LIKE 'FACTORING%'""").fetchall()
        ids = {row["id"] for row in rows}
        workflows = {row["data"]["dealType"] for row in rows}
        if prior:
            ids.add(prior["dealId"])
            workflows.add(prior.get("workflowId", "FACTORING"))
        for request_id in ids:
            for table in ("audit", "evaluations", "attestations"):
                db.execute(sql.SQL("DELETE FROM {} WHERE deal_id=%s").format(sql.Identifier(table)), (request_id,))
            db.execute("DELETE FROM deals WHERE id=%s", (request_id,))
        for workflow in workflows:
            in_use = db.execute("SELECT 1 FROM deals WHERE data->>'dealType'=%s LIMIT 1", (workflow,)).fetchone()
            if not in_use:
                removed = db.execute("DELETE FROM workflow_configs WHERE id=%s RETURNING data", (workflow,)).fetchone()
                process_id = removed["data"].get("processId") if removed else None
                if process_id == workflow:
                    shared = db.execute("SELECT 1 FROM workflow_configs WHERE data->>'processId'=%s LIMIT 1", (process_id,)).fetchone()
                    if not shared:
                        db.execute("DELETE FROM process_configs WHERE id=%s", (process_id,))


def deploy():
    """Deploy the factoring demo with a fixed commission and funded buyer."""
    store = Store(os.environ["DATABASE_URL"])
    store.initialize()
    if os.getenv("SEED_DEMO", "1") != "1":
        return
    root = Path(os.getenv("RUNTIME_DIR", "/runtime"))
    w3 = Web3(Web3.HTTPProvider(os.environ["RPC_URL"], request_kwargs={"timeout": 5}))
    if w3.eth.chain_id != 31337:
        raise RuntimeError("demo deployment requires local chain 31337")
    central_bank = Account.from_key(Path(os.getenv("CENTRAL_BANK_KEY_FILE", str(root / "central-bank.key"))).read_text().strip())
    factor = Account.from_key(Path(os.getenv("FACTOR_KEY_FILE", str(root / "factor.key"))).read_text().strip())
    buyer_account = Account.from_key(Path(os.getenv("BUYER_KEY_FILE", str(root / "buyer.key"))).read_text().strip())
    if central_bank.address in (factor.address, buyer_account.address):
        raise RuntimeError("central bank, factor and buyer must use distinct keys")
    for actor in (central_bank, factor, buyer_account):
        response = w3.provider.make_request("anvil_setBalance", [actor.address, hex(100 * 10**18)])
        if "error" in response:
            raise RuntimeError("Anvil participant funding failed")
    oracle = json.loads((root / "oracle-signer.json").read_text())["address"]
    artifacts = Path(os.getenv("ARTIFACTS_DIR", "/artifacts"))
    deployment_path = root / "deployment.json"
    prior = json.loads(deployment_path.read_text()) if deployment_path.exists() else None
    token_artifact = json.loads((artifacts / "MockRuble.sol/MockRuble.json").read_text())
    factoring_artifact = json.loads((artifacts / "Factoring.sol/Factoring.json").read_text())
    if prior and w3.eth.get_code(prior["contract"]) and prior.get("contractVersion") == CONTRACT_VERSION:
        contract = w3.eth.contract(address=prior["contract"], abi=factoring_artifact["abi"])
        try:
            token = w3.eth.contract(address=prior["token"], abi=token_artifact["abi"])
            compatible = (contract.functions.CONTRACT_VERSION().call() == CONTRACT_VERSION
                          and token.functions.minter().call().lower() == central_bank.address.lower()
                          and token.functions.name().call() == TOKEN_NAME)
        except (BadFunctionCallOutput, ContractLogicError):
            compatible = False
        if compatible:
            document_path = root / "mock-document.json"
            document = json.loads(document_path.read_text())
            reference = document["facts"]["CLAIM_ASSIGNED"]["claimId"]
            document["deal"].setdefault("claimId", reference)
            Deal.model_validate(document["deal"])
            store.put_deal(document["deal"])
            migrate_demo_claim(store, prior["dealId"], reference)
            document_path.write_text(json.dumps(document))
            return
    contracts = {}
    for name, args in (("MockRuble", []), ("Factoring", [oracle])):
        artifact = json.loads((artifacts / f"{name}.sol/{name}.json").read_text())
        factory = w3.eth.contract(abi=artifact["abi"], bytecode=artifact["bytecode"]["object"])
        deployer = central_bank if name == "MockRuble" else factor
        receipt = transact(w3, factory.constructor(*args), deployer)
        contracts[name] = w3.eth.contract(address=receipt.contractAddress, abi=artifact["abi"])
    token, factoring = contracts["MockRuble"], contracts["Factoring"]
    supplier, buyer = w3.eth.accounts[1], buyer_account.address
    workflow_id = f"FACTORING_V{CONTRACT_VERSION}_" + factoring.address[-8:]
    instance = receipt.blockHash.hex()
    subject = f"test-supply-742-v{CONTRACT_VERSION}-" + instance[-8:]
    document = {"id": "test-upd-742", "supplier": supplier, "buyer": buyer,
                "claimAmount": 10_000_000 * 10**6, "subjectRef": subject}
    document_hash = "0x" + Web3.keccak(text=json.dumps(document, sort_keys=True)).hex()
    deal_id = "0x" + Web3.keccak(text=subject + factoring.address).hex()
    due = w3.eth.get_block("latest").timestamp + 60 * 86400
    deal = Deal(dealType=workflow_id, dealId=deal_id, documentHash=document_hash, subjectRef=subject, claimId="claim-" + deal_id[-12:],
                supplier=supplier, buyer=buyer, factor=factor.address,
                claimAmount=10_000_000 * 10**6, financingAmount=9_000_000 * 10**6,
                commissionAmount=200_000 * 10**6, dueDate=due)
    values = (supplier, buyer, factor.address, token.address, deal.financingAmount,
              deal.claimAmount, due, bytes.fromhex(document_hash[2:]), int(FactoringStatus.ACTIVE), deal.commissionAmount)
    transact(w3, factoring.functions.createDeal(bytes.fromhex(deal_id[2:]), values), factor)
    transact(w3, token.functions.mint(factor.address, deal.financingAmount), central_bank)
    transact(w3, token.functions.mint(buyer, deal.claimAmount), central_bank)
    transact(w3, token.functions.approve(factoring.address, deal.financingAmount), factor)
    transact(w3, factoring.functions.reserve(bytes.fromhex(deal_id[2:])), factor)
    facts = {
        "DELIVERY_ACCEPTED": {"supplier": supplier, "buyer": buyer, "claimAmount": deal.claimAmount,
                              "buyerAuthorized": True, "acceptance": "signed"},
        "BUYER_NOTIFIED": {"buyer": buyer, "factor": factor.address, "notificationStatus": "received"},
        "CLAIM_ASSIGNED": {"supplier": supplier, "buyer": buyer, "factor": factor.address,
                           "claimAmount": deal.claimAmount, "claimId": deal.claimId},
        "FINANCING_APPROVED": {"factor": factor.address, "financingAmount": deal.financingAmount,
                               "commissionAmount": deal.commissionAmount, "dueDate": due,
                               "conditions": "unconditional"},
    }
    # Old demo rows are discarded; unrelated sources, consumers and requests survive.
    remove_old_demo(store, prior)
    store.put_deal(deal.model_dump())
    (root / "mock-document.json").write_text(json.dumps({"deal": deal.model_dump(), "document": document, "facts": facts}))
    deployment_path.write_text(json.dumps({"chainId": 31337, "contract": factoring.address,
        "token": token.address, "dealId": deal_id, "workflowId": workflow_id,
        "contractVersion": CONTRACT_VERSION, "tokenDecimals": 6, "tokenName": TOKEN_NAME, "issuer": central_bank.address}))
    print(json.dumps({"dealId": deal_id, "status": "RESERVED", "commission": 200_000}))


if __name__ == "__main__":
    deploy()
