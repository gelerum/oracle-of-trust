import time
from types import SimpleNamespace

from eth_account import Account
import pytest

from oracle.crypto import FIELDS, typed_message
from oracle.execution import financing_execution
from oracle.models import FactoringStatus


@pytest.fixture
def execution_case(deal, keys):
    now = int(time.time())
    address = Account.create().address
    payload = {"dealId": deal.dealId, "documentHash": deal.documentHash,
               "deliveryAccepted": True, "claimAssigned": True, "buyerNotified": True,
               "financingApproved": True, "issuedAt": now - 5, "expiresAt": now + 120, "nonce": 1}
    signature = Account.sign_message(typed_message(payload, 31337, address), keys["oracle"]).signature
    args = {"a": {name: bytes.fromhex(payload[name][2:]) if kind == "bytes32" else payload[name] for name, kind in FIELDS}, "signature": signature}
    tx_hash, block_hash = b"\x11" * 32, b"\x22" * 32
    event = {"transactionHash": tx_hash, "blockHash": block_hash, "args": {
        "dealId": bytes.fromhex(deal.dealId[2:]), "amount": deal.financingAmount, "supplier": deal.supplier}}
    receipt = {"transactionHash": tx_hash, "blockHash": block_hash, "blockNumber": 17, "status": 1}
    block = {"hash": block_hash, "timestamp": now}
    tx = {"blockHash": block_hash, "to": address, "input": b"encoded"}
    oracle = Account.from_key(keys["oracle"]).address
    contract = SimpleNamespace(address=address, events=SimpleNamespace(FinancingExecuted=lambda: SimpleNamespace(get_logs=lambda **_: [event])),
        decode_function_input=lambda _: (SimpleNamespace(fn_name="execute"), args),
        functions=SimpleNamespace(oracle=lambda: SimpleNamespace(call=lambda: oracle)))
    eth = SimpleNamespace(chain_id=31337, get_transaction_receipt=lambda _: receipt,
        get_block=lambda _: block, get_transaction=lambda _: tx)
    chain = SimpleNamespace(contract=contract, web3=SimpleNamespace(eth=eth))
    values = [deal.supplier, deal.buyer, deal.factor, "token", deal.financingAmount, deal.claimAmount,
              deal.dueDate, bytes.fromhex(deal.documentHash[2:]), FactoringStatus.FINANCED, deal.commissionAmount]
    return chain, values, event, receipt, block, tx, args


def test_actual_receipt_and_signed_execute_are_required(execution_case, deal):
    chain, values, *_ = execution_case
    result = financing_execution(chain, deal.dealId, values)
    assert result["status"] == "CONFIRMED"
    assert result["signatureStatus"] == "VERIFIED_BY_CONTRACT"
    assert result["transactionHash"] == "0x" + "11" * 32
    assert result["amount"] == deal.financingAmount
    assert result["attestation"]["dealId"] == deal.dealId


@pytest.mark.parametrize("failure", ["reverted", "reorg", "wrong_deal", "wrong_amount", "wrong_supplier", "wrong_contract", "wrong_signature", "wrong_payload", "expired_at_execution"])
def test_unverified_execution_never_claims_signature_checked(execution_case, deal, failure):
    chain, values, event, receipt, block, tx, args = execution_case
    if failure == "reverted": receipt["status"] = 0
    if failure == "reorg": block["hash"] = b"\x33" * 32
    if failure == "wrong_deal": event["args"]["dealId"] = b"\x44" * 32
    if failure == "wrong_amount": event["args"]["amount"] += 1
    if failure == "wrong_supplier": event["args"]["supplier"] = Account.create().address
    if failure == "wrong_contract": tx["to"] = Account.create().address
    if failure == "wrong_signature": args["signature"] = b"\x00" * 65
    if failure == "wrong_payload": args["a"]["dealId"] = b"\x44" * 32
    if failure == "expired_at_execution": block["timestamp"] = args["a"]["expiresAt"]
    result = financing_execution(chain, deal.dealId, values)
    assert result["status"] == "UNVERIFIED"
    assert result["signatureStatus"] == "NOT_CHECKED"
    assert result["transactionHash"] is None


def test_reserved_is_not_paid_and_does_not_query_receipts(execution_case, deal):
    chain, values, *_ = execution_case
    values[8] = FactoringStatus.RESERVED
    chain.contract.events = None
    result = financing_execution(chain, deal.dealId, values)
    assert result["status"] == "NOT_EXECUTED"
    assert result["signatureStatus"] == "NOT_CHECKED"


def test_success_remains_historical_after_attestation_expiry(execution_case, deal, monkeypatch):
    chain, values, *_ = execution_case
    monkeypatch.setattr(time, "time", lambda: 99999999999)
    assert financing_execution(chain, deal.dealId, values)["status"] == "CONFIRMED"
