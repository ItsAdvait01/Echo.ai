"""Bounded retry and safe-stop starter. U4 owns this module."""


class RecoveryPolicy:
    max_retries = 1

    def should_retry(self, attempt: int, confidence: float, ambiguous_target: bool) -> bool:
        if ambiguous_target or confidence < 0.8:
            return False
        return attempt < self.max_retries
