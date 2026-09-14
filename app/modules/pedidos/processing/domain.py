"""Tipos e regras puras do processamento durável de Pedidos."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any
from uuid import UUID, uuid5

from app.modules.pedidos.domain.motor_adequacao import (
    CancellationToken,
    PlanningCancellationRequested,
    processar_pedidos,
)
from app.modules.pedidos.domain.ordem_reserva_linx import montar_linha_linx
from app.modules.pedidos.domain.politica_quantidade import ModoAdequacao
from app.modules.pedidos.domain.standby_motivo import SEM_ESTOQUE
from app.modules.pedidos.domain.value_objects import normalizar_canal
from app.shared.jobs.domain import NewJob, digest_idempotency_key, fingerprint_payload

PROCESSING_JOB_KIND = "orders.processing.v1"
PROCESSING_IDEMPOTENCY_NAMESPACE = "orders.processing"
PROCESSING_MAX_ATTEMPTS = 3
PROCESSING_DEADLINE = timedelta(minutes=15)
PROCESSING_LEASE = timedelta(seconds=60)
PROCESSING_RETRY_DELAY = timedelta(seconds=15)
PLAN_PAIR_LIMIT = 100_000
# Os dois modos agora hidratam a foto global do canal na mesma transação
# (`load_pending_items`) e a submetem inteira a `build_processing_plan`; o
# teto de memória real é o preflight (`PLAN_ITEM_LIMIT`/`MAX_PLAN_TOTAL_BYTES`),
# não mais um esquema de páginas keyset — o streaming que existia para
# `sem_adequar` foi removido (15-03).
# 132.405 itens mediram 434 MiB RSS total; 200k mantém margem para a foto real e
# limita o pior caso extrapolado a cerca de 600 MiB antes da hidratação.
# O pico de memória do modo sem adequação ainda não foi medido (plano 15-04) —
# não extrapole o número acima, o volume elegível de `sem_adequar` pode ser
# estruturalmente maior porque, até esta entrega, ele aceitava todo par.
PLAN_ITEM_LIMIT = 200_000
# 256 MiB permanece hard cap contratual. O teto operacional de 96 MiB é uma
# defesa adicional aplicada aos dois modos ao acumular os payloads do plano.
HARD_MAX_PLAN_TOTAL_BYTES = 256 * 1024 * 1024
MAX_PLAN_TOTAL_BYTES = 96 * 1024 * 1024
APPLY_CHUNK_SIZE = 250
PLAN_PAIR_ITEM_LIMIT = 100
STOCK_TARGET_CHUNK_SIZE = 200
MAX_PLAN_PAYLOAD_BYTES = 128 * 1024
MAX_PRODUCT_CODE_LENGTH = 64
MAX_SIZE_LENGTH = 16

_INTEGER_MAX = 2_147_483_647
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_EVENT_NAMESPACE = UUID("7c433305-3138-594e-a810-bf9104d09735")


class ProcessingMode(StrEnum):
    ADEQUAR = "adequar"
    SEM_ADEQUAR = "sem_adequar"


class ProcessingChannel(StrEnum):
    TODOS = "Todos"
    FRANQUIA = "Franquia"
    MULTIMARCA = "Multimarca"


class PlanningState(StrEnum):
    PENDING = "pending"
    PLANNED = "planned"


class PlanRowState(StrEnum):
    PLANNED = "planned"
    APPLIED = "applied"


class ProcessingError(RuntimeError):
    """Erro do caso de uso cuja mensagem nunca contém payload/PII."""


class InvalidProcessingRequest(ProcessingError, ValueError):
    pass


class ProcessingPlanTooLarge(ProcessingError):
    pass


class ProcessingPlanConflict(ProcessingError):
    pass


class ProcessingLockUnavailable(ProcessingError):
    pass


class ProcessingLeaseLost(ProcessingError):
    pass


class ProcessingPlanningCancelled(ProcessingLeaseLost):
    """Cancelamento cooperativo do planejamento sem expor dados do snapshot."""


def _ensure_not_cancelled(cancel_token: CancellationToken | None) -> None:
    if cancel_token is None:
        return
    cancelled = cancel_token() if callable(cancel_token) else cancel_token.is_set()
    if cancelled:
        raise ProcessingPlanningCancelled("processing_planning_cancelled")


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            default=_json_default,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InvalidProcessingRequest("processing_payload_not_json_safe") from exc


def _jsonb_text_size(value: object) -> int:
    """Mede a representação textual usada pelo JSONB no limite persistido.

    O PostgreSQL inclui um espaço após `,` e `:` em ``jsonb::text``. O hash
    continua compacto/canônico, mas o limite precisa medir a forma realmente
    validada pelo CHECK para não aceitar em memória algo que o INSERT rejeite.
    Os números do payload vêm de INTEGER/NUMERIC bounded do snapshot.
    """

    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
            default=_json_default,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise InvalidProcessingRequest("processing_payload_not_json_safe") from exc
    return len(encoded)


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def validate_idempotency_key(value: str) -> str:
    """Valida ASCII imprimível sem permitir whitespace ou segredos ilimitados."""

    if not _IDEMPOTENCY_KEY.fullmatch(value):
        raise InvalidProcessingRequest("invalid_idempotency_key")
    return value


@dataclass(frozen=True, slots=True)
class ProcessingRequest:
    mode: ProcessingMode
    channel: ProcessingChannel


@dataclass(frozen=True, slots=True)
class PendingSnapshotStats:
    pair_count: int
    item_count: int
    estimated_bytes: int
    max_pair_item_count: int = 0

    def __post_init__(self) -> None:
        if (
            min(
                self.pair_count,
                self.item_count,
                self.estimated_bytes,
                self.max_pair_item_count,
            )
            < 0
        ):
            raise InvalidProcessingRequest("pending_snapshot_stats_invalid")
        if self.pair_count > PLAN_PAIR_LIMIT:
            raise ProcessingPlanTooLarge("processing_plan_pair_limit_exceeded")
        if self.item_count > PLAN_ITEM_LIMIT:
            raise ProcessingPlanTooLarge("processing_plan_item_limit_exceeded")
        if self.max_pair_item_count > PLAN_PAIR_ITEM_LIMIT:
            raise ProcessingPlanTooLarge("processing_plan_pair_item_limit_exceeded")
        if self.estimated_bytes > MAX_PLAN_TOTAL_BYTES:
            raise ProcessingPlanTooLarge("processing_plan_input_bytes_exceeded")


@dataclass(frozen=True, slots=True)
class ProcessingSpec:
    job_id: UUID
    mode: ProcessingMode
    channel: ProcessingChannel
    planning_state: PlanningState
    next_ordinal: int
    candidate_count: int
    planned_count: int
    applied_count: int
    deferred_count: int
    blocked_credit_count: int
    plan_hash: str | None
    planned_at: datetime | None

    @property
    def complete(self) -> bool:
        return (
            self.planning_state is PlanningState.PLANNED
            and self.applied_count == self.planned_count
        )


@dataclass(frozen=True, slots=True)
class PlannedPair:
    ordinal: int
    nr_pedido: int
    cd_prod_cor: str
    payload: Mapping[str, Any] = field(repr=False)
    payload_hash: str = ""
    payload_size: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not 1 <= self.ordinal <= PLAN_PAIR_LIMIT:
            raise InvalidProcessingRequest("plan_ordinal_out_of_range")
        if not 1 <= self.nr_pedido <= _INTEGER_MAX:
            raise InvalidProcessingRequest("plan_order_id_out_of_range")
        code = self.cd_prod_cor.strip()
        if not 1 <= len(code) <= MAX_PRODUCT_CODE_LENGTH or code != self.cd_prod_cor:
            raise InvalidProcessingRequest("plan_product_code_invalid")
        # Uma única serialização compacta materializa Decimal/datetime e fornece
        # os mesmos bytes do hash; só a medição JSONB spaced faz a 2ª passagem.
        encoded = _canonical_json(dict(self.payload))
        materialized = json.loads(encoded)
        persisted_size = _jsonb_text_size(materialized)
        if persisted_size > MAX_PLAN_PAYLOAD_BYTES:
            raise InvalidProcessingRequest("plan_payload_too_large")
        computed_hash = hashlib.sha256(encoded).hexdigest()
        if self.payload_hash and self.payload_hash != computed_hash:
            raise InvalidProcessingRequest("plan_payload_hash_mismatch")
        object.__setattr__(self, "payload", _freeze_mapping(materialized))
        object.__setattr__(self, "payload_hash", computed_hash)
        object.__setattr__(self, "payload_size", persisted_size)


@dataclass(frozen=True, slots=True)
class PlanDraft:
    rows: tuple[PlannedPair, ...]
    candidate_count: int
    deferred_count: int
    blocked_credit_count: int
    plan_hash: str
    # Grão par (nr_pedido, cd_prod_cor), não pedido. As arities diferem porque
    # o motivo varia por par: `deferred_pairs` carrega
    # (nr_pedido, cd_prod_cor, canal, motivo) já que furo_grade/sem_estoque
    # (plano 16-01) precisam de um motivo por linha; `blocked_credit_pairs`
    # carrega só (nr_pedido, cd_prod_cor, canal) porque o motivo é sempre
    # sem_credito — implícito, aplicado pelo plano 16-04 a partir da constante
    # SEM_CREDITO, não replicado em cada linha. Os defaults `()` existem para
    # retrocompatibilidade dos call sites diretos já na suíte
    # (test_pedidos_processing_repository.py), não porque ausência de pares
    # seja um estado esperado no caminho de build_processing_plan.
    deferred_pairs: tuple[tuple[int, str, str, str], ...] = ()
    blocked_credit_pairs: tuple[tuple[int, str, str], ...] = ()

    def __post_init__(self) -> None:
        if not 0 <= self.candidate_count <= PLAN_PAIR_LIMIT:
            raise InvalidProcessingRequest("candidate_count_out_of_range")
        if len(self.rows) > self.candidate_count:
            raise InvalidProcessingRequest("planned_count_exceeds_candidates")
        if not 0 <= self.deferred_count <= self.candidate_count:
            raise InvalidProcessingRequest("deferred_count_out_of_range")
        if not 0 <= self.blocked_credit_count <= self.candidate_count:
            raise InvalidProcessingRequest("blocked_credit_count_out_of_range")
        expected_ordinals = tuple(range(1, len(self.rows) + 1))
        if tuple(row.ordinal for row in self.rows) != expected_ordinals:
            raise InvalidProcessingRequest("plan_ordinals_not_contiguous")
        pairs = {(row.nr_pedido, row.cd_prod_cor) for row in self.rows}
        if len(pairs) != len(self.rows):
            raise InvalidProcessingRequest("plan_contains_duplicate_pair")
        if not _SHA256.fullmatch(self.plan_hash):
            raise InvalidProcessingRequest("invalid_plan_hash")
        if self.deferred_pairs and self.blocked_credit_pairs:
            deferred_keys = {pair[:2] for pair in self.deferred_pairs}
            blocked_keys = {pair[:2] for pair in self.blocked_credit_pairs}
            if deferred_keys & blocked_keys:
                raise InvalidProcessingRequest("standby_pair_in_both_groups")
        total_bytes = sum(row.payload_size for row in self.rows)
        if total_bytes > MAX_PLAN_TOTAL_BYTES:
            raise ProcessingPlanTooLarge("processing_plan_payload_bytes_exceeded")

    @property
    def planned_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    planned: int
    applied: int
    deferred: int
    blocked_credit: int

    def __post_init__(self) -> None:
        values = (self.planned, self.applied, self.deferred, self.blocked_credit)
        if any(value < 0 or value > PLAN_PAIR_LIMIT for value in values):
            raise InvalidProcessingRequest("processing_result_out_of_range")
        if self.applied > self.planned:
            raise InvalidProcessingRequest("processing_result_applied_exceeds_planned")

    def as_dict(self) -> dict[str, int]:
        return {
            "plannedCount": self.planned,
            "appliedCount": self.applied,
            "deferredCount": self.deferred,
            "blockedCreditCount": self.blocked_credit,
        }


def new_processing_job(
    *,
    owner_id: int,
    idempotency_key: str,
    request: ProcessingRequest,
    requested_at: datetime,
) -> NewJob:
    validate_idempotency_key(idempotency_key)
    return NewJob(
        kind=PROCESSING_JOB_KIND,
        owner_id=owner_id,
        scope_key=f"{request.mode.value}:{request.channel.value}",
        idempotency_digest=digest_idempotency_key(
            f"{PROCESSING_IDEMPOTENCY_NAMESPACE}:{idempotency_key}"
        ),
        fingerprint=fingerprint_payload(
            {
                "kind": PROCESSING_JOB_KIND,
                "schemaVersion": 1,
                "mode": request.mode.value,
                "channel": request.channel.value,
            }
        ),
        max_attempts=PROCESSING_MAX_ATTEMPTS,
        requested_at=requested_at,
        deadline_at=requested_at + PROCESSING_DEADLINE,
    )


def deterministic_completion_event_id(job_id: UUID) -> UUID:
    return uuid5(_EVENT_NAMESPACE, f"{PROCESSING_JOB_KIND}:{job_id}:completed")


def deterministic_alert_event_id(job_id: UUID) -> UUID:
    return uuid5(_EVENT_NAMESPACE, f"{PROCESSING_JOB_KIND}:{job_id}:alerts")


def deterministic_progress_event_id(job_id: UUID, applied_count: int) -> UUID:
    if not 1 <= applied_count <= PLAN_PAIR_LIMIT:
        raise InvalidProcessingRequest("processing_progress_event_out_of_range")
    return uuid5(
        _EVENT_NAMESPACE,
        f"{PROCESSING_JOB_KIND}:{job_id}:progress:{applied_count}",
    )


def _pair_channel(items: Sequence[Mapping[str, Any]]) -> ProcessingChannel | None:
    channels = {normalizar_canal(item.get("canal")) for item in items}
    if len(channels) != 1:
        return None
    channel = channels.pop()
    if channel is None:
        return None
    return ProcessingChannel(channel)


def _eligible_pairs(
    items: Iterable[Mapping[str, Any]],
    requested_channel: ProcessingChannel,
    *,
    cancel_token: CancellationToken | None = None,
) -> tuple[dict[tuple[int, str], list[Mapping[str, Any]]], int]:
    # Mantém referências ao snapshot já materializado pelo adapter. Copiar cada
    # dict aqui duplicava centenas de milhares de linhas antes mesmo de congelar
    # os payloads; PlannedPair fará a única cópia JSON necessária.
    grouped: dict[tuple[int, str], list[Mapping[str, Any]]] = defaultdict(list)
    for raw in items:
        _ensure_not_cancelled(cancel_token)
        try:
            nr = int(raw["nr_pedido"])
            code = str(raw["cd_prod_cor"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise InvalidProcessingRequest("invalid_pending_item_identity") from exc
        if not 1 <= nr <= _INTEGER_MAX or not 1 <= len(code) <= MAX_PRODUCT_CODE_LENGTH:
            raise InvalidProcessingRequest("invalid_pending_item_identity")
        grouped[(nr, code)].append(raw)
    if len(grouped) > PLAN_PAIR_LIMIT:
        raise ProcessingPlanTooLarge("processing_plan_pair_limit_exceeded")

    eligible: dict[tuple[int, str], list[Mapping[str, Any]]] = {}
    for pair, pair_items in sorted(grouped.items()):
        _ensure_not_cancelled(cancel_token)
        channel = _pair_channel(pair_items)
        if channel is None:
            raise InvalidProcessingRequest("pending_pair_channel_is_not_canonical")
        if (
            requested_channel is not ProcessingChannel.TODOS
            and channel is not requested_channel
        ):
            continue
        eligible[pair] = pair_items
    return eligible, 0


def fingerprint_pair_source(
    items: Sequence[Mapping[str, Any]],
    *,
    cancel_token: CancellationToken | None = None,
) -> str:
    """Fingerprint da foto original para detectar plano obsoleto após crash."""

    copied: list[dict[str, Any]] = []
    for item in items:
        _ensure_not_cancelled(cancel_token)
        copied.append(dict(item))
    ordered = sorted(
        copied,
        key=lambda item: (
            str(item.get("sg_tamanho") or ""),
            int(item.get("nr_pedido") or 0),
            str(item.get("cd_prod_cor") or ""),
        ),
    )
    return _sha256(ordered)


def _payload_for_pair(
    *,
    pair: tuple[int, str],
    items: Sequence[Mapping[str, Any]],
    source_items: Sequence[Mapping[str, Any]],
    tipo: str,
    reference: Mapping[str, Mapping[str, int]],
    cancel_token: CancellationToken | None = None,
) -> dict[str, Any]:
    _ensure_not_cancelled(cancel_token)
    nr, code = pair
    channel = _pair_channel(items)
    if channel is None:
        raise InvalidProcessingRequest("planned_pair_channel_is_not_canonical")
    payload_items = [dict(item) for item in items]
    linx = montar_linha_linx(
        nr,
        code,
        payload_items,
        dict(reference.get(code, {})),
        tipo=tipo,
    )
    targets = sorted(
        {
            (code, str(item.get("sg_tamanho") or "").strip().upper(), channel.value)
            for item in payload_items
            if item.get("status_item") != "Pedido em Stand By"
            and int(item.get("qt_liquida") or 0) > 0
            and 1 <= len(str(item.get("sg_tamanho") or "").strip()) <= MAX_SIZE_LENGTH
        }
    )
    return {
        "type": tipo,
        "channel": channel.value,
        "items": payload_items,
        "sourceHash": fingerprint_pair_source(
            source_items,
            cancel_token=cancel_token,
        ),
        "linx": linx,
        "stockTargets": [list(target) for target in targets],
    }


def build_processing_plan(
    *,
    request: ProcessingRequest,
    pending_items: Sequence[Mapping[str, Any]],
    reference: Mapping[str, Mapping[str, int]],
    stock: Mapping[str, Mapping[str, int]] | None = None,
    criterion: str = "valor",
    tolerance: float = 0.05,
    cancel_token: CancellationToken | None = None,
    total_original_por_pedido: Mapping[int, int] | None = None,
    consumido_previo_adicao_por_pedido: Mapping[int, int] | None = None,
    consumido_previo_corte_por_pedido: Mapping[int, int] | None = None,
) -> PlanDraft:
    """Congela uma seleção determinística; nenhum acesso externo ocorre aqui."""

    eligible, invalid_count = _eligible_pairs(
        pending_items,
        request.channel,
        cancel_token=cancel_token,
    )
    candidate_count = len(eligible) + invalid_count
    deferred_count = invalid_count

    # Estoque é exigido nos dois modos: SEM_ADEQUAR agora reserva de verdade
    # (tudo-ou-nada) tanto quanto ADEQUAR reserva com tolerância. A guarda
    # roda depois de `_eligible_pairs` de propósito — validação de canal tem
    # precedência sobre a de estoque (ver `test_mixed_channel_pair_fails_...`).
    if stock is None:
        raise InvalidProcessingRequest("adequation_requires_stock_snapshot")

    modo = (
        ModoAdequacao.ADEQUAR
        if request.mode is ProcessingMode.ADEQUAR
        else ModoAdequacao.SEM_ADEQUAR
    )
    tipo = "com" if request.mode is ProcessingMode.ADEQUAR else "sem"

    flat: list[Mapping[str, Any]] = []
    channel_by_pair: dict[tuple[int, str], str] = {}
    for pair in sorted(eligible):
        _ensure_not_cancelled(cancel_token)
        pair_channel = _pair_channel(eligible[pair])
        if pair_channel is None:
            raise InvalidProcessingRequest("pending_pair_channel_is_not_canonical")
        channel_by_pair[pair] = pair_channel.value
        for item in eligible[pair]:
            _ensure_not_cancelled(cancel_token)
            flat.append(item)
    try:
        # Decisão do usuário: o orçamento é lido e repassado nos DOIS modos,
        # mesmo que SEM_ADEQUAR não toque o ledger (`_resolver_orcamento_pedido`
        # só roda no ramo ADEQUAR do motor). O custo é uma query agregada por
        # execução; o ganho é eliminar a classe de bug "esqueceram de ligar o
        # orçamento neste caminho" — nunca existe um modo onde o wiring falta.
        result = processar_pedidos(
            flat,
            {channel: dict(values) for channel, values in stock.items()},
            set(),
            criterion,
            tolerance,
            modo=modo,
            resultados_apenas_selecionados=True,
            cancel_token=cancel_token,
            total_original_por_pedido=total_original_por_pedido,
            consumido_previo_adicao_por_pedido=consumido_previo_adicao_por_pedido,
            consumido_previo_corte_por_pedido=consumido_previo_corte_por_pedido,
        )
    except PlanningCancellationRequested as exc:
        raise ProcessingPlanningCancelled("processing_planning_cancelled") from exc
    selected_pairs = sorted(result["pares_processados"])
    selected: dict[tuple[int, str], Sequence[Mapping[str, Any]]] = {
        pair: result["resultados"][pair] for pair in selected_pairs
    }
    deferred_count += len(result["preteridos"])
    bloqueados_credito_set = set(result["bloqueados_credito"])
    blocked_credit_count = len(bloqueados_credito_set)

    deferred_pairs_list: list[tuple[int, str, str, str]] = []
    for pair in sorted(result["preteridos"]):
        if pair not in channel_by_pair:
            raise InvalidProcessingRequest("standby_pair_not_eligible")
        canal = channel_by_pair[pair]
        # Acesso tolerante com fallback para SEM_ESTOQUE: o plano 16-01 garante
        # len(preteridos) == len(preteridos_motivo) e testa isso nos 4 pontos
        # de escrita do motor, então esta falta é impossível hoje. Se um
        # chamador futuro quebrar essa invariante, o custo de rotular o par
        # como sem_estoque (rótulo de UI degradado) é estritamente menor que
        # abortar a geração de OR da rodada inteira.
        motivo = result["preteridos_motivo"].get(pair, SEM_ESTOQUE)
        deferred_pairs_list.append((pair[0], pair[1], canal, motivo))
    deferred_pairs = tuple(deferred_pairs_list)

    # O fan-out por produto elegível (não pelos que apareceriam no plano)
    # existe porque crédito é do CLIENTE, não do produto — o pedido inteiro
    # fica parado, então todos os produtos que ele tentou reservar nesta
    # rodada precisam da tag (K2 de 16-VALIDATION.md, insumo direto de
    # STANDBY-02 na Phase 17). Iterar `sorted(eligible)` em vez do `set`
    # mantém a ordem determinística que o plan_hash exige.
    blocked_credit_pairs = tuple(
        (pair[0], pair[1], channel_by_pair[pair])
        for pair in sorted(eligible)
        if pair[0] in bloqueados_credito_set
    )

    rows: list[PlannedPair] = []
    cumulative_payload_bytes = 0
    for ordinal, pair in enumerate(sorted(selected), start=1):
        _ensure_not_cancelled(cancel_token)
        planned = PlannedPair(
            ordinal=ordinal,
            nr_pedido=pair[0],
            cd_prod_cor=pair[1],
            payload=_payload_for_pair(
                pair=pair,
                items=selected[pair],
                source_items=eligible[pair],
                tipo=tipo,
                reference=reference,
                cancel_token=cancel_token,
            ),
        )
        cumulative_payload_bytes += planned.payload_size
        if cumulative_payload_bytes > MAX_PLAN_TOTAL_BYTES:
            raise ProcessingPlanTooLarge("processing_plan_payload_bytes_exceeded")
        rows.append(planned)
    hash_rows: list[list[int | str]] = []
    for row in rows:
        _ensure_not_cancelled(cancel_token)
        hash_rows.append(
            [row.ordinal, row.nr_pedido, row.cd_prod_cor, row.payload_hash]
        )
    plan_hash = _sha256(
        {
            # Bump de 1 para 2: a fórmula do plan_hash mudou de forma com
            # deferredPairs/blockedCreditPairs. Documenta, para quem depurar
            # um ProcessingPlanConflict, que um job planejado antes deste
            # deploy e replanejado depois produz hash diferente.
            "schemaVersion": 2,
            "mode": request.mode.value,
            "channel": request.channel.value,
            "candidateCount": candidate_count,
            "deferredCount": deferred_count,
            "blockedCreditCount": blocked_credit_count,
            "rows": hash_rows,
            "deferredPairs": [list(pair) for pair in deferred_pairs],
            "blockedCreditPairs": [list(pair) for pair in blocked_credit_pairs],
        }
    )
    return PlanDraft(
        rows=tuple(rows),
        candidate_count=candidate_count,
        deferred_count=deferred_count,
        blocked_credit_count=blocked_credit_count,
        deferred_pairs=deferred_pairs,
        blocked_credit_pairs=blocked_credit_pairs,
        plan_hash=plan_hash,
    )


__all__ = [
    "APPLY_CHUNK_SIZE",
    "HARD_MAX_PLAN_TOTAL_BYTES",
    "MAX_PLAN_PAYLOAD_BYTES",
    "MAX_PLAN_TOTAL_BYTES",
    "PLAN_ITEM_LIMIT",
    "PLAN_PAIR_ITEM_LIMIT",
    "PLAN_PAIR_LIMIT",
    "PROCESSING_DEADLINE",
    "PROCESSING_JOB_KIND",
    "PROCESSING_LEASE",
    "PROCESSING_MAX_ATTEMPTS",
    "PROCESSING_RETRY_DELAY",
    "STOCK_TARGET_CHUNK_SIZE",
    "InvalidProcessingRequest",
    "PendingSnapshotStats",
    "PlanDraft",
    "PlanRowState",
    "PlannedPair",
    "PlanningState",
    "ProcessingChannel",
    "ProcessingError",
    "ProcessingLeaseLost",
    "ProcessingLockUnavailable",
    "ProcessingMode",
    "ProcessingPlanConflict",
    "ProcessingPlanTooLarge",
    "ProcessingPlanningCancelled",
    "ProcessingRequest",
    "ProcessingResult",
    "ProcessingSpec",
    "build_processing_plan",
    "deterministic_alert_event_id",
    "deterministic_completion_event_id",
    "deterministic_progress_event_id",
    "fingerprint_pair_source",
    "new_processing_job",
    "validate_idempotency_key",
]
