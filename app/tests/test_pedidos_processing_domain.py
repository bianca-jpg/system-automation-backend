from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.modules.pedidos.processing import domain as processing_domain
from app.modules.pedidos.processing.domain import (
    HARD_MAX_PLAN_TOTAL_BYTES,
    MAX_PLAN_PAYLOAD_BYTES,
    MAX_PLAN_TOTAL_BYTES,
    PLAN_ITEM_LIMIT,
    PLAN_PAIR_LIMIT,
    InvalidProcessingRequest,
    PendingSnapshotStats,
    PlanDraft,
    PlannedPair,
    ProcessingChannel,
    ProcessingMode,
    ProcessingPlanTooLarge,
    ProcessingRequest,
    ProcessingResult,
    build_processing_plan,
    new_processing_job,
    validate_idempotency_key,
)
from app.shared.jobs.domain import digest_idempotency_key


def _item(
    order_id: int,
    code: str,
    *,
    channel: str = "Franquia",
    size: str = "36",
    qty: int = 2,
    value: float = 40.0,
    status_credito: str = "Com Crédito",
) -> dict:
    return {
        "nr_pedido": order_id,
        "cd_prod_cor": code,
        "sg_tamanho": size,
        "ds_grupo": "GRUPO",
        "qt_liquida": qty,
        "vl_liquido": value,
        "status_credito": status_credito,
        "client": f"Cliente {order_id}",
        "canal": channel,
        "ds_produto": f"Produto {code}",
        "data": "2026-08-08",
    }


def test_idempotency_namespace_scope_and_public_result_contract() -> None:
    request = ProcessingRequest(
        mode=ProcessingMode.ADEQUAR,
        channel=ProcessingChannel.FRANQUIA,
    )
    job = new_processing_job(
        owner_id=42,
        idempotency_key="safe-key-123",
        request=request,
        requested_at=datetime(2026, 8, 8, tzinfo=UTC),
    )

    assert job.scope_key == "adequar:Franquia"
    assert job.idempotency_digest == digest_idempotency_key(
        "orders.processing:safe-key-123"
    )
    assert ProcessingResult(4, 3, 1, 1).as_dict() == {
        "plannedCount": 4,
        "appliedCount": 3,
        "deferredCount": 1,
        "blockedCreditCount": 1,
    }


@pytest.mark.parametrize(
    "value",
    [
        "short",
        " leading-space",
        "contains space",
        "contains/slash",
        "a" * 129,
        "ábcdefgh",
    ],
)
def test_idempotency_key_rejects_non_contract_values(value: str) -> None:
    with pytest.raises(InvalidProcessingRequest, match="invalid_idempotency_key"):
        validate_idempotency_key(value)


def test_exact_channel_excludes_other_channel_without_counting_deferred() -> None:
    draft = build_processing_plan(
        request=ProcessingRequest(
            mode=ProcessingMode.SEM_ADEQUAR,
            channel=ProcessingChannel.FRANQUIA,
        ),
        pending_items=[
            _item(1, "PROD", channel="Franquia"),
            _item(2, "PROD", channel="Multimarca"),
        ],
        reference={"PROD": {"36": 1}},
        stock={"Franquia": {"PROD_36": 2}},
    )

    assert draft.candidate_count == 1
    assert draft.deferred_count == 0
    assert [(row.nr_pedido, row.cd_prod_cor) for row in draft.rows] == [(1, "PROD")]
    assert draft.rows[0].payload["stockTargets"] == [["PROD", "36", "Franquia"]]


@pytest.mark.parametrize("channel", list(ProcessingChannel))
def test_mixed_channel_pair_fails_closed_for_every_scope(
    channel: ProcessingChannel,
) -> None:
    with pytest.raises(
        InvalidProcessingRequest,
        match="pending_pair_channel_is_not_canonical",
    ):
        build_processing_plan(
            request=ProcessingRequest(
                mode=ProcessingMode.SEM_ADEQUAR,
                channel=channel,
            ),
            pending_items=[
                _item(1, "PROD", channel="Franquia", size="36"),
                _item(1, "PROD", channel="Multimarca", size="37"),
            ],
            reference={"PROD": {"36": 1, "37": 2}},
        )


def test_plan_preserves_numeric_sizes_and_freezes_linx_and_source_hash() -> None:
    draft = build_processing_plan(
        request=ProcessingRequest(
            mode=ProcessingMode.SEM_ADEQUAR,
            channel=ProcessingChannel.TODOS,
        ),
        pending_items=[
            _item(7, "SKU|01", size="36", qty=2, value=40),
            _item(7, "SKU|01", size="37", qty=3, value=60),
        ],
        reference={"SKU|01": {"36": 1, "37": 2}},
        stock={"Franquia": {"SKU|01_36": 2, "SKU|01_37": 3}},
    )
    row = draft.rows[0]

    assert [item["sg_tamanho"] for item in row.payload["items"]] == ["36", "37"]
    assert row.payload["linx"]["e1"] == 2
    assert row.payload["linx"]["e2"] == 3
    assert row.payload["linx"]["qtde_embalada"] == 5
    assert len(row.payload["sourceHash"]) == 64
    assert row.payload_size < 128 * 1024


def test_sem_adequar_now_defers_by_stock_and_blocks_by_credit() -> None:
    """J1/J2/J3 no domínio puro: `SEM_ADEQUAR` deixa de aceitar todo par
    elegível incondicionalmente. Um par sem crédito (J1) e um par com grade
    incompleta/estoque insuficiente (J2) não entram em `rows`; os dois
    contadores refletem o resultado real do motor (J3), não constantes."""

    draft = build_processing_plan(
        request=ProcessingRequest(
            mode=ProcessingMode.SEM_ADEQUAR,
            channel=ProcessingChannel.FRANQUIA,
        ),
        pending_items=[
            _item(1, "A", qty=2),
            _item(2, "B", qty=2),
            _item(3, "C", qty=2, status_credito="Sem Crédito"),
        ],
        reference={"A": {"36": 1}, "B": {"36": 1}, "C": {"36": 1}},
        stock={"Franquia": {"A_36": 2, "B_36": 0, "C_36": 5}},
    )

    assert draft.candidate_count == 3
    assert [(row.nr_pedido, row.cd_prod_cor) for row in draft.rows] == [(1, "A")]
    assert draft.deferred_count == 1
    assert draft.blocked_credit_count == 1


def test_plan_draft_rejects_pair_in_deferred_and_blocked_credit() -> None:
    """Um pedido sem crédito já é excluído de `ordens_credito` antes do laço
    que gera `preteridos` (`motor_adequacao.py:687`), então este cenário não
    deveria ser estruturalmente alcançável a partir de `build_processing_plan`.
    A guarda existe para documentar a invariante e pegar regressão futura — é
    ela que garante que o INSERT único do plano 16-04 nunca colide com
    "ON CONFLICT DO UPDATE command cannot affect row a second time"."""

    with pytest.raises(InvalidProcessingRequest, match="standby_pair_in_both_groups"):
        PlanDraft(
            rows=(),
            candidate_count=1,
            deferred_count=1,
            blocked_credit_count=1,
            plan_hash="a" * 64,
            deferred_pairs=((1, "A", "Franquia", "sem_estoque"),),
            blocked_credit_pairs=((1, "A", "Franquia"),),
        )


def test_plan_draft_accepts_disjoint_standby_groups_and_freezes_them() -> None:
    draft = PlanDraft(
        rows=(),
        candidate_count=2,
        deferred_count=1,
        blocked_credit_count=1,
        plan_hash="a" * 64,
        deferred_pairs=((2, "B", "Franquia", "furo_grade"),),
        blocked_credit_pairs=((3, "C", "Multimarca"),),
    )

    assert draft.deferred_pairs == ((2, "B", "Franquia", "furo_grade"),)
    assert draft.blocked_credit_pairs == ((3, "C", "Multimarca"),)
    assert isinstance(draft.deferred_pairs, tuple)
    assert isinstance(draft.blocked_credit_pairs, tuple)


def test_plan_draft_standby_groups_default_to_empty_for_existing_call_sites() -> None:
    draft = PlanDraft(
        rows=(),
        candidate_count=0,
        deferred_count=0,
        blocked_credit_count=0,
        plan_hash="b" * 64,
    )

    assert draft.deferred_pairs == ()
    assert draft.blocked_credit_pairs == ()


def test_plan_captures_deferred_motivo_per_pair_and_credit_fan_out_per_product() -> (
    None
):
    draft = build_processing_plan(
        request=ProcessingRequest(
            mode=ProcessingMode.ADEQUAR,
            channel=ProcessingChannel.FRANQUIA,
        ),
        pending_items=[
            _item(1, "OK", size="36", qty=2),
            _item(2, "FURO", size="PP", qty=1),
            _item(2, "FURO", size="M", qty=1),
            _item(2, "FURO", size="G", qty=1),
            _item(3, "SEMEST", size="36", qty=2),
            _item(4, "C1", size="36", qty=2, status_credito="Sem Crédito"),
            _item(4, "C2", size="36", qty=2, status_credito="Sem Crédito"),
        ],
        reference={
            "OK": {"36": 1},
            "FURO": {"PP": 1, "M": 2, "G": 3},
            "SEMEST": {"36": 1},
            "C1": {"36": 1},
            "C2": {"36": 1},
        },
        stock={
            "Franquia": {
                "OK_36": 2,
                "FURO_PP": 5,
                "FURO_M": 0,
                "FURO_G": 5,
                "SEMEST_36": 0,
                "C1_36": 5,
                "C2_36": 5,
            }
        },
    )

    assert [(row.nr_pedido, row.cd_prod_cor) for row in draft.rows] == [(1, "OK")]
    assert draft.deferred_pairs == (
        (2, "FURO", "Franquia", "furo_grade"),
        (3, "SEMEST", "Franquia", "sem_estoque"),
    )
    assert draft.deferred_pairs[0][3] != draft.deferred_pairs[1][3]
    assert draft.blocked_credit_pairs == (
        (4, "C1", "Franquia"),
        (4, "C2", "Franquia"),
    )
    assert draft.blocked_credit_count == 1
    assert len(draft.blocked_credit_pairs) == 2
    assert len(draft.deferred_pairs) == draft.deferred_count == 2
    planned_pairs = {(row.nr_pedido, row.cd_prod_cor) for row in draft.rows}
    for pair in draft.blocked_credit_pairs:
        assert (pair[0], pair[1]) not in planned_pairs


def test_plan_hash_is_sensitive_to_standby_pairs_and_deterministic() -> None:
    draft_a = build_processing_plan(
        request=ProcessingRequest(
            mode=ProcessingMode.SEM_ADEQUAR,
            channel=ProcessingChannel.FRANQUIA,
        ),
        pending_items=[_item(1, "A", qty=2), _item(2, "B", qty=2)],
        reference={"A": {"36": 1}, "B": {"36": 1}},
        stock={"Franquia": {"A_36": 2, "B_36": 0}},
    )
    draft_b = build_processing_plan(
        request=ProcessingRequest(
            mode=ProcessingMode.SEM_ADEQUAR,
            channel=ProcessingChannel.FRANQUIA,
        ),
        pending_items=[_item(1, "A", qty=2), _item(3, "C", qty=2)],
        reference={"A": {"36": 1}, "C": {"36": 1}},
        stock={"Franquia": {"A_36": 2, "C_36": 0}},
    )

    assert [
        (row.ordinal, row.nr_pedido, row.cd_prod_cor, row.payload_hash)
        for row in draft_a.rows
    ] == [
        (row.ordinal, row.nr_pedido, row.cd_prod_cor, row.payload_hash)
        for row in draft_b.rows
    ]
    assert draft_a.candidate_count == draft_b.candidate_count == 2
    assert draft_a.deferred_count == draft_b.deferred_count == 1
    assert draft_a.blocked_credit_count == draft_b.blocked_credit_count == 0

    assert draft_a.deferred_pairs != draft_b.deferred_pairs
    assert draft_a.plan_hash != draft_b.plan_hash

    draft_a_again = build_processing_plan(
        request=ProcessingRequest(
            mode=ProcessingMode.SEM_ADEQUAR,
            channel=ProcessingChannel.FRANQUIA,
        ),
        pending_items=[_item(1, "A", qty=2), _item(2, "B", qty=2)],
        reference={"A": {"36": 1}, "B": {"36": 1}},
        stock={"Franquia": {"A_36": 2, "B_36": 0}},
    )
    assert draft_a_again.plan_hash == draft_a.plan_hash


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        (
            {"pair_count": PLAN_PAIR_LIMIT + 1, "item_count": 1, "estimated_bytes": 1},
            "pair_limit",
        ),
        (
            {"pair_count": 1, "item_count": PLAN_ITEM_LIMIT + 1, "estimated_bytes": 1},
            "item_limit",
        ),
        (
            {
                "pair_count": 1,
                "item_count": 1,
                "estimated_bytes": MAX_PLAN_TOTAL_BYTES + 1,
            },
            "input_bytes",
        ),
    ],
)
def test_preflight_hard_caps_fail_before_hydration(kwargs: dict, error: str) -> None:
    with pytest.raises(ProcessingPlanTooLarge, match=error):
        PendingSnapshotStats(**kwargs)


def test_operational_payload_cap_stays_below_contract_hard_cap() -> None:
    assert MAX_PLAN_TOTAL_BYTES == 96 * 1024 * 1024
    assert MAX_PLAN_TOTAL_BYTES < HARD_MAX_PLAN_TOTAL_BYTES
    assert HARD_MAX_PLAN_TOTAL_BYTES == 256 * 1024 * 1024


def test_preflight_rejects_oversized_single_pair_before_hydration() -> None:
    with pytest.raises(ProcessingPlanTooLarge, match="pair_item_limit"):
        PendingSnapshotStats(
            pair_count=1,
            item_count=101,
            estimated_bytes=1,
            max_pair_item_count=101,
        )


def test_plan_aborts_during_build_when_cumulative_payload_exceeds_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    single = build_processing_plan(
        request=ProcessingRequest(
            mode=ProcessingMode.SEM_ADEQUAR,
            channel=ProcessingChannel.FRANQUIA,
        ),
        pending_items=[_item(1, "A")],
        reference={"A": {"36": 1}},
        stock={"Franquia": {"A_36": 2, "B_36": 2}},
    )
    monkeypatch.setattr(
        processing_domain,
        "MAX_PLAN_TOTAL_BYTES",
        single.rows[0].payload_size + 1,
    )

    with pytest.raises(
        ProcessingPlanTooLarge,
        match="processing_plan_payload_bytes_exceeded",
    ):
        build_processing_plan(
            request=ProcessingRequest(
                mode=ProcessingMode.SEM_ADEQUAR,
                channel=ProcessingChannel.FRANQUIA,
            ),
            pending_items=[_item(1, "A"), _item(2, "B")],
            reference={"A": {"36": 1}, "B": {"36": 1}},
            stock={"Franquia": {"A_36": 2, "B_36": 2}},
        )


def test_payload_limit_uses_same_spaced_json_text_size_as_jsonb() -> None:
    empty_size = len(b'{"x": ""}')
    boundary = "x" * (MAX_PLAN_PAYLOAD_BYTES - empty_size)

    accepted = PlannedPair(
        ordinal=1,
        nr_pedido=1,
        cd_prod_cor="PROD",
        payload={"x": boundary},
    )
    assert accepted.payload_size == MAX_PLAN_PAYLOAD_BYTES

    with pytest.raises(InvalidProcessingRequest, match="plan_payload_too_large"):
        PlannedPair(
            ordinal=1,
            nr_pedido=1,
            cd_prod_cor="PROD",
            payload={"x": f"{boundary}x"},
        )
