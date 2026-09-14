from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[2]
_RUNBOOK = _BACKEND_ROOT / "docs" / "seguranca.md"
_INDEX = _BACKEND_ROOT / "docs" / "index.md"

# Senhas default das antigas 5 contas seed (removidas junto com o login local
# na quick task 260827-fqf) — nenhuma pode voltar a aparecer no runbook.
_SENHAS_SEED_ANTIGAS = ("basico123", "oper123", "gestor123", "admin123", "admintec123")


def test_runbook_nao_reintroduz_senhas_das_contas_seed_antigas() -> None:
    conteudo = _RUNBOOK.read_text(encoding="utf-8")

    for senha in _SENHAS_SEED_ANTIGAS:
        assert senha not in conteudo, f"senha seed reintroduzida no runbook: {senha}"


def test_runbook_tem_secoes_obrigatorias() -> None:
    conteudo = _RUNBOOK.read_text(encoding="utf-8")
    linhas = conteudo.splitlines()

    obrigatorias = ["## Detecção", "## Rotação", "## Remoção", "## Prevenção"]
    for titulo in obrigatorias:
        assert any(linha.startswith(titulo) for linha in linhas), (
            f"seção obrigatória ausente: {titulo}"
        )


def test_index_linka_o_runbook() -> None:
    conteudo = _INDEX.read_text(encoding="utf-8")

    assert "(seguranca.md)" in conteudo
