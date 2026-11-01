from enum import IntEnum, StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, AliasChoices, JsonValue

class Retrieval(StrEnum):
    PRESENT = "present"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    REJECTED = "rejected"


class OracleRequest(BaseModel):
    """Carry generic deal identity and rule-specific metadata.

    Args:
        dealId: On-chain deal identifier.
        documentHash: Hash of the underlying document.
        subjectRef: Off-chain subject reference.
        dealType: Configured rule and attestation profile name.
        **data: Additional terms validated by the concrete deal boundary.
    """

    model_config = ConfigDict(extra="allow", strict=True)
    dealId: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$", validation_alias=AliasChoices("dealId", "requestId"))
    documentHash: str = Field(default="0x" + "00" * 32, pattern=r"^0x[0-9a-fA-F]{64}$")
    subjectRef: str
    dealType: str = Field(validation_alias=AliasChoices("dealType", "workflowId"))
    parameters: dict = Field(default_factory=dict)


Deal = OracleRequest


class FactoringStatus(IntEnum):
    """Match the on-chain lifecycle of the factoring example."""

    NONE = 0
    ACTIVE = 1
    RESERVED = 2
    FINANCED = 3
    SETTLED = 4


class FactoringDeal(OracleRequest):
    """Validate the fixed factoring scenario at its integration boundary.

    Args:
        supplier: Immutable advance recipient.
        buyer: Debtor address.
        factor: Funding party address.
        claimAmount: Claim amount in token base units.
        financingAmount: Reserved advance in token base units.
        dueDate: Claim maturity as a Unix timestamp.
        commissionAmount: Fixed fee in token base units.
        claimId: Claim reference agreed for this specific deal.
        **data: Common deal identity fields.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    claimId: str = Field(min_length=1, max_length=128)
    supplier: str
    buyer: str
    factor: str
    claimAmount: int = Field(gt=0)
    financingAmount: int = Field(gt=0)
    dueDate: int = Field(gt=0)
    commissionAmount: int = Field(default=0, ge=0)


class Evidence(BaseModel):
    """Represent signed source evidence before gateway validation.

    Args:
        schemaVersion: Canonical schema version.
        evidenceId: Source-scoped unique evidence identifier.
        sourceId: Original registered source.
        factType: Type of the asserted fact.
        dealId: Bound deal identifier.
        subjectRef: Off-chain subject reference.
        value: Boolean assertion, or None when no assertion is available.
        retrievalStatus: Present, unknown, unavailable or rejected result.
        documentHash: Hash of the supporting document.
        observedAt: Source observation timestamp.
        expiresAt: Source validity deadline.
        attributes: Signed business terms and signer authorizations.
        sourceProof: Test source signature covering the canonical fields.
    """

    model_config = ConfigDict(extra="forbid", strict=True)
    schemaVersion: Literal["1"]
    evidenceId: str = Field(min_length=1, max_length=128)
    sourceId: str
    factType: str
    dealId: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    subjectRef: str
    value: JsonValue
    retrievalStatus: Literal["present", "unknown", "unavailable", "rejected"]
    documentHash: str = Field(pattern=r"^0x[0-9a-fA-F]{64}$")
    observedAt: int = Field(gt=0)
    expiresAt: int = Field(gt=0)
    attributes: dict
    sourceProof: str

    def signed_data(self) -> dict:
        """Return the canonical fields covered by the source signature."""
        return self.model_dump(mode="json", exclude={"sourceProof"})
