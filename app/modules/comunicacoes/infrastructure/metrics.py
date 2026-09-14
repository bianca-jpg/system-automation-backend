"""Métricas de cardinalidade fixa para o delivery outbox."""

from prometheus_client import Counter, Histogram

EMAIL_DELIVERY_CLAIMS = Counter(
    "automation_email_delivery_claims_total",
    "Entregas de e-mail reivindicadas pelo worker.",
)
EMAIL_DELIVERY_OUTCOMES = Counter(
    "automation_email_delivery_outcomes_total",
    "Resultado das tentativas de entrega de e-mail.",
    ("outcome",),
)
EMAIL_DELIVERY_DURATION = Histogram(
    "automation_email_delivery_duration_seconds",
    "Tempo de uma tentativa SMTP até sua persistência.",
    ("outcome",),
    buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 40, 60),
)


class PrometheusEmailDeliveryMetrics:
    def reivindicada(self) -> None:
        EMAIL_DELIVERY_CLAIMS.inc()

    def concluida(self, outcome: str, duration_seconds: float) -> None:
        # ``outcome`` vem apenas do conjunto fechado do caso de uso.
        EMAIL_DELIVERY_OUTCOMES.labels(outcome=outcome).inc()
        EMAIL_DELIVERY_DURATION.labels(outcome=outcome).observe(duration_seconds)
