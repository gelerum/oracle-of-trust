from eth_account import Account
from web3 import Web3

from oracle.crypto import FIELDS, typed_message
from oracle.models import FactoringStatus


def financing_execution(chain, deal_id: str, values: list) -> dict:
    """Verify the actual financing transaction independently of oracle readiness.

    Args:
        chain: Factoring contract client and trusted RPC connection.
        deal_id: Requested on-chain deal identifier.
        values: Current contract deal tuple.

    Returns:
        Confirmed execution proof or an explicit unverified/not-executed result.
    """
    result = {"status": "NOT_EXECUTED", "signatureStatus": "NOT_CHECKED",
              "transactionHash": None, "blockNumber": None}
    if values[8] not in (FactoringStatus.FINANCED, FactoringStatus.SETTLED):
        return result
    result["status"] = "UNVERIFIED"
    try:
        contract, eth = chain.contract, chain.web3.eth
        events = contract.events.FinancingExecuted().get_logs(
            from_block=0, to_block="latest", argument_filters={"dealId": bytes.fromhex(deal_id[2:])})
        if len(events) != 1:
            raise ValueError("expected one financing event for this deal")
        event = events[0]
        receipt = eth.get_transaction_receipt(event["transactionHash"])
        block = eth.get_block(receipt["blockNumber"])
        tx = eth.get_transaction(event["transactionHash"])
        if (receipt["status"] != 1 or event.get("removed", False)
                or receipt["transactionHash"] != event["transactionHash"]
                or event["args"]["dealId"] != bytes.fromhex(deal_id[2:])
                or receipt["blockHash"] != block["hash"] or event["blockHash"] != block["hash"]
                or tx["blockHash"] != block["hash"] or tx["to"].lower() != contract.address.lower()
                or event["args"]["amount"] != values[4]
                or event["args"]["supplier"].lower() != values[0].lower()):
            raise ValueError("financing receipt does not match the deal")
        function, args = contract.decode_function_input(tx["input"])
        if function.fn_name != "execute":
            raise ValueError("financing transaction does not call execute")
        payload = {name: Web3.to_hex(args["a"][name]) if kind == "bytes32" else args["a"][name]
                   for name, kind in FIELDS}
        if (payload["dealId"].lower() != deal_id.lower()
                or payload["documentHash"].lower() != Web3.to_hex(values[7]).lower()
                or not all(payload[name] is True for name in (
                    "deliveryAccepted", "claimAssigned", "buyerNotified", "financingApproved"))
                or not payload["issuedAt"] <= block["timestamp"] < payload["expiresAt"]):
            raise ValueError("executed attestation does not match the deal")
        signer = Account.recover_message(typed_message(payload, eth.chain_id, contract.address), signature=args["signature"])
        trusted = contract.functions.oracle().call()
        if signer.lower() != trusted.lower():
            raise ValueError("executed attestation signer is not trusted")
        result.update(status="CONFIRMED", signatureStatus="VERIFIED_BY_CONTRACT",
                      transactionHash=Web3.to_hex(event["transactionHash"]), blockNumber=receipt["blockNumber"],
                      amount=event["args"]["amount"], supplier=event["args"]["supplier"],
                      attestation=payload, signer=signer)
    except Exception as exc:
        result["error"] = f"financing proof unavailable ({type(exc).__name__})"
    return result
