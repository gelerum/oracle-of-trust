import httpx


class SourceReply(dict):
    """Carry canonical evidence with transport metadata outside signed fields.

    Args:
        evidence: Canonical source evidence.
        transport: Nonsecret transport result, including HTTP status when available.
        original: Original response text for operator inspection.
    """

    def __init__(self, evidence: dict, transport: dict, original: str):
        super().__init__(evidence)
        self.transport, self.original = transport, original


class ResponseFormatError(ValueError):
    """Preserve an invalid source response for audit.

    Args:
        message: Normalization error.
        response: Received HTTP response.
    """

    def __init__(self, message: str, response: httpx.Response):
        super().__init__(message)
        self.transport = {"protocol": "http", "status": "received", "httpStatus": response.status_code}
        self.original = response.text


def http_reply(response: httpx.Response, normalize) -> SourceReply:
    """Normalize JSON while preserving the actual response independently.

    Args:
        response: Received HTTP response.
        normalize: Trusted adapter normalization function.

    Returns:
        Evidence with audit-only response metadata.
    """
    response.raise_for_status()
    try:
        evidence = normalize(response.json())
        if not isinstance(evidence, dict):
            raise ValueError("source response must contain an evidence object")
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise ResponseFormatError(str(exc), response) from exc
    return SourceReply(evidence, {"protocol": "http", "status": "received", "httpStatus": response.status_code}, response.text)


def response_details(raw, error=None) -> dict:
    """Describe an adapter outcome without treating transport success as truth.

    Args:
        raw: Canonical evidence or a source reply.
        error: Optional transport or validation exception.

    Returns:
        Audit metadata without request headers or credential values.
    """
    details = {"kind": "source", "transport": dict(getattr(raw, "transport", {})),
               "validation": {"status": "NOT_CHECKED", "checks": {}},
               "rules": {"status": "NOT_CHECKED", "reasons": []}}
    original = getattr(raw, "original", None)
    if error is not None:
        if hasattr(error, "transport"):
            details["transport"] = error.transport
            original = error.original
        elif isinstance(error, httpx.HTTPStatusError):
            details["transport"] = {"protocol": "http", "status": "error", "httpStatus": error.response.status_code}
            original = error.response.text
        elif isinstance(error, httpx.TimeoutException):
            details["transport"] = {"status": "timeout"}
        elif isinstance(error, (httpx.HTTPError, OSError)):
            details["transport"] = {"status": "unavailable"}
    if original is not None:
        details["originalResponse"] = original
    return details
