import asyncio
import json
import time

from eth_account import Account
import httpx
from requests.exceptions import RequestException
from web3.exceptions import Web3Exception

from oracle.attestations import AttestationService
from oracle.registry import load_json
from oracle.models import Deal
from oracle.validation import EvidenceValidator, MockAccessPolicy
from oracle.responses import response_details
from oracle.rules import RulesEngine


class Gateway:
    """Collect evidence and issue auditable contract-bound attestations.

    Args:
        registry: Registered adapters and source authorities.
        rules: Declarative rules engine.
        store: PostgreSQL persistence.
        chain: Read-only contract client.
        key: Gateway private key, separate from source keys.
        validator: Optional document and evidence validator.
        policy: Optional access and consent policy implementation.
        profiles: Consumer-specific EIP-712 schemas and payload bindings.
    """

    def __init__(self, registry, rules, store, chain, key, validator=None, policy=None, profiles=None):
        self.registry, self.rules, self.store, self.chain = registry, rules, store, chain
        self.key = key
        self.attestor = AttestationService(key, chain.chain_id, chain.address, (profiles if profiles is not None else load_json("config/attestations.json")))
        self.validator = validator or EvidenceValidator()
        self.policy = policy or MockAccessPolicy()
        signer = Account.from_key(key).address.lower()
        if any(s["signer"].lower() == signer for s in registry.sources.values()):
            raise ValueError("gateway and source keys must differ")
        if chain.oracle_address.lower() != signer:
            raise ValueError("gateway signer differs from contract oracle")

    async def evaluate(self, deal_id: str) -> dict:
        """Evaluate a registered deal under a cross-process database lock.

        Args:
            deal_id: Immutable deal identifier.

        Returns:
            Decision, reasons and optionally a signed attestation.
        """
        return await asyncio.to_thread(lambda: asyncio.run(self._evaluate(deal_id)))

    async def _evaluate(self, deal_id: str) -> dict:
        """Evaluate in a worker thread so database locks cannot block the API loop.

        Args:
            deal_id: Requested deal identifier.

        Returns:
            Persisted decision and optional signed attestation.
        """
        with self.store.transaction(deal_id) as db:
            def finish(result):
                db.execute("INSERT INTO evaluations(deal_id,result) VALUES (%s,%s::jsonb)",
                           (deal_id, json.dumps(result)))
                return result
            row = db.execute("SELECT data FROM deals WHERE id=%s", (deal_id,)).fetchone()
            if row is None:
                return finish({"status": "REJECTED", "reasons": ["unknown deal"]})
            deal = Deal.model_validate(row["data"])
            try:
                self.chain.validate_deal(deal)
            except (RequestException, Web3Exception) as exc:
                reason = f"consumer chain unavailable ({type(exc).__name__})"
                self.store.audit(db, deal_id, "gateway", "CHAIN_STATE", {}, False, reason)
                return finish({"status": "WAITING", "reasons": [reason]})
            except ValueError as exc:
                self.store.audit(db, deal_id, "gateway", "CHAIN_STATE", {}, False, str(exc))
                return finish({"status": "REJECTED", "reasons": [str(exc)]})
            from oracle.crypto import canonical
            from web3 import Web3
            binding_hash = Web3.keccak(text=canonical({
                "request": deal.model_dump(), "rules": self.rules.rules,
                "profiles": self.attestor.profiles, "sources": {
                    sid: self.registry.sources.get(sid) for requirements in self.rules.rules.values()
                    for rule in requirements.values() for sid in rule["sources"]},
                "chainId": self.chain.chain_id, "contract": self.chain.address,
                "contractProfile": getattr(self.chain, "workflow", {}),
            })).hex()
            # Reuse a live attestation instead of issuing multiple concurrent authorizations.
            previous = db.execute("SELECT * FROM attestations WHERE deal_id=%s AND signature IS NOT NULL ORDER BY nonce DESC LIMIT 1", (deal_id,)).fetchone()
            now = int(time.time())
            authorities_active = all(
                any(sid in self.registry.sources and self.registry.sources[sid]["enabled"]
                    and getattr(self.registry.adapters.get(sid), "available", True)
                    and self.validator.can_verify(self.registry.sources[sid])
                    for sid in rule["sources"])
                for rule in self.rules.rules.get(deal.dealType, {}).values()
            )
            if previous and (previous["expires_at"] or previous["payload"].get("expiresAt", 0)) > now and authorities_active and previous["binding_hash"] == binding_hash:
                return finish({"status": "READY", "reasons": [], "attestation": dict(previous["payload"], signature=previous["signature"]),
                               "payload": previous["payload"], "signature": previous["signature"]})
            facts, errors, invalid = {}, [], False
            for fact, rule in self.rules.rules.get(deal.dealType, {}).items():
                source_id = next((sid for sid in rule["sources"]
                                  if sid in self.registry.sources and self.registry.sources[sid]["enabled"]), None)
                if source_id is None:
                    reason = "no enabled source"
                    errors.append(f"{fact}: {reason}")
                    expected_source = rule["sources"][0] if rule["sources"] else "registry"
                    self.store.audit(db, deal_id, expected_source, fact, {}, False, reason, {
                        "kind": "source", "transport": {"status": "not_requested"},
                        "validation": {"status": "NOT_CHECKED", "checks": {}},
                        "rules": {"status": "WAITING", "reasons": [reason]}})
                    continue
                source = self.registry.sources[source_id]
                raw = {}
                details = response_details(raw)
                details["verificationMethod"] = source.get("verification", "eip191")
                details["sourceSigner"] = source.get("signer", "")
                try:
                    if not self.validator.can_verify(source):
                        raise httpx.ConnectError("source verification extension is unavailable")
                    if not self.policy.allows(source, deal):
                        details["validation"]["checks"]["access"] = "REJECTED"
                        raise ValueError("source access denied")
                    raw = await self.registry.adapters[source_id].fetch_fact(deal, fact)
                    details.update(response_details(raw))
                    evidence, checks = self.validator.validate_with_report(raw, source, fact, deal)
                    details["validation"] = {"status": "VERIFIED", "checks": checks}
                    inserted = db.execute("INSERT INTO evidence_seen VALUES (%s,%s) ON CONFLICT DO NOTHING RETURNING evidence_id",
                                          (source_id, evidence.evidenceId)).fetchone()
                    checks["replay"] = "VERIFIED" if inserted else "REJECTED"
                    if inserted is None:
                        raise ValueError("duplicate evidence")
                    facts[fact] = evidence
                    rule_status, rule_reasons = RulesEngine({deal.dealType: {fact: rule}}).evaluate(deal, {fact: evidence})
                    details["rules"] = {"status": rule_status, "reasons": rule_reasons}
                    self.store.audit(db, deal_id, source_id, fact, raw, True, "VERIFIED", details)
                except (httpx.HTTPError, OSError) as exc:
                    errors.append(f"{fact}: source unavailable ({type(exc).__name__})")
                    captured = response_details(raw, exc) if not raw else response_details(raw)
                    details.update(captured)
                    details["rules"] = {"status": "WAITING", "reasons": [errors[-1]]}
                    self.store.audit(db, deal_id, source_id, fact, raw, False, errors[-1], details)
                except (ValueError, TypeError, KeyError, NotImplementedError) as exc:
                    invalid = True
                    errors.append(f"{fact}: {exc}")
                    if hasattr(exc, "transport"):
                        details.update(response_details(raw, exc))
                    details["validation"]["status"] = "REJECTED"
                    if hasattr(exc, "checks"):
                        details["validation"]["checks"] = exc.checks
                    self.store.audit(db, deal_id, source_id, fact, raw, False, errors[-1], details)
            status, reasons = self.rules.evaluate(deal, facts)
            if invalid:
                status = "REJECTED"
            if status != "READY":
                return finish({"status": status, "reasons": errors + reasons})
            try:
                self.chain.validate_deal(deal)
            except (RequestException, Web3Exception) as exc:
                reason = f"consumer chain unavailable ({type(exc).__name__})"
                self.store.audit(db, deal_id, "gateway", "CHAIN_STATE", {}, False, reason)
                return finish({"status": "WAITING", "reasons": [reason]})
            except ValueError as exc:
                self.store.audit(db, deal_id, "gateway", "CHAIN_STATE", {}, False, str(exc))
                return finish({"status": "REJECTED", "reasons": [str(exc)]})
            nonce = db.execute("INSERT INTO attestations(deal_id) VALUES (%s) RETURNING nonce", (deal_id,)).fetchone()["nonce"]
            try:
                payload, signature = self.attestor.issue(deal, facts, nonce, int(time.time()))
            except (ValueError, KeyError) as exc:
                return finish({"status": "WAITING", "reasons": [str(exc)]})
            db.execute("UPDATE attestations SET payload=%s::jsonb, signature=%s, evidence_ids=%s::jsonb, binding_hash=%s, expires_at=%s WHERE nonce=%s",
                       (json.dumps(payload), signature, json.dumps({f: {"sourceId": e.sourceId, "evidenceId": e.evidenceId} for f, e in facts.items()}), binding_hash, self.attestor.expiry(deal.dealType, payload), nonce))
            return finish({"status": "READY", "reasons": [], "attestation": dict(payload, signature=signature), "payload": payload, "signature": signature})
