import os
import re
from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import AliasChoices, Field, computed_field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

ENV = os.getenv("ENV", "dev").upper()


def _postgres_aliases(suffix: str) -> AliasChoices:
    aliases = [f"{ENV}_POSTGRES_{suffix}", f"POSTGRES_{suffix}"]
    # TEST e outros ambientes locais historicamente reutilizam DEV_*; PROD
    # nunca pode fazer esse fallback silencioso.
    if ENV != "PROD" and ENV != "DEV":
        aliases.append(f"DEV_POSTGRES_{suffix}")
    return AliasChoices(*aliases)


def _resolve_postgres_host(host: str) -> str:
    """Traduz nomes da rede Compose quando o processo roda no host."""
    if host in {"dev_db", "system_automation_db"} and not os.path.exists("/.dockerenv"):
        return os.getenv("DEV_POSTGRES_HOST_LOCAL", "localhost")
    return host


def _resolve_redis_url(url: str) -> str:
    """Troca apenas o servidor Compose e preserva o DB de cada consumidor."""
    if os.path.exists("/.dockerenv"):
        return url
    parsed = urlsplit(url)
    if parsed.hostname not in {"redis", "system_automation_redis"}:
        return url
    local = urlsplit(os.getenv("REDIS_URL_LOCAL", "redis://localhost:6379"))
    if not local.scheme or not local.netloc:
        return url
    return urlunsplit(
        (local.scheme, local.netloc, parsed.path, parsed.query, parsed.fragment)
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url_override: str | None = Field(
        default=None,
        validation_alias=AliasChoices("DATABASE_URL"),
    )

    postgres_user: str = Field(
        default="automation",
        validation_alias=_postgres_aliases("USER"),
    )
    postgres_password: str = Field(
        default="automation",
        validation_alias=_postgres_aliases("PASSWORD"),
    )
    postgres_db: str = Field(
        default="system_automation",
        validation_alias=_postgres_aliases("DB"),
    )
    postgres_host: str = Field(
        default="dev_db",
        validation_alias=_postgres_aliases("HOST"),
    )
    postgres_port: str = Field(
        default="5432",
        validation_alias=_postgres_aliases("PORT"),
    )
    postgres_ssl_mode: Literal[
        "disable", "allow", "prefer", "require", "verify-ca", "verify-full"
    ] = Field(
        default="require" if ENV == "PROD" else "prefer",
        validation_alias="POSTGRES_SSL_MODE",
    )
    postgres_connect_timeout_seconds: float = Field(
        default=5.0,
        gt=0,
        le=30,
        validation_alias="POSTGRES_CONNECT_TIMEOUT_SECONDS",
    )
    postgres_command_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=300,
        validation_alias="POSTGRES_COMMAND_TIMEOUT_SECONDS",
    )
    postgres_migration_timeout_seconds: float = Field(
        default=900.0,
        ge=60,
        le=3_600,
        validation_alias="POSTGRES_MIGRATION_TIMEOUT_SECONDS",
    )

    # Databricks (SQL Statement Execution API — ver doc técnica project).
    # Token NÃO deve ir para o Git; mora só no .env (gitignored).
    databricks_server_hostname: str = Field(
        default="", validation_alias="DATABRICKS_SERVER_HOSTNAME"
    )
    databricks_http_path: str = Field(
        default="", validation_alias="DATABRICKS_HTTP_PATH"
    )
    databricks_token: str = Field(default="", validation_alias="DATABRICKS_TOKEN")
    databricks_tabela_pedidos: str = Field(
        default="", validation_alias="DATABRICKS_TABELA_PEDIDOS"
    )
    databricks_tabela_estoque: str = Field(
        default="", validation_alias="DATABRICKS_TABELA_ESTOQUE"
    )
    # Tabela do ERP com os flags indica_reserva/indica_embalado — fonte de verdade
    # sobre o que já foi processado de fato (reserva feita / pedido embalado).
    databricks_tabela_pedidos_processados: str = Field(
        default="", validation_alias="DATABRICKS_TABELA_PEDIDOS_PROCESSADOS"
    )
    # Referência tamanho->posição da grade Linx (view
    # programa_estagio.refined.system_automation_prod_tamanho_ref) — 5ª fonte do sync (Fase 2).
    databricks_tabela_tamanho_ref: str = Field(
        default="", validation_alias="DATABRICKS_TABELA_TAMANHO_REF"
    )
    # Cliente SQL: timeout por request fica dentro de um deadline total; retries
    # e coleta sao bounded para uma origem lenta nao ocupar o worker sem limite.
    databricks_request_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=120,
        validation_alias="DATABRICKS_REQUEST_TIMEOUT_SECONDS",
    )
    databricks_total_timeout_seconds: float = Field(
        default=900.0,
        gt=0,
        le=1_800,
        validation_alias="DATABRICKS_TOTAL_TIMEOUT_SECONDS",
    )
    databricks_cancel_timeout_seconds: float = Field(
        default=3.0,
        gt=0,
        le=10,
        validation_alias="DATABRICKS_CANCEL_TIMEOUT_SECONDS",
    )
    databricks_max_attempts: int = Field(
        default=4,
        ge=1,
        le=8,
        validation_alias="DATABRICKS_MAX_ATTEMPTS",
    )
    databricks_retry_base_seconds: float = Field(
        default=0.5,
        ge=0,
        le=30,
        validation_alias="DATABRICKS_RETRY_BASE_SECONDS",
    )
    databricks_retry_max_seconds: float = Field(
        default=8.0,
        ge=0,
        le=60,
        validation_alias="DATABRICKS_RETRY_MAX_SECONDS",
    )
    databricks_poll_interval_seconds: float = Field(
        default=2.0,
        gt=0,
        le=60,
        validation_alias="DATABRICKS_POLL_INTERVAL_SECONDS",
    )
    databricks_max_polls: int = Field(
        default=450,
        ge=1,
        le=3_600,
        validation_alias="DATABRICKS_MAX_POLLS",
    )
    databricks_max_chunks: int = Field(
        default=2_048,
        ge=1,
        le=10_000,
        validation_alias="DATABRICKS_MAX_CHUNKS",
    )
    databricks_max_rows: int = Field(
        default=1_500_000,
        ge=1,
        le=2_000_000,
        validation_alias="DATABRICKS_MAX_ROWS",
    )
    databricks_max_bytes: int = Field(
        default=536_870_912,
        ge=1_048_576,
        le=1_073_741_824,
        validation_alias="DATABRICKS_MAX_BYTES",
    )
    databricks_max_statement_bytes: int = Field(
        default=1_048_576,
        ge=1_024,
        le=16_777_216,
        validation_alias="DATABRICKS_MAX_STATEMENT_BYTES",
    )
    databricks_external_host_suffixes: str = Field(
        default=".blob.core.windows.net,.dfs.core.windows.net",
        min_length=1,
        max_length=1_024,
        validation_alias="DATABRICKS_EXTERNAL_HOST_SUFFIXES",
    )
    # Intervalo (segundos) da sincronização periódica Databricks -> Postgres
    # executada pelo Celery beat. Default 7200s (2 horas).
    ingestao_intervalo_segundos: int = Field(
        default=7200, validation_alias="INGESTAO_INTERVALO_SEGUNDOS"
    )
    ingestao_total_timeout_seconds: float = Field(
        default=1800.0,
        ge=300,
        le=3600,
        validation_alias="INGESTAO_TOTAL_TIMEOUT_SECONDS",
    )
    # Ano dos pedidos JÁ processados (ERP) trazidos para o Histórico — filtra a
    # ingestão por year(dt_emissao). Default 2026 (ano corrente do projeto).
    ingestao_ano_historico: int = Field(
        default=2026, validation_alias="INGESTAO_ANO_HISTORICO"
    )

    redis_url: str = Field(default="redis://redis:6379/0", validation_alias="REDIS_URL")
    celery_broker_url: str = Field(
        default="redis://redis:6379/0", validation_alias="CELERY_BROKER_URL"
    )
    celery_result_backend: str = Field(
        default="redis://redis:6379/1", validation_alias="CELERY_RESULT_BACKEND"
    )

    # Realtime (outbox PostgreSQL -> Redis Stream -> WebSockets). Todos os
    # limites são deliberadamente bounded para impedir crescimento sem controle
    # e conexões lentas consumindo memória da API.
    realtime_stream_key: str = Field(
        default="automation:realtime:v1",
        min_length=1,
        max_length=128,
        validation_alias="REALTIME_STREAM_KEY",
    )
    realtime_redis_connect_timeout_seconds: float = Field(
        default=2.0,
        gt=0,
        le=10,
        validation_alias="REALTIME_REDIS_CONNECT_TIMEOUT_SECONDS",
    )
    realtime_redis_socket_timeout_seconds: float = Field(
        default=5.0,
        gt=1,
        le=30,
        validation_alias="REALTIME_REDIS_SOCKET_TIMEOUT_SECONDS",
    )
    realtime_stream_maxlen: int = Field(
        default=20_000,
        ge=1_000,
        le=1_000_000,
        validation_alias="REALTIME_STREAM_MAXLEN",
    )
    realtime_payload_max_bytes: int = Field(
        default=16_384,
        ge=1_024,
        le=262_144,
        validation_alias="REALTIME_PAYLOAD_MAX_BYTES",
    )
    realtime_ticket_ttl_seconds: int = Field(
        default=30,
        # Frontend consulta /status (deadline 10 s) antes de abrir o socket.
        # Mantém margem operacional mesmo no menor valor aceito.
        ge=20,
        le=120,
        validation_alias="REALTIME_TICKET_TTL_SECONDS",
    )
    realtime_ticket_user_rate_per_minute: int = Field(
        default=12,
        ge=1,
        le=120,
        validation_alias="REALTIME_TICKET_USER_RATE_PER_MINUTE",
    )
    realtime_ticket_ip_rate_per_minute: int = Field(
        default=60,
        ge=1,
        le=600,
        validation_alias="REALTIME_TICKET_IP_RATE_PER_MINUTE",
    )
    realtime_connection_queue_size: int = Field(
        default=128,
        ge=8,
        le=2_048,
        validation_alias="REALTIME_CONNECTION_QUEUE_SIZE",
    )
    realtime_max_connections_per_user: int = Field(
        default=4,
        ge=1,
        le=20,
        validation_alias="REALTIME_MAX_CONNECTIONS_PER_USER",
    )
    realtime_max_connections_per_instance: int = Field(
        default=1_000,
        ge=10,
        le=20_000,
        validation_alias="REALTIME_MAX_CONNECTIONS_PER_INSTANCE",
    )
    realtime_heartbeat_seconds: int = Field(
        default=20,
        ge=5,
        # O cliente web encerra silêncio em 60 s; manter cadence <=25 s dá
        # margem mesmo com jitter e uma batida perdida.
        le=25,
        validation_alias="REALTIME_HEARTBEAT_SECONDS",
    )
    realtime_idle_timeout_seconds: int = Field(
        default=65,
        ge=15,
        le=300,
        validation_alias="REALTIME_IDLE_TIMEOUT_SECONDS",
    )
    realtime_auth_revalidate_seconds: int = Field(
        default=60,
        ge=15,
        le=600,
        validation_alias="REALTIME_AUTH_REVALIDATE_SECONDS",
    )
    realtime_relay_interval_ms: int = Field(
        default=250,
        ge=50,
        le=5_000,
        validation_alias="REALTIME_RELAY_INTERVAL_MS",
    )
    realtime_relay_batch_size: int = Field(
        default=100,
        ge=1,
        le=1_000,
        validation_alias="REALTIME_RELAY_BATCH_SIZE",
    )
    realtime_replay_max_events: int = Field(
        default=250,
        ge=10,
        le=1_000,
        validation_alias="REALTIME_REPLAY_MAX_EVENTS",
    )
    realtime_send_timeout_seconds: float = Field(
        default=5.0,
        ge=1,
        le=15,
        validation_alias="REALTIME_SEND_TIMEOUT_SECONDS",
    )
    realtime_replay_timeout_seconds: float = Field(
        default=15.0,
        ge=2,
        le=60,
        validation_alias="REALTIME_REPLAY_TIMEOUT_SECONDS",
    )
    realtime_outbox_retention_days: int = Field(
        default=14,
        ge=1,
        le=365,
        validation_alias="REALTIME_OUTBOX_RETENTION_DAYS",
    )
    realtime_cleanup_interval_seconds: int = Field(
        default=3_600,
        ge=60,
        le=86_400,
        validation_alias="REALTIME_CLEANUP_INTERVAL_SECONDS",
    )
    realtime_allowed_topics: str = Field(
        default="orders,alerts,communications,history",
        min_length=1,
        max_length=512,
        validation_alias="REALTIME_ALLOWED_TOPICS",
    )

    jwt_secret: str = Field(
        default="change-me-in-production", validation_alias="JWT_SECRET"
    )
    jwt_algorithm: str = Field(default="HS256", validation_alias="JWT_ALGORITHM")
    jwt_expire_minutes: int = Field(default=480, validation_alias="JWT_EXPIRE_MINUTES")
    # Teto absoluto de sessão, contado a partir do login original (não do
    # último refresh) — impede que o refresh token renove indefinidamente via
    # janela deslizante. Passado esse prazo, refresh_token falha e força novo
    # login (Microsoft SSO incluso).
    auth_absolute_session_seconds: int = Field(
        default=7 * 24 * 60 * 60, validation_alias="AUTH_ABSOLUTE_SESSION_SECONDS"
    )

    # Login via Microsoft Entra ID (SSO). Vazios = endpoint /sso/microsoft
    # responde 503 em vez de tentar validar token contra um tenant inexistente.
    microsoft_tenant_id: str = Field(default="", validation_alias="MICROSOFT_TENANT_ID")
    microsoft_client_id: str = Field(default="", validation_alias="MICROSOFT_CLIENT_ID")

    # Chave fixa que o scraper Prometheus manda no header X-Metrics-Key.
    # Vazia = GET /metrics responde 503 (fail-closed, nunca abre sem chave
    # configurada). O valor mora só no .env/secret manager de cada ambiente.
    metrics_api_key: str = Field(default="", validation_alias="METRICS_API_KEY")

    # Teto do corpo de requisicoes HTTP aceito pela API (MaxBodySizeMiddleware
    # em app/main.py). Default 2 MiB: o maior payload legitimo hoje e
    # AlterarGradesProdutoRequest (100 changes, cada uma com expected_version
    # de 64 hex e ate 100 tamanhos de <=16 caracteres) — pior caso ~290 KB,
    # ~7x de folga. Os demais inputs sao bem menores (MicrosoftSsoRequest.id_token
    # <= 8 KiB; proposed_payload <= 16 KiB).
    request_max_body_bytes: int = Field(
        default=2_097_152,
        ge=65_536,
        le=67_108_864,
        validation_alias="REQUEST_MAX_BODY_BYTES",
    )

    cors_origins: str = Field(
        default="http://localhost:3000,http://localhost:5173",
        validation_alias="CORS_ORIGINS",
    )

    # SMTP / envio de e-mail (provider-agnóstico).
    # Teste: Gmail (smtp.gmail.com:587 + senha de app). Prod: smtp.office365.com:587.
    # Credenciais ficam só no .env (gitignored).
    smtp_host: str = Field(default="smtp.gmail.com", validation_alias="SMTP_HOST")
    smtp_port: int = Field(default=587, validation_alias="SMTP_PORT")
    smtp_user: str = Field(default="", validation_alias="SMTP_USER")
    smtp_password: str = Field(default="", validation_alias="SMTP_PASSWORD")
    smtp_from: str = Field(default="", validation_alias="SMTP_FROM")
    smtp_starttls: bool = Field(default=True, validation_alias="SMTP_STARTTLS")
    smtp_timeout_seconds: int = Field(
        default=10,
        ge=1,
        le=60,
        validation_alias="SMTP_TIMEOUT_SECONDS",
    )
    communication_delivery_interval_seconds: int = Field(
        default=30,
        ge=5,
        le=3_600,
        validation_alias="COMMUNICATION_DELIVERY_INTERVAL_SECONDS",
    )
    communication_delivery_batch_size: int = Field(
        default=20,
        ge=1,
        le=100,
        validation_alias="COMMUNICATION_DELIVERY_BATCH_SIZE",
    )
    communication_delivery_max_attempts: int = Field(
        default=5,
        ge=1,
        le=20,
        validation_alias="COMMUNICATION_DELIVERY_MAX_ATTEMPTS",
    )
    communication_delivery_lease_seconds: int = Field(
        default=120,
        ge=30,
        le=3_600,
        validation_alias="COMMUNICATION_DELIVERY_LEASE_SECONDS",
    )
    communication_delivery_retry_base_seconds: int = Field(
        default=30,
        ge=1,
        le=3_600,
        validation_alias="COMMUNICATION_DELIVERY_RETRY_BASE_SECONDS",
    )
    communication_delivery_retry_max_seconds: int = Field(
        default=1_800,
        ge=1,
        le=86_400,
        validation_alias="COMMUNICATION_DELIVERY_RETRY_MAX_SECONDS",
    )

    # Adequação
    tolerancia_adequacao: float = Field(
        default=0.05, validation_alias="TOLERANCIA_ADEQUACAO"
    )
    criterio_selecao: str = Field(default="valor", validation_alias="CRITERIO_SELECAO")

    @property
    def effective_postgres_host(self) -> str:
        return _resolve_postgres_host(self.postgres_host)

    @computed_field
    @property
    def database_url(self) -> str:
        if self.database_url_override:
            url = self.database_url_override
            if url.startswith("postgresql://"):
                return url.replace("postgresql://", "postgresql+asyncpg://", 1)
            return url
        host = self.effective_postgres_host
        return URL.create(
            drivername="postgresql+asyncpg",
            username=self.postgres_user,
            password=self.postgres_password,
            host=host,
            port=int(self.postgres_port),
            database=self.postgres_db,
        ).render_as_string(hide_password=False)

    @property
    def databricks_base_url(self) -> str:
        return f"https://{self.databricks_server_hostname}"

    @property
    def databricks_warehouse_id(self) -> str:
        # http_path tem o formato /sql/1.0/warehouses/<warehouse_id>
        return self.databricks_http_path.rstrip("/").split("/")[-1]

    @property
    def databricks_external_host_suffixes_list(self) -> list[str]:
        return [
            suffix.strip().lower()
            for suffix in self.databricks_external_host_suffixes.split(",")
            if suffix.strip()
        ]

    @property
    def effective_redis_url(self) -> str:
        return _resolve_redis_url(self.redis_url)

    @property
    def effective_celery_broker_url(self) -> str:
        return _resolve_redis_url(self.celery_broker_url)

    @property
    def effective_celery_result_backend(self) -> str:
        return _resolve_redis_url(self.celery_result_backend)

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def realtime_allowed_topics_list(self) -> list[str]:
        return [
            topic.strip()
            for topic in self.realtime_allowed_topics.split(",")
            if topic.strip()
        ]

    @property
    def effective_smtp_from(self) -> str:
        return self.smtp_from or self.smtp_user

    @property
    def smtp_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    @model_validator(mode="after")
    def validate_cross_field_constraints(self) -> "Settings":
        if (
            self.databricks_request_timeout_seconds
            > self.databricks_total_timeout_seconds
        ):
            raise ValueError(
                "DATABRICKS_REQUEST_TIMEOUT_SECONDS nao pode exceder "
                "DATABRICKS_TOTAL_TIMEOUT_SECONDS"
            )
        if self.databricks_retry_base_seconds > self.databricks_retry_max_seconds:
            raise ValueError(
                "DATABRICKS_RETRY_BASE_SECONDS nao pode exceder "
                "DATABRICKS_RETRY_MAX_SECONDS"
            )
        if self.databricks_max_attempts > 1 and self.databricks_retry_max_seconds == 0:
            raise ValueError(
                "DATABRICKS_RETRY_MAX_SECONDS deve ser positivo quando retries estao ativos"
            )
        external_suffixes = self.databricks_external_host_suffixes_list
        suffix_pattern = re.compile(r"^\.[a-z0-9-]+(?:\.[a-z0-9-]+)+$")
        if (
            not external_suffixes
            or len(external_suffixes) > 16
            or len(set(external_suffixes)) != len(external_suffixes)
            or any(suffix_pattern.fullmatch(item) is None for item in external_suffixes)
        ):
            raise ValueError(
                "DATABRICKS_EXTERNAL_HOST_SUFFIXES deve conter 1..16 sufixos DNS unicos"
            )
        if self.communication_delivery_lease_seconds < self.smtp_timeout_seconds + 30:
            raise ValueError(
                "COMMUNICATION_DELIVERY_LEASE_SECONDS deve exceder "
                "SMTP_TIMEOUT_SECONDS em pelo menos 30 segundos"
            )
        if (
            self.communication_delivery_retry_base_seconds
            > self.communication_delivery_retry_max_seconds
        ):
            raise ValueError(
                "COMMUNICATION_DELIVERY_RETRY_BASE_SECONDS não pode exceder o máximo"
            )
        if ENV == "PROD":
            forbidden_hosts = {
                "localhost",
                "127.0.0.1",
                "::1",
                "dev_db",
                "system_automation_db",
                "redis",
                "system_automation_redis",
            }
            database = make_url(self.database_url)
            if (
                not database.host
                or database.host.lower() in forbidden_hosts
                or not database.username
                or not database.password
                or database.password == "automation"
            ):
                raise ValueError(
                    "PROD exige DATABASE_URL ou credenciais PostgreSQL remotas e explícitas"
                )
            redis_urls = (
                self.redis_url,
                self.celery_broker_url,
                self.celery_result_backend,
            )
            if any(
                not urlsplit(url).hostname
                or str(urlsplit(url).hostname).lower() in forbidden_hosts
                for url in redis_urls
            ):
                raise ValueError("PROD exige Redis/Celery remotos e explícitos")
            if (
                self.jwt_secret == "change-me-in-production"
                or len(self.jwt_secret) < 32
            ):
                raise ValueError(
                    "PROD exige JWT_SECRET forte com pelo menos 32 caracteres"
                )
            if len(self.metrics_api_key) < 32:
                raise ValueError(
                    "PROD exige METRICS_API_KEY com pelo menos 32 caracteres"
                )
            localhost_hosts = {"localhost", "127.0.0.1", "::1"}
            cors_hosts = {
                (urlsplit(origin).hostname or "").lower()
                for origin in self.cors_origins_list
            }
            if not cors_hosts or cors_hosts <= localhost_hosts:
                raise ValueError(
                    "PROD exige CORS_ORIGINS com ao menos uma origem que não seja localhost"
                )
        if self.realtime_idle_timeout_seconds <= 2 * self.realtime_heartbeat_seconds:
            raise ValueError(
                "REALTIME_IDLE_TIMEOUT_SECONDS deve ser maior que duas vezes "
                "REALTIME_HEARTBEAT_SECONDS"
            )
        if (
            self.realtime_max_connections_per_user
            > self.realtime_max_connections_per_instance
        ):
            raise ValueError(
                "REALTIME_MAX_CONNECTIONS_PER_USER não pode exceder o limite da instância"
            )
        topics = self.realtime_allowed_topics_list
        topic_pattern = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
        if not topics or len(topics) > 16 or len(set(topics)) != len(topics):
            raise ValueError("REALTIME_ALLOWED_TOPICS deve conter 1..16 tópicos únicos")
        if any(topic_pattern.fullmatch(topic) is None for topic in topics):
            raise ValueError("REALTIME_ALLOWED_TOPICS contém tópico inválido")
        if (
            self.realtime_stream_maxlen * (self.realtime_payload_max_bytes + 2_048)
            > 512 * 1_024 * 1_024
        ):
            raise ValueError(
                "REALTIME_STREAM_MAXLEN × payload excede o orçamento de 512 MiB"
            )
        if (
            self.realtime_replay_max_events * self.realtime_payload_max_bytes
            > 32 * 1_024 * 1_024
        ):
            raise ValueError(
                "REALTIME_REPLAY_MAX_EVENTS × payload excede 32 MiB por handshake"
            )
        if (
            self.realtime_connection_queue_size * self.realtime_payload_max_bytes
            > 8 * 1_024 * 1_024
        ):
            raise ValueError(
                "REALTIME_CONNECTION_QUEUE_SIZE × payload excede 8 MiB por socket"
            )
        if (
            self.realtime_max_connections_per_instance
            * self.realtime_connection_queue_size
            > 2_000_000
        ):
            raise ValueError(
                "conexões × queue excede 2 milhões de referências por instância"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
