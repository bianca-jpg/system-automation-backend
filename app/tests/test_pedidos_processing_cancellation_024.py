from __future__ import annotations

import asyncio
from threading import Event

import pytest

from app.modules.pedidos.domain import motor_adequacao as motor
from app.modules.pedidos.processing import domain as processing_domain
from app.modules.pedidos.processing.domain import (
    ProcessingChannel,
    ProcessingMode,
    ProcessingPlanningCancelled,
    ProcessingRequest,
    build_processing_plan,
)


def _item(order_id: int, code: str) -> dict[str, object]:
    return {
        "nr_pedido": order_id,
        "cd_prod_cor": code,
        "sg_tamanho": "36",
        "ds_grupo": "GRUPO",
        "qt_liquida": 2,
        "vl_liquido": 40.0,
        "status_credito": "Com Crédito",
        "client": "dado-que-nao-pode-vazar",
        "canal": "Franquia",
        "ds_produto": "Produto",
        "data": "2026-08-08",
    }


@pytest.mark.asyncio
async def test_cpu_planning_cancel_stops_thread_before_remaining_products(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product_count = 200
    pending_items = [
        _item(order_id, f"PROD-{order_id:04d}")
        for order_id in range(1, product_count + 1)
    ]
    reference = {str(item["cd_prod_cor"]): {"36": 1} for item in pending_items}
    stock = {"Franquia": {f"{item['cd_prod_cor']}_36": 10 for item in pending_items}}
    cancel = Event()
    entered_slow_grade = Event()
    release_slow_grade = Event()
    thread_exited = Event()
    processed_products: list[str] = []
    original_grade = motor.adequar_grade_produto

    def _slow_grade(
        items: list,
        available_stock: dict,
        product_code: str,
        ledger: motor.OrcamentoPedido,
        *,
        cancel_token: motor.CancellationToken | None = None,
    ) -> list:
        processed_products.append(product_code)
        if len(processed_products) == 1:
            entered_slow_grade.set()
            if not release_slow_grade.wait(timeout=2):
                raise AssertionError("test_gate_timeout")
        return original_grade(
            items,
            available_stock,
            product_code,
            ledger,
            cancel_token=cancel_token,
        )

    monkeypatch.setattr(motor, "adequar_grade_produto", _slow_grade)

    def _operation() -> object:
        try:
            return build_processing_plan(
                request=ProcessingRequest(
                    mode=ProcessingMode.ADEQUAR,
                    channel=ProcessingChannel.FRANQUIA,
                ),
                pending_items=pending_items,
                reference=reference,
                stock=stock,
                cancel_token=cancel,
            )
        finally:
            thread_exited.set()

    task = asyncio.create_task(asyncio.to_thread(_operation))
    assert await asyncio.to_thread(entered_slow_grade.wait, 2)
    cancel.set()
    release_slow_grade.set()

    with pytest.raises(
        ProcessingPlanningCancelled,
        match="^processing_planning_cancelled$",
    ):
        await asyncio.wait_for(task, timeout=2)

    assert thread_exited.is_set()
    assert task.done()
    assert len(processed_products) == 1
    assert len(processed_products) < product_count // 10


def test_callable_cancel_token_is_checked_between_payload_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = False
    payload_calls = 0
    original_payload = processing_domain._payload_for_pair

    def _cancel_token() -> bool:
        return cancelled

    def _payload(*args, **kwargs):
        nonlocal cancelled, payload_calls
        result = original_payload(*args, **kwargs)
        payload_calls += 1
        cancelled = True
        return result

    monkeypatch.setattr(processing_domain, "_payload_for_pair", _payload)

    with pytest.raises(
        ProcessingPlanningCancelled,
        match="^processing_planning_cancelled$",
    ):
        build_processing_plan(
            request=ProcessingRequest(
                mode=ProcessingMode.SEM_ADEQUAR,
                channel=ProcessingChannel.FRANQUIA,
            ),
            pending_items=[_item(1, "PROD-0001"), _item(2, "PROD-0002")],
            reference={"PROD-0001": {"36": 1}, "PROD-0002": {"36": 1}},
            stock={"Franquia": {"PROD-0001_36": 2, "PROD-0002_36": 2}},
            cancel_token=_cancel_token,
        )

    assert payload_calls == 1
