from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_abi.exceptions import EncodingError
from eth_utils.exceptions import ValidationError as EncodingValidationError

from oracle.configuration import context_data, resolve


class AttestationService:
    """Sign contract attestations using configured payload profiles.

    Args:
        key: Gateway signing key.
        chain_id: Target Ethereum chain identifier.
        address: Verifying contract address.
        profiles: Deal type to EIP-712 field and fact mappings.
    """

    def __init__(self, key: str, chain_id: int, address: str, profiles: dict):
        self.key, self.chain_id, self.address, self.profiles = key, chain_id, address, profiles

    def issue(self, deal, facts: dict, nonce: int, now: int) -> tuple[dict, str]:
        """Build a profile-specific payload from verified facts and sign it.

        Args:
            deal: Validated immutable deal context.
            facts: Evidence accepted by the rules engine.
            nonce: Persistently allocated single-use nonce.
            now: Issuance timestamp.

        Returns:
            Payload and its EIP-712 signature.

        Raises:
            ValueError: Evidence is expired or the profile is incomplete.
        """
        profile = self.profiles[deal.dealType]
        expires = min(now + profile["ttl"], *(e.expiresAt for e in facts.values()))
        if expires <= now:
            raise ValueError("evidence expired during collection")
        runtime = {"issuedAt": now, "expiresAt": expires, "nonce": nonce}
        if "bindings" in profile:
            payload = {field: resolve(binding, context_data(deal), facts, runtime)
                       for field, binding in profile["bindings"].items()}
        else:
            payload = {"dealId": deal.dealId, "documentHash": deal.documentHash, **runtime}
            for field, fact in profile["facts"].items():
                if facts[fact].value is not True:
                    raise ValueError("attestation requires affirmative verified evidence")
                payload[field] = facts[fact].value
        def json_value(value):
            if isinstance(value, bytes):
                return "0x" + value.hex()
            if isinstance(value, dict):
                return {key: json_value(item) for key, item in value.items()}
            if isinstance(value, list):
                return [json_value(item) for item in value]
            return value
        payload = json_value(payload)
        try:
            message = encode_typed_data(
                domain_data={"name": profile["name"], "version": profile["version"],
                             "chainId": self.chain_id, "verifyingContract": self.address},
                message_types={**profile.get("types", {}), profile["typeName"]: profile["fields"]}, message_data=payload,
            )
            signature = "0x" + Account.sign_message(message, self.key).signature.hex()
        except (EncodingError, EncodingValidationError, TypeError, ValueError) as exc:
            raise ValueError("payload does not match the configured EIP-712 schema") from exc
        return payload, signature


    def expiry(self, profile_id: str, payload: dict) -> int:
        """Read the signed expiry without assuming its contract field name.

        Args:
            profile_id: Selected consumer profile.
            payload: Signed attestation fields.

        Returns:
            Unix validity deadline.
        """
        profile = self.profiles[profile_id]
        field = next((name for name, binding in profile.get("bindings", {}).items()
                      if binding == {"runtime": "expiresAt"}), "expiresAt")
        return payload[field]
