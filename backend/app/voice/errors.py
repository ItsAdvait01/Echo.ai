"""Safe, typed voice-provider errors. A3 owns this module."""


class VoiceAdapterError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code


class VoiceAdapterUnavailable(VoiceAdapterError):
    def __init__(self, message: str) -> None:
        super().__init__("voice_unavailable", message, retryable=False)
