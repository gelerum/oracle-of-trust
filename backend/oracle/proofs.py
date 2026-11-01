from oracle.crypto import recover_source


class ProofVerifiers:
    """Register origin verification independently of transports and business rules."""

    def __init__(self):
        self.verifiers = {"eip191": self._eip191}

    def register(self, name: str, verifier):
        """Install a verifier from trusted application code.

        Args:
            name: Method identifier referenced by a source definition.
            verifier: Callable receiving evidence and public source configuration.
        """
        if name in self.verifiers:
            raise ValueError("proof verifier already registered")
        self.verifiers[name] = verifier

    def verify(self, evidence, source: dict):
        """Verify provenance with the source's registered verification method.

        Args:
            evidence: Schema-validated source evidence.
            source: Expected source authority and verification settings.

        Raises:
            ValueError: Origin is unverified or the method is not installed.
        """
        method = source.get("verification", "eip191")
        method = "eip191" if method == "EIP-191 test signature" else method
        if method not in self.verifiers:
            raise ValueError("proof verification method is not installed")
        if self.verifiers[method](evidence, source) is not True:
            raise ValueError("invalid source proof")

    @staticmethod
    def _eip191(evidence, source: dict) -> bool:
        """Check the canonical mock signature against the configured public signer.

        Args:
            evidence: Canonical signed evidence.
            source: Source public key configuration.

        Returns:
            Whether the expected source signed all canonical fields.
        """
        if recover_source(evidence.signed_data(), evidence.sourceProof).lower() != source["signer"].lower():
            raise ValueError("invalid source signature")
        return True


def load_extensions(factories, verifiers):
    """Load explicitly installed server extensions, never operator-uploaded code.

    Args:
        factories: Adapter registration interface.
        verifiers: Proof verification registration interface.
    """
    import importlib
    import os
    for name in filter(None, (item.strip() for item in os.getenv("ORACLE_EXTENSIONS", "").split(","))):
        importlib.import_module(name).register(factories, verifiers)
