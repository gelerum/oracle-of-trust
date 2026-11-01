import json
import re

from eth_account import Account
from eth_account.messages import encode_defunct, encode_typed_data
from eth_keys.exceptions import BadSignature
from eth_utils.exceptions import ValidationError as SignatureValidationError

FIELDS = [("dealId", "bytes32"), ("documentHash", "bytes32"),
          ("deliveryAccepted", "bool"), ("claimAssigned", "bool"),
          ("buyerNotified", "bool"), ("financingApproved", "bool"),
          ("issuedAt", "uint256"), ("expiresAt", "uint256"), ("nonce", "uint256")]


def canonical(data: dict) -> str:
    """Serialize source data deterministically.

    Args:
        data: Signed source fields.

    Returns:
        Canonical JSON representation.
    """
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def sign_source(data: dict, key: str) -> str:
    """Sign mock evidence using a dedicated source key.

    Args:
        data: Canonical evidence fields.
        key: Source private key.

    Returns:
        Hexadecimal test signature.
    """
    return "0x" + Account.sign_message(encode_defunct(text=canonical(data)), key).signature.hex()


def recover_source(data: dict, signature: str) -> str:
    """Recover the source signer.

    Args:
        data: Signed evidence fields.
        signature: EIP-191 test signature.

    Returns:
        Recovered Ethereum address.
    """
    if re.fullmatch(r"0x[0-9a-fA-F]{130}", signature) is None:
        raise ValueError("invalid source signature encoding")
    try:
        return Account.recover_message(encode_defunct(text=canonical(data)), signature=signature)
    except (BadSignature, SignatureValidationError, ValueError) as exc:
        raise ValueError("invalid source signature") from exc


def typed_message(payload: dict, chain_id: int, contract: str):
    """Encode the contract attestation as EIP-712.

    Args:
        payload: Attestation fields excluding signature.
        chain_id: Target chain identifier.
        contract: Verifying contract address.

    Returns:
        Signable EIP-712 message.
    """
    return encode_typed_data(
        domain_data={"name": "VTB Oracle of Trust", "version": "2",
                     "chainId": chain_id, "verifyingContract": contract},
        message_types={"Attestation": [{"name": n, "type": t} for n, t in FIELDS]},
        message_data=payload,
    )
