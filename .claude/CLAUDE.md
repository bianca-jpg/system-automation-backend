# automation - OR - Back-end — Claude Context

Backend do sistema de **otimização de OR (Ordem de Reserva)** e automação da adequação de pedidos do automation. Ingere pedidos e estoque do Databricks para o Postgres e roda o motor de adequação (matching pedido × estoque) que gera as ordens de reserva.

## Antes de qualquer mudança

### 1. Consulte o mapa do codebase — não varra o repositório inteiro

Este repositório e o frontend estão mapeados em **`.planning/codebase/`** (7 documentos, atualizados em 2026-08-04).

**Antes de editar qualquer coisa, leia o documento que cobre a área em questão.** Não explore o código inteiro com buscas amplas para redescobrir onde as coisas estão: o mapa já responde isso e aponta os caminhos: você abre apenas os arquivos indicados. Isso economiza contexto e evita leitura desnecessária.

| O que você precisa saber | Documento a consultar |
|---|---|
| Onde fica X? Onde adicionar código novo? | `.planning/codebase/STRUCTURE.md` |
| Como as camadas se relacionam? Fluxo de dados? Entry points? | `.planning/codebase/ARCHITECTURE.md` |
| Que biblioteca/versão o projeto usa? Como rodar? | `.planning/codebase/STACK.md` |
| Como falar com Databricks, Postgres, Redis, SMTP? | `.planning/codebase/INTEGRATIONS.md` |
| Estilo, nomenclatura, tratamento de erro, tipagem? | `.planning/codebase/CONVENTIONS.md` |
| Como escrever e rodar testes? | `.planning/codebase/TESTING.md` |
| Áreas frágeis, dívidas técnicas, riscos conhecidos? | `.planning/codebase/CONCERNS.md` |

Fluxo esperado: **documento relevante → arquivos que ele aponta → edição.** Só faça varredura ampla quando o mapa não cobrir o assunto.

O mapa é ponto de partida, não verdade absoluta — ele retrata o estado de quando foi gerado. Confirme no código antes de afirmar algo como fato, e regenere com `/gsd-map-codebase` (rodando **de dentro deste repositório**, onde vive o `.planning/`) quando estiver defasado.

### 2. Consulte o coding standards

**Sempre** consulte o coding standards via MCP do Backstage antes de editar arquivos neste repositório:

- Ferramenta: `backstage_get_coding_standards` (servidor MCP `backstage`)
- Configuração: copie `.mcp.json.example` para `.mcp.json` e troque `YOUR_PAT_HERE` pelo seu PAT pessoal do Backstage
- **Nunca commite o PAT.**

O coding standards é a fonte de verdade para convenções de código, estrutura de pastas, nomenclatura e padrões de qualidade do projeto.

## Stack

- **Python 3.13+** · FastAPI 0.115 · Uvicorn — gerenciado com **uv** (`pyproject.toml`)
- **Postgres** via SQLAlchemy 2.0 async + asyncpg; migrations com **Alembic** (`alembic/versions/`; para descobrir a head, rode `uv run alembic heads` — não confie em número anotado aqui)
- **Celery 5.4 + Redis** — sincronização periódica do Databricks (beat a cada 2h, ajustável via `INGESTAO_INTERVALO_SEGUNDOS`)
- **Databricks SQL Statement Execution API** via `httpx` — disposição INLINE com fallback automático p/ EXTERNAL_LINKS (`app/shared/infrastructure/databricks_client.py`)
- Auth: **Microsoft Entra ID (SSO)** + **JWT** (python-jose, HS256) para sessão; **RBAC de 5 níveis**
- Pydantic v2 (settings) · pytest + pytest-asyncio (`app/tests/`) · prometheus-client (métricas)
- Configuração por `.env` (gitignored): Postgres, Databricks, Redis/Celery, JWT, SMTP

## Arquitetura

Monólito modular. Cada feature vive em `app/modules/<nome>/` com `routes.py` (HTTP) + `schemas.py` (Pydantic) + `models.py` (ORM) + `service.py`, mais as camadas **DDD**: `domain/` (regras de negócio puras), `application/` (casos de uso) e `infrastructure/` (repositórios e acesso a dados). Infra compartilhada em `app/shared/`, com 9 subpacotes: `config`, `database`, `errors`, `infrastructure`, `jobs`, `logging`, `metrics`, `pagination`, `security`.

Para o detalhe das camadas e de como elas se relacionam, consulte `ARCHITECTURE.md` no mapa do codebase — não deduza a partir do código.

**Módulos:**
- **ingestao** — full refresh Databricks→Postgres (`sincronizar_pedidos` / `sincronizar_estoque` / `sincronizar_tudo`). O recorte de pedidos em aberto é feito na origem (view `system_automation_pedidos_em_aberto`); o leitor só mantém a sanidade `qt_entregar > 0` (`infrastructure/databricks_reader.py::ler_pedidos_em_aberto`) — **não existe filtro por mês corrente**. Estoque agregado por `(cd_prod_cor, sg_tamanho, canal)`.
- **pedidos** — motor de adequação: `processar_pedidos` → `_processar_pedidos_canal` → `adequar_grade_produto`. **Isolado por canal** (Franquia/Multimarca): um pedido só consome estoque do próprio canal. Grava `ordens_reserva` + `pedidos_processados`. Também expõe leitura de pedidos e alteração de grade.
- **auth** — login via Microsoft Entra ID (SSO, único caminho), JWT sign/refresh, RBAC (`basico` < `operacional` < `gestor` < `administrador` < `admin_tecnico`); conta nova nasce no primeiro login com papel mínimo e é promovida via endpoint de admin; roles sempre relidos do banco (não confia só no token).
- **parametros** — knobs de negócio (`criterio_selecao`, `tolerancia_adequacao`), com change requests.
- **comunicacoes** — envio de e-mail via SMTP (aiosmtplib); botão "Comunicar Time Comercial".
- **realtime** — Open Host Service de eventos (outbox + Redis Stream + WebSocket); os demais módulos publicam por `app/modules/realtime/__init__.py`.
- **core**, **health** — apoio (status e health checks). Não existem módulos `alertas` nem `history`: alertas são o `alertas_router` de `app/modules/pedidos/routes.py`, e o histórico não tem rota própria — é a projeção por produto `GET /api/v1/pedidos/produtos?stage=historico` (as rotas legadas `GET /api/v1/pedidos` e `GET /api/v1/pedidos/historico` foram removidas).

**Entry points:** `app/main.py` (FastAPI + lifespan/CORS), `app/workers/celery_app.py` (beat/worker), `alembic/` (schema).

**Tabelas:** o schema é bem maior do que o núcleo de adequação (auth, ingestão, ORs, comunicações, jobs duráveis, processamento e realtime). A lista completa e sempre atualizada são os imports de model em `alembic/env.py` (o registro do `Base.metadata`); a visão por camada está em `docs/arquitetura.md`. ⛔ Nunca "limpar" os imports `# noqa: F401` de `alembic/env.py` — sem eles o autogenerate propõe `DROP TABLE`.

**Regras de arquitetura importantes:** full refresh (DELETE + INSERT a cada sync, sem incremental); isolamento por canal no matching (nunca alocar entre canais); async/await de ponta a ponta.

## Placebos (soluções temporárias com data de validade)

Coisas que existem só porque uma dependência externa ainda não está pronta. **Não trate como arquitetura definitiva** — cada uma tem condição de remoção explícita.

### `estoque_virtual` — estoque descontando as ORs do app

**Por que existe:** a view `system_automation_estoque_filtrado` no Databricks entrega uma foto **congelada por dia** (`dt_estoque = current_date()`) e o app **ainda não tem conexão com o ERP**. Então uma OR gerada às 10h não reduz o disponível às 12h: dois pedidos diferentes do mesmo produto seriam ambos atendidos entre ciclos de sync, reservando mais peça do que existe.

**O que é:** a projeção `foto(estoque) − ORs geradas desde a data da foto`, recalculada do zero (não é saldo decrementado, então não acumula drift). O reset diário não é código: quando `dt_estoque` avança, as ORs do dia anterior saem do filtro e a projeção volta a ser igual à foto.

**Onde:** `pedidos/models.py::EstoqueVirtual`, `pedidos/domain/estoque_virtual.py` (regra pura), `pedidos/infrastructure/repositorio_estoque_virtual.py` (projeção + leitura com auto-cura), gatilho em `executar_adequacao` / `executar_sem_adequacao`. `carregar_estoque` devolve o **virtual**; a foto crua é `carregar_estoque_fisico`.

**Limites aceitos:** reserva virtual não sobrevive à virada do dia (se a OR não chegou ao ERP, a peça reaparece amanhã); reserva feita direto no Linx entra na conta só no próximo recálculo (erra para o lado conservador).

**Quando remover:** quando o time de TI tornar a tabela mãe de estoque near real-time, ou quando existir integração com o ERP que atualize o disponível. Aí `carregar_estoque` volta a apontar para `estoque`, e a tabela, o domínio, o repositório e os gatilhos saem juntos.

## Como rodar

Roda via **Docker**: `docker compose -f .docker/docker-compose.yml up -d` → API em `localhost:8000`. O `.env` só é lido na **subida** da stack — reinicie ao alterar variáveis. Testes: `uv run pytest`. Ver `README.md` para o setup local completo.

Ao adicionar dependências ou estrutura nova, além do coding standards (acima), verifique se já existe um template específico no Backstage para o tipo de peça que você quer construir (ex.: `backend-api`, `npm-package`, `connect-template`).

## Convenções

- Commits: [Conventional Commits](https://www.conventionalcommits.org/) com escopo quando o domínio for claro
- Branches: `feat/`, `fix/`, `chore/`, `refactor/`, `docs/` a partir de `develop`
- **Sempre faça commit e push para `develop`, nunca para `main`, a não ser que seja solicitado explicitamente.** `main` é populada apenas em release, via PR.
- Código: Clean Code + princípios de [refactoring.guru](https://refactoring.guru/) — sem over-engineering.

## Catalog

Este componente está registrado em [Backstage](https://your-backstage-instance.example.com/catalog/default/component/system-automation).
