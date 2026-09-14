"""Etapa 6 (Integração de Dados): ingestao/api_leitura.py é a superfície de
leitura cross-context que `pedidos` consome via
`repositorio_ingestao_readmodel.py`. Estes testes só LEEM (nenhum delete/insert)
e cobrem o contrato de cada projeção.

⚠️ Dívida conhecida: os dois testes de "equivalência" com `pedidos_service`
(`carregar_estoque_fisico`, `carregar_processados_erp`) são tautológicos — o
nome do lado esquerdo é apenas um `import ... as` do lado direito."""

from app.modules.ingestao.infrastructure.http import api_leitura
from app.modules.ingestao.infrastructure.models import ProdutoTamanhoPosicao
from app.modules.pedidos import service as pedidos_service
from app.shared.database.session import async_session_factory


def _normalizar_processados_erp(dic: dict[int, dict]) -> dict[int, dict]:
    normalizado = {}
    for nr, info in dic.items():
        copia = dict(info)
        copia["items"] = sorted(
            info["items"], key=lambda i: (i["cd_prod_cor"], i["sg_tamanho"])
        )
        normalizado[nr] = copia
    return normalizado


async def test_obter_estoque_por_canal_equivale_a_pedidos_service():
    """Paridade da FOTO CRUA. `pedidos_service.carregar_estoque` passou a devolver o
    estoque VIRTUAL (foto menos as ORs já geradas), então a equivalência é com
    `carregar_estoque_fisico` — que segue sendo este mesmo alias."""
    async with async_session_factory() as session:
        esperado = await pedidos_service.carregar_estoque_fisico(session)
        obtido = await api_leitura.obter_estoque_por_canal(session)

    assert obtido == esperado


async def test_obter_foto_estoque_bate_com_obter_estoque_por_canal():
    """As duas projeções da foto (tupla completa vs. dict plano por canal) leem a
    mesma tabela e precisam concordar chave por chave."""
    async with async_session_factory() as session:
        plano = await api_leitura.obter_estoque_por_canal(session)
        foto, _dt = await api_leitura.obter_foto_estoque(session)

    achatado: dict[str, dict[str, int]] = {canal: {} for canal in api_leitura.CANAIS}
    for (cd, tam, canal), qt in foto.items():
        achatado[canal][f"{cd}_{tam}"] = qt

    assert achatado == plano


async def test_obter_pedidos_processados_erp_equivale_a_pedidos_service():
    async with async_session_factory() as session:
        esperado = await pedidos_service.carregar_processados_erp(session)
        obtido = await api_leitura.obter_pedidos_processados_erp(session)

    assert _normalizar_processados_erp(obtido) == _normalizar_processados_erp(esperado)


async def test_obter_referencia_posicoes_por_produtos_filtra_pelos_produtos_da_rodada():
    """O contrato é validado com dados isolados, sem depender do snapshot DEV."""
    async with async_session_factory() as session:
        codigos = ["TEST_REF_A", "TEST_REF_B"]
        session.add_all(
            [
                ProdutoTamanhoPosicao(
                    cd_prod_cor=codigos[0], sg_tamanho="P", nr_posicao=1
                ),
                ProdutoTamanhoPosicao(
                    cd_prod_cor=codigos[1], sg_tamanho="M", nr_posicao=2
                ),
            ]
        )
        await session.flush()

        referencia = await api_leitura.obter_referencia_posicoes_por_produtos(
            session, set(codigos)
        )

    assert set(referencia.keys()) == set(codigos)
    for cd in codigos:
        assert len(referencia[cd]) >= 1


async def test_obter_referencia_posicoes_por_produtos_conjunto_vazio_devolve_dict_vazio():
    async with async_session_factory() as session:
        referencia = await api_leitura.obter_referencia_posicoes_por_produtos(
            session, set()
        )

    assert referencia == {}


async def test_obter_referencia_posicoes_por_produtos_produto_inexistente_nao_aparece():
    async with async_session_factory() as session:
        referencia = await api_leitura.obter_referencia_posicoes_por_produtos(
            session, {"PRODUTO_QUE_NUNCA_EXISTIU|999"}
        )

    assert referencia == {}
