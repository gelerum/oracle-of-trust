import operator

from oracle.configuration import at_path, context_data
from oracle.models import Deal, Evidence, Retrieval


class RulesEngine:
    """Apply declarative rules independently of source and contract adapters.

    Args:
        rules: Consumer profile to required fact mapping.
    """

    def __init__(self, rules: dict):
        self.rules = rules

    def evaluate(self, deal: Deal, evidence: dict[str, Evidence]) -> tuple[str, list[str]]:
        """Check authorities, typed value conditions and signed attributes.

        Args:
            deal: Registered request context.
            evidence: Verified facts indexed by fact type.

        Returns:
            READY, WAITING or REJECTED and explanatory reasons.
        """
        requirements = self.rules.get(deal.dealType)
        if not requirements:
            return "REJECTED", ["unsupported consumer profile"]
        waiting, rejected = [], []
        context = context_data(deal)
        for fact, rule in requirements.items():
            item = evidence.get(fact)
            if item is None or item.retrievalStatus in (Retrieval.UNKNOWN, Retrieval.UNAVAILABLE):
                waiting.append(f"{fact}: unavailable")
                continue
            if item.sourceId not in rule["sources"]:
                rejected.append(f"{fact}: unauthorized authority")
                continue
            if item.retrievalStatus == Retrieval.REJECTED:
                rejected.append(f"{fact}: negative or rejected")
                continue
            try:
                condition = rule.get("condition", {"op": "eq", "value": rule.get("equals")})
                expected = at_path(context, condition["context"]) if "context" in condition else condition["value"]
                op = condition["op"]
                if op in ("eq", "ne"):
                    matches = type(item.value) is type(expected) and item.value == expected
                    matches = not matches if op == "ne" else matches
                elif op == "in":
                    matches = isinstance(expected, list) and any(type(item.value) is type(v) and item.value == v for v in expected)
                else:
                    numeric = type(item.value) in (int, float) and type(expected) in (int, float)
                    matches = numeric and {"gt": operator.gt, "gte": operator.ge,
                                           "lt": operator.lt, "lte": operator.le}[op](item.value, expected)
                if not matches:
                    rejected.append(f"{fact}: negative or condition not satisfied")
                attributes = {key: at_path(context, field) for key, field in rule.get("attributes", {}).items()}
                attributes.update(rule.get("required", {}))
                if any(type(item.attributes.get(k)) is not type(v) or item.attributes.get(k) != v
                       for k, v in attributes.items()):
                    rejected.append(f"{fact}: business attributes mismatch")
            except (KeyError, TypeError, ValueError, IndexError):
                rejected.append(f"{fact}: invalid rule or missing request parameter")
        return ("REJECTED", rejected + waiting) if rejected else (("WAITING", waiting) if waiting else ("READY", []))
