"""Métricas realtime sem labels de alta cardinalidade ou dados sensíveis."""

from prometheus_client import Counter, Gauge

ACTIVE_CONNECTIONS = Gauge(
    "automation_realtime_ws_active",
    "Conexões WebSocket realtime ativas nesta instância",
)
DELIVERED_EVENTS = Counter(
    "automation_realtime_events_delivered_total",
    "Eventos realtime enfileirados para conexões locais",
)
DROPPED_CONNECTIONS = Counter(
    "automation_realtime_ws_backpressure_total",
    "Conexões encerradas por fila local cheia",
)
WS_CLOSED = Counter(
    "automation_realtime_ws_closed_total",
    "Conexões WebSocket encerradas por classe de motivo",
    labelnames=("reason",),
)
RELAY_FAILURES = Counter(
    "automation_realtime_outbox_relay_failures_total",
    "Falhas transitórias ao publicar o outbox no Redis",
)
OUTBOX_PENDING = Gauge(
    "automation_realtime_outbox_pending",
    "Linhas ainda não publicadas no outbox realtime",
)
