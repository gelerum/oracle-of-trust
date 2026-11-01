from typing import Protocol

import httpx

from oracle.models import Deal
from oracle.responses import SourceReply, http_reply


class SourceAdapter(Protocol):
    """Define pull and optional push operations for registered sources."""

    async def fetch_fact(self, query: Deal, fact_type: str) -> dict:
        """Fetch canonical evidence.

        Args:
            query: Deal to query.
            fact_type: Requested fact type.

        Returns:
            Canonical evidence including source proof.
        """
        ...

    async def handle_event(self, event: dict) -> dict:
        """Normalize a push event.

        Args:
            event: Source event.

        Returns:
            Canonical evidence.
        """
        ...


class HttpMockAdapter:
    """Fetch signed mock responses over HTTP with bounded timeouts.

    Args:
        base_url: Registered mock endpoint.
        envelope: Source-specific response field.
        client: Optional injected HTTP client.
    """

    def __init__(self, base_url: str, envelope: str, client: httpx.AsyncClient | None = None):
        self.base_url = base_url
        self.envelope = envelope
        self.client = client

    async def fetch_fact(self, query: Deal, fact_type: str) -> dict:
        """Fetch and normalize an HTTP response.

        Args:
            query: Deal context.
            fact_type: Requested fact type.

        Returns:
            Signed canonical evidence.
        """
        async def request(client):
            response = await client.post(self.base_url + "/facts", json={
                "deal": query.model_dump(), "factType": fact_type}, timeout=3)
            def normalize(body):
                if not isinstance(body, dict) or not isinstance(body.get(self.envelope), dict):
                    raise ValueError("invalid source envelope")
                return body[self.envelope]
            return http_reply(response, normalize)
        if self.client is not None:
            return await request(self.client)
        async with httpx.AsyncClient() as client:
            return await request(client)

    async def handle_event(self, event: dict) -> dict:
        """Reject push mode until its transport authentication is configured.

        Args:
            event: Incoming source event.

        Raises:
            NotImplementedError: Push transport is not enabled in the MVP.
        """
        raise NotImplementedError("pull-only source")


class MockEdoAdapter(HttpMockAdapter):
    """Normalize the mock EDO document envelope.

    Args:
        base_url: Mock EDO URL.
        client: Optional HTTP test client.
    """

    def __init__(self, base_url: str, client=None):
        super().__init__(base_url, "documentEvidence", client)


class MockVtbFactoringAdapter(HttpMockAdapter):
    """Normalize the mock factoring decision envelope.

    Args:
        base_url: Mock factoring URL.
        client: Optional HTTP test client.
    """

    def __init__(self, base_url: str, client=None):
        super().__init__(base_url, "bankDecision", client)


class AlternateEdoAdapter(MockEdoAdapter):
    """Provide an interchangeable EDO transport implementation.

    Args:
        base_url: Alternate mock EDO URL.
        client: Optional HTTP test client.
    """

    async def fetch_fact(self, query: Deal, fact_type: str) -> dict:
        """Fetch evidence via the alternative mock route.

        Args:
            query: Deal context.
            fact_type: Requested fact type.

        Returns:
            The same signed canonical evidence contract.
        """
        original = self.base_url
        alternate = MockEdoAdapter(original + "/alternate", self.client)
        return await alternate.fetch_fact(query, fact_type)


class SignedHttpAdapter:
    """Normalize signed JSON from an operator-configured HTTP source.

    Args:
        config: Validated source definition without private credentials.
        client: Optional injected HTTP client.
    """

    def __init__(self, config: dict, client=None):
        self.config, self.client = config, client

    async def fetch_fact(self, query: Deal, fact_type: str) -> dict:
        """Read a signed fact using the standard oracle query envelope.

        Args:
            query: Registered request context.
            fact_type: Requested authorized fact.

        Returns:
            Canonical evidence whose signature is checked independently.
        """
        import os
        from oracle.configuration import at_path, context_data, resolve

        headers = {header: os.environ[name] for header, name in self.config.get("headersEnv", {}).items()}
        body = {"requestId": query.dealId, "factType": fact_type,
                "subjectRef": query.subjectRef, "documentHash": query.documentHash,
                "parameters": query.model_dump().get("parameters", {})}

        if self.config.get("requestBindings"):
            body = {field: resolve(binding, context_data(query), runtime={"factType": fact_type})
                    for field, binding in self.config["requestBindings"].items()}

        async def fetch(client):
            options = {"json": body} if self.config.get("method", "POST") == "POST" else {
                "params": body}
            response = await client.request(self.config.get("method", "POST"), self.config["baseUrl"],
                                            headers=headers, timeout=self.config.get("timeout", 3), **options)
            def normalize(body):
                raw = at_path(body, self.config.get("responsePath", ""))
                if self.config.get("fieldMap"):
                    raw = {field: at_path(raw, path) for field, path in self.config["fieldMap"].items()}
                return raw
            return http_reply(response, normalize)
        if self.client is not None:
            return await fetch(self.client)
        async with httpx.AsyncClient() as client:
            return await fetch(client)

    async def handle_event(self, event: dict) -> dict:
        """Reject unconfigured push transport.

        Args:
            event: Incoming source event.
        """
        raise NotImplementedError("push transport is not configured")


class SignedFileAdapter:
    """Read signed evidence only from the configured source-files directory.

    Args:
        config: Source definition with a relative file path.
    """

    def __init__(self, config: dict):
        self.config = config

    async def fetch_fact(self, query: Deal, fact_type: str) -> dict:
        """Load canonical signed evidence from a confined local file.

        Args:
            query: Requested context, checked by the independent validator.
            fact_type: Requested fact type.

        Returns:
            Untrusted canonical evidence.
        """
        import json
        import os
        from pathlib import Path

        root = Path(os.getenv("SOURCE_FILES_DIR", "/source-files")).resolve()
        path = (root / self.config["filePath"]).resolve()
        if not path.is_relative_to(root) or path == root:
            raise ValueError("source file must remain inside SOURCE_FILES_DIR")
        if path.stat().st_size > 1_048_576:
            raise ValueError("source file is too large")
        original = path.read_text()
        return SourceReply(json.loads(original), {"protocol": "file", "status": "received"}, original)

    async def handle_event(self, event: dict) -> dict:
        """Reject unconfigured push transport.

        Args:
            event: Incoming source event.
        """
        raise NotImplementedError("pull-only source")


class UnavailableAdapter:
    """Represent a registered extension that is absent from this server build."""

    available = False

    async def fetch_fact(self, query: Deal, fact_type: str) -> dict:
        """Report transport unavailability without producing a truth value.

        Args:
            query: Requested context.
            fact_type: Requested fact type.
        """
        raise httpx.ConnectError("registered source adapter is unavailable")

    async def handle_event(self, event: dict) -> dict:
        """Reject events while the installed source extension is unavailable.

        Args:
            event: Source event.
        """
        raise httpx.ConnectError("registered source adapter is unavailable")
