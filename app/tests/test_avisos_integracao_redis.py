"""Repositório Redis + fachada best-effort dos avisos de integração (Task 2)."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from redis.exceptions import RedisError

from app.modules.health.domain.avisos import FonteIntegracao
from app.modules.health.service import (
    listar_linhas_de_alerta,
    registrar_falha_integracao,
    registrar_sucesso_integracao,
)
from app.shared.infrastructure.redis_client import close_redis, get_redis


@pytest.fixture(autouse=True)
async def _limpar_chaves_de_aviso():
    """Chave vazada polui `test_pedidos_read_projection.py`, que itera as
    linhas de alerta — por isso a limpeza acontece antes E depois.

    `close_redis()` no fim de cada teste evita o mesmo pitfall já documentado
    para o engine do Postgres em `conftest.py::_dispose_db_engine_after_test`:
    o cliente Redis é um singleton global preso ao event loop que o criou, e
    pytest-asyncio cria um loop novo por teste — sem fechar o pool, o próximo
    teste herdaria uma conexão presa a um loop já encerrado."""

    chaves = [f"automation:aviso-integracao:{fonte.value}" for fonte in FonteIntegracao]
    redis = get_redis()
    await redis.delete(*chaves)
    yield
    redis = get_redis()
    await redis.delete(*chaves)
    await close_redis()


async def test_uma_falha_gera_uma_linha_com_categoria_do_catalogo():
    await registrar_falha_integracao(FonteIntegracao.BANCO_DE_DADOS)

    linhas = await listar_linhas_de_alerta()

    assert len(linhas) == 1
    assert linhas[0]["category"] == "integracao_banco_de_dados"
    assert linhas[0]["kind"] == "integracao"
    assert linhas[0]["affected_count"] == 1


async def test_tres_falhas_na_mesma_fonte_geram_uma_unica_linha_com_contador_3():
    await registrar_falha_integracao(FonteIntegracao.ESTOQUE)
    await registrar_falha_integracao(FonteIntegracao.ESTOQUE)
    await registrar_falha_integracao(FonteIntegracao.ESTOQUE)

    linhas = await listar_linhas_de_alerta()

    assert len(linhas) == 1
    assert linhas[0]["affected_count"] == 3


async def test_sucesso_depois_de_falha_remove_a_linha():
    await registrar_falha_integracao(FonteIntegracao.BANCO_DE_DADOS)
    await registrar_sucesso_integracao(FonteIntegracao.BANCO_DE_DADOS)

    linhas = await listar_linhas_de_alerta()

    assert linhas == []


async def test_sucesso_sem_aviso_aberto_nao_escreve_nem_falha():
    await registrar_sucesso_integracao(FonteIntegracao.ESTOQUE)

    linhas = await listar_linhas_de_alerta()

    assert linhas == []


async def test_log_estruturado_contem_fonte_severidade_ocorrencias_e_detalhe(caplog):
    with caplog.at_level(logging.WARNING, logger="app.avisos.integracao"):
        await registrar_falha_integracao(
            FonteIntegracao.BANCO_DE_DADOS, detalhe_tecnico="ConnectionError"
        )

    assert "fonte=banco_de_dados" in caplog.text
    assert "severidade=error" in caplog.text
    assert "ocorrencias=" in caplog.text
    assert "ConnectionError" in caplog.text


async def test_redis_indisponivel_nao_propaga_excecao_e_lista_vazia():
    """`pipeline()` no redis-py é síncrono (só enfileira comandos); a falha de
    rede acontece em `execute()`, que é async — o double precisa refletir essa
    forma real, senão o mock não exercitaria o caminho de erro de verdade."""

    mock_pipeline = MagicMock()
    mock_pipeline.hgetall.return_value = mock_pipeline
    mock_pipeline.delete.return_value = mock_pipeline
    mock_pipeline.hset.return_value = mock_pipeline
    mock_pipeline.expire.return_value = mock_pipeline
    mock_pipeline.execute = AsyncMock(side_effect=RedisError("redis down"))

    redis_com_falha = AsyncMock()
    redis_com_falha.hgetall.side_effect = RedisError("redis down")
    redis_com_falha.pipeline = MagicMock(return_value=mock_pipeline)

    with patch(
        "app.modules.health.service.get_redis", return_value=redis_com_falha
    ):
        await registrar_falha_integracao(FonteIntegracao.BANCO_DE_DADOS)
        await registrar_sucesso_integracao(FonteIntegracao.BANCO_DE_DADOS)
        linhas = await listar_linhas_de_alerta()

    assert linhas == []
