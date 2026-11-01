import time
from typing import Protocol

from jsonschema import Draft202012Validator

from oracle.proofs import ProofVerifiers
from oracle.models import Deal, Evidence, Retrieval


class AccessPolicy(Protocol):
    """Authorize access before any private-source read."""

    def allows(self, source: dict, deal: Deal) -> bool:
        """Check access.

        Args:
            source: Registered source.
            deal: Requested deal.

        Returns:
            Whether reading is permitted.
        """
        ...


class MockAccessPolicy:
    """Allow only explicitly marked mock integrations; not legal consent."""

    def allows(self, source: dict, deal: Deal) -> bool:
        """Check the demo source boundary.

        Args:
            source: Registered source metadata.
            deal: Requested deal.

        Returns:
            True only for enabled mock sources.
        """
        return source["type"] == "mock" and source["enabled"] is True


class DocumentCheck(Protocol):
    """Define optional deterministic or AI-assisted document checks."""

    def check(self, evidence: Evidence, deal: Deal) -> bool:
        """Inspect document consistency without replacing source authorization.

        Args:
            evidence: Verified source evidence.
            deal: Expected deal context.

        Returns:
            Strict boolean consistency decision.
        """
        ...


class DeterministicDocumentCheck:
    """Compare the signed document reference and subject with the deal."""

    def check(self, evidence: Evidence, deal: Deal) -> bool:
        """Check document bindings.

        Args:
            evidence: Evidence under validation.
            deal: Expected deal.

        Returns:
            Whether both bindings match.
        """
        return evidence.documentHash == deal.documentHash and evidence.subjectRef == deal.subjectRef


class EvidenceValidationError(ValueError):
    """Carry the completed validation stages with a rejected confirmation.

    Args:
        message: Rejection reason.
        checks: Stage results at the point of refusal.
    """

    def __init__(self, message: str, checks: dict):
        super().__init__(message)
        self.checks = dict(checks)


class EvidenceValidator:
    """Validate schema, provenance, authorization and evidence lifetime.

    Args:
        document_check: Additional document consistency implementation.
        verifiers: Installed origin verification methods.
    """

    def __init__(self, document_check: DocumentCheck | None = None, verifiers=None):
        self.document_check = document_check or DeterministicDocumentCheck()
        self.verifiers = verifiers or ProofVerifiers()

    def can_verify(self, source: dict) -> bool:
        """Check whether the configured provenance extension is installed.

        Args:
            source: Registered verification settings.

        Returns:
            Whether verification can be attempted.
        """
        method = source.get("verification", "eip191")
        return ("eip191" if method == "EIP-191 test signature" else method) in self.verifiers.verifiers

    def validate(self, raw: dict, source: dict, fact: str, deal: Deal, now: int | None = None) -> Evidence:
        """Validate untrusted source data independently of business rules.

        Args:
            raw: Untrusted canonical source response.
            source: Expected registered source and public signer.
            fact: Requested fact type.
            deal: Expected immutable deal.
            now: Optional test clock.

        Returns:
            Cryptographically verified evidence.

        Raises:
            ValueError: Evidence is invalid or unauthorized.
        """
        return self.validate_with_report(raw, source, fact, deal, now)[0]

    def validate_with_report(self, raw: dict, source: dict, fact: str, deal: Deal,
                             now: int | None = None) -> tuple[Evidence, dict]:
        """Validate evidence and report stages without changing signed data.

        Args:
            raw: Canonical response.
            source: Expected authority and verification method.
            fact: Requested fact type.
            deal: Registered context.
            now: Optional test clock.

        Returns:
            Verified evidence and completed stage results.
        """
        checks = {name: "NOT_CHECKED" for name in ("schema", "source", "request", "lifetime", "origin", "document", "value")}
        stage = "schema"
        try:
            evidence = Evidence.model_validate(raw)
            if evidence.schemaVersion != source["schemaVersion"]:
                raise ValueError("schema version mismatch")
            checks[stage] = "VERIFIED"
            stage = "source"
            if not source["enabled"] or evidence.sourceId != source["sourceId"]:
                raise ValueError("unauthorized source")
            if evidence.factType != fact or fact not in source["factTypes"]:
                raise ValueError("unauthorized fact type")
            checks[stage] = "VERIFIED"
            stage = "request"
            if evidence.dealId != deal.dealId:
                raise ValueError("deal mismatch")
            checks[stage] = "VERIFIED"
            stage = "lifetime"
            now = int(time.time()) if now is None else now
            if not (evidence.observedAt <= now < evidence.expiresAt) or evidence.observedAt >= evidence.expiresAt:
                raise ValueError("expired or future evidence")
            checks[stage] = "VERIFIED"
            stage = "origin"
            self.verifiers.verify(evidence, source)
            checks[stage] = "VERIFIED"
            stage = "document"
            if self.document_check.check(evidence, deal) is not True:
                raise ValueError("invalid document check")
            checks[stage] = "VERIFIED"
            stage = "value"
            if evidence.retrievalStatus == Retrieval.PRESENT:
                schema = source.get("factSchemas", {}).get(fact, {"type": "boolean"})
                if list(Draft202012Validator(schema).iter_errors(evidence.value)):
                    raise ValueError("fact value does not match its registered schema")
            elif evidence.value is not None:
                raise ValueError("missing evidence cannot carry a boolean")
            checks[stage] = "VERIFIED"
        except (ValueError, TypeError, KeyError) as exc:
            checks[stage] = "REJECTED"
            raise EvidenceValidationError(str(exc), checks) from exc
        return evidence, checks


class ConfiguredAccessPolicy:
    """Enforce explicit operator access decisions without claiming legal consent."""

    def allows(self, source: dict, deal: Deal) -> bool:
        """Check source availability and an explicit access reference.

        Args:
            source: Registered source definition.
            deal: Requested context.

        Returns:
            Whether the configured technical access policy permits a read.
        """
        if not source["enabled"]:
            return False
        if source["type"] == "mock":
            return True
        access = source.get("access", {})
        return access.get("approved") is True and bool(access.get("reference")) and bool(access.get("purpose"))
