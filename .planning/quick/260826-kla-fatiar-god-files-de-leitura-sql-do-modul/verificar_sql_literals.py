"""Gate de fidelidade de literais SQL multilinha.

Recebe caminhos de arquivo ``.py`` em ``argv`` e imprime, um por linha e na
ordem de aparicao no AST, ``sha1(texto)`` + numero de linhas de cada literal
de string multilinha encontrado:

- todo ``ast.Constant`` de ``str`` cujo valor contem ``\\n``;
- todo ``ast.JoinedStr`` (f-string) cujo ``ast.unparse`` contem ``\\n``, usando
  o proprio ``ast.unparse`` como texto (nao ha como recuperar o literal fonte
  exato de uma f-string a partir do AST, entao o unparse normalizado e o
  proxy usado nos dois lados da comparacao).

Uso: ``python verificar_sql_literals.py arquivo1.py arquivo2.py ...``

Sem dependencia externa (so stdlib: ast, hashlib, pathlib, sys), sem
argparse, saida deterministica (ordem de aparicao no arquivo, arquivos na
ordem recebida em argv).
"""

from __future__ import annotations

import ast
import hashlib
import pathlib
import sys


def _literals_multilinha(caminho: pathlib.Path) -> list[str]:
    fonte = caminho.read_text(encoding="utf-8")
    arvore = ast.parse(fonte, filename=str(caminho))
    saida: list[str] = []
    for node in ast.walk(arvore):
        texto: str | None = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "\n" in node.value:
                texto = node.value
        elif isinstance(node, ast.JoinedStr):
            unparsed = ast.unparse(node)
            if "\n" in unparsed:
                texto = unparsed
        if texto is not None:
            digest = hashlib.sha1(texto.encode("utf-8")).hexdigest()
            linhas = texto.count("\n") + 1
            saida.append(f"{digest} {linhas}")
    return saida


def main(argv: list[str]) -> int:
    for arg in argv[1:]:
        caminho = pathlib.Path(arg)
        for linha in _literals_multilinha(caminho):
            print(linha)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
