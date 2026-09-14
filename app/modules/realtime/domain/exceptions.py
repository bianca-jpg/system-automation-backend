class RealtimeUnavailableError(RuntimeError):
    """Dependência transitória do transporte realtime indisponível."""


class CursorAheadError(ValueError):
    """Cliente tentou confirmar além do watermark conhecido do tópico."""


class ReplayRequiredError(RuntimeError):
    """O cursor ficou anterior à janela retida e precisa de resync REST."""
