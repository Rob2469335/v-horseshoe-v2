class FallbackRouter:
    """
    Used when main Router fails or is unavailable.
    Deterministic safe routing only.
    """

    def route(self, candidates=None, role=None):
        if not candidates:
            return "qwen2.5:7b-instruct"

        # Prefer smallest safe model
        for c in candidates:
            if "3b" in c:
                return c

        return candidates[0]
