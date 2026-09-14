# Inventário de rotas HTTP sem consumidor confirmado no frontend — 2026-08-26

**Origem:** quick task `260826-iln` (remover-endpoints-legados-de-pedidos).

**Escopo:** rotas HTTP do `backend` cruzadas com o consumo real do
`frontend`.

**Status:** levantamento validado com a mantenedora. Grupos B e C são decisão de
negócio pendente — nada neles deve ser removido sem confirmação explícita. Grupo D
é proibido: não pode ser removido antes de a Fase 11 (v1.2) fechar.

## Método e limites

O OpenAPI do backend tinha **43 paths** antes desta quick task e ficou com **40**
depois da Task 1 (`refactor(pedidos): remover endpoints legados que so respondiam
410 Gone`, commit `02ee402`). O cruzamento cobre **apenas os dois repositórios**
(`backend` e `frontend`).

**Ausência de consumidor no frontend não prova ausência de consumidor.** Pode
haver Postman, script de ops, job externo, monitoramento ou integração de
terceiro fora do alcance desta busca — o método aqui é grep contra um repositório
de frontend, não uma varredura de todos os consumidores possíveis em produção.

`GET /` aparece no OpenAPI e **não foi classificado em nenhum grupo abaixo** —
pendente de confirmação com a mantenedora sobre o que ele é e se tem consumidor.

## Grupo A — resolvido nesta quick task (removido)

| Path antigo | Handler | Por que era seguro remover | Substituto |
|---|---|---|---|
| `POST /api/v1/pedidos/adequar` | `adequar_pedidos` | Respondia só `410 Gone`; nenhum consumidor pode legitimamente depender de uma resposta 410 | `POST /api/v1/pedidos/processamentos` com `mode=adequar` |
| `POST /api/v1/pedidos/sem_adequar` | `sem_adequar_pedidos` | Respondia só `410 Gone`; nenhum consumidor pode legitimamente depender de uma resposta 410 | `POST /api/v1/pedidos/processamentos` com `mode=sem_adequar` |
| `PUT /api/v1/pedidos/{nr_pedido}/alterar-grade` | `alterar_grade_legacy` | Respondia só `410 Gone`; nenhum consumidor pode legitimamente depender de uma resposta 410 | `PUT /api/v1/pedidos/produtos/grades` com `expectedVersion` |

Removidos em código, testes e docs no commit `02ee402` (branch `develop`).

## Grupo B — sem consumidor achado no frontend, decisão de negócio pendente

| Path | O que faz | Por que aparece como órfão | O que precisa ser confirmado antes de remover |
|---|---|---|---|
| `GET /api/v1/pedidos/ordens-reserva` | Lista as ORs para o Linx, uma row por pedido/produto, com keyset 1–25 | Nenhuma tela do frontend chama este path diretamente na busca atual | Se existe consumo direto (Postman/relatório manual) ou se a exportação para o Linx acontece só via job/batch server-side, sem HTTP |
| `POST /api/v1/pedidos/{nr_pedido}/aprovar` | Aprova ORs de um pedido inteiro, opcionalmente restrito a um produto (`cd_prod_cor`) — é o contrato de compatibilidade **por pedido** | Nenhuma tela achada que chame aprovação por pedido inteiro | Se ainda existe fluxo de UI ou script que aprova o pedido inteiro de uma vez, em vez de por produto |
| `GET /api/v1/comunicacoes/{communication_id}` | Lê o detalhe de uma comunicação específica enviada ao time comercial | O frontend parece só criar (`POST /api/v1/comunicacoes`) e listar comunicações, sem abrir o detalhe individual por id | Se há tela de detalhe de comunicação prevista ou planejada, ou se o GET por id é só para uso interno/debug |

**Registro inequívoco:** `POST /api/v1/pedidos/{nr_pedido}/aprovar` (pedido inteiro)
**NÃO é o mesmo endpoint** que `POST /api/v1/pedidos/produtos/aprovar` (por
produto/canal). O segundo **é usado pelo frontend** (tela de aprovação em lote
por produto) e **não entra em discussão de remoção** — só o primeiro, que aprova
o pedido inteiro, está no Grupo B.

## Grupo C — sem consumidor no frontend, plausivelmente infra/ops, não confirmado

| Path | Uso plausível |
|---|---|
| `GET /health` | Provável healthcheck de ALB/ECS |
| `GET /health/ready` | Provável readiness probe de ALB/ECS |
| `GET /metrics` | Provável scrape do Prometheus |
| `GET /v1/status` | Único path do módulo `core` hoje, protegido por `require_viewer`; possível consumidor de monitoramento externo autenticado |
| `POST /api/v1/ingestao/sincronizar` | Gatilho manual de sincronização; sem tela no frontend, possível uso via Swagger ou script de ops |
| `GET /api/v1/ingestao/sincronizar/{jobId}` | Consulta de status do job de sincronização disparado manualmente |

**"Plausível" é hipótese, não verificação.** Nada aqui foi confirmado contra a
configuração real do ALB, do ECS ou do Prometheus nesta rodada — é inferência a
partir do nome e do padrão usual desses paths, não uma checagem de infraestrutura.

## Grupo D — NÃO é legado, proibido remover

| Path | Verbo |
|---|---|
| `/api/v1/parametros` | `POST` |
| `/api/v1/parametros/{chave}` | `PUT` |
| `/api/v1/parametros/{chave}` | `DELETE` |

Não há consumidor no frontend hoje porque o frontend só chama o fluxo de
change-requests (`/api/v1/parametros/change-requests...`). O wiring direto dessas
3 rotas para papéis `administrador`+ é uma **lacuna conhecida e documentada**:
trabalho em andamento da Victoria na **Fase 11** do milestone v1.2
(`par-metros-fluxo-de-solicita-o-aplica-o-na-aprova-o-e-audito`), com 1/5 planos
executados, o plano 11-05 invalidado pela decisão de negócio de 2026-08-18 (que
determinou que `administrador`+ edita/exclui pelas Ações com efeito imediato,
tornando `PUT`/`DELETE` o caminho normal, não válvula de emergência) e o plano de
frontend (11-06) ainda inexistente. Ver `.planning/STATE.md`, seção "Milestone
v1.2 (paralelo, Victoria)".

**Nada do Grupo D pode ser removido antes de a Fase 11 fechar.**

## Grupo E — drift de documentação, não é código morto

Achados já confirmados por grep no repositório de planning. Nenhum destes paths
corresponde a um `@router.*` real no backend — são drift entre a documentação de
mapeamento (`.planning/codebase/`) e o código atual.

| Path documentado | Onde está documentado | Realidade atual |
|---|---|---|
| `POST /api/auth/account/update` | `.planning/codebase/INTEGRATIONS.md:195` | Não existe `@router.*` correspondente no módulo `auth` |
| `POST /api/auth/account/email/confirm` | `.planning/codebase/INTEGRATIONS.md:196` | Não existe `@router.*` correspondente no módulo `auth` |
| `POST /api/comunicacoes/comunicar-comercial` | `.planning/codebase/INTEGRATIONS.md:269` | O path real de comunicações é `POST /api/v1/comunicacoes`, não este |
| `GET /core/version` | `.planning/codebase/STRUCTURE.md:152` | O módulo `core` expõe hoje só `GET /v1/status` |
| `GET /core/info` | `.planning/codebase/ARCHITECTURE.md:128` | O módulo `core` expõe hoje só `GET /v1/status` |

A correção natural é regenerar o mapa do codebase com `/gsd-map-codebase`. Isso
ficou **fora do escopo** desta quick task — nenhum arquivo de
`.planning/codebase/` foi alterado por `260826-iln`.

## Próximos passos

- **Grupo B:** confirmar com a mantenedora se `GET /ordens-reserva`, `POST
  /{nr_pedido}/aprovar` e `GET /comunicacoes/{communication_id}` têm consumidor
  fora dos dois repositórios (Postman, script, integração de terceiro) antes de
  cogitar remoção.
- **Grupo C:** confirmar com quem cuida de infra (ALB/ECS/Prometheus) se
  `/health`, `/health/ready`, `/metrics` e `/v1/status` são de fato consumidos
  por monitoramento externo, e se `/ingestao/sincronizar*` segue precisando de
  gatilho manual via HTTP.
- **Grupo D:** não mexer até a Fase 11 (v1.2) fechar — ver `.planning/STATE.md`.
- **Grupo E:** regenerar o mapa do codebase com `/gsd-map-codebase` para corrigir
  o drift de documentação.
