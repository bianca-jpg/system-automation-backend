# Phase 11: Parâmetros — fluxo de solicitação, aplicação na aprovação e auditoria por papel - Context

**Gathered:** 2026-08-14
**Status:** Ready for planning
**Source:** Sessão de investigação com Victória (product owner do automation OR), verificada linha a linha no código

**Baseline de código em que a investigação foi feita** (para comparar depois que a Bianca subir as mudanças dela):

- Backend `backend` — branch `develop`, commit `01c23fa` (2026-08-12)
- Frontend `frontend` — branch `develop`, commit `5f72780` (2026-08-12)

> Se ao retomar esta fase o HEAD de qualquer um dos dois repositórios for diferente, **revalide os achados abaixo antes de executar** — vários deles são bugs pontuais que podem ter sido corrigidos por commits regulares fora do fluxo GSD (foi exatamente o que aconteceu com metade da v1.2). O diff relevante: `app/modules/parametros/`, `app/modules/pedidos/processing/infrastructure/adapters.py` no backend; `app/(app)/parametros/`, `features/parametros/`, `entities/parametro/` no frontend.

<domain>
## Phase Boundary

Esta fase entrega o **ciclo completo de alteração de parâmetro de negócio**: a solicitação nasce no painel, é aprovada por administrador ou admin_técnico, **passa a valer no motor de adequação no mesmo ato**, e deixa registrado quem pediu, quem autorizou, com qual papel, por quê, e qual era o valor anterior.

**Dentro do escopo (backend):** módulo `parametros` (schemas, rotas, repositórios, migration), registro das chaves consumidas pelo motor, validação de faixa, auditoria.

**Fora do escopo:** qualquer alteração de frontend. O trabalho de tela (visibilidade por papel, selo "ativo no motor", justificativa obrigatória no formulário, histórico com nome+papel, "+5%" derivado do parâmetro) pertence à **Phase 8 — Contratos front↔API**, que passa a depender desta.

**Explicitamente fora:** transformar a página em motor de regras dinâmico. Parâmetro novo continua exigindo leitura escrita no código — ver `<deferred>`.

</domain>

<decisions>
## Implementation Decisions

### Governança de acesso (decidido por Victória, 2026-08-14)

- **Todos os níveis veem** os parâmetros vigentes — inclusive `basico` e `operacional`. O backend já permite (`require_viewer`); o frontend é que restringe indevidamente (fica na Phase 8).
- **Solicitar é gestor(30)+** — gestor, administrador e admin_técnico abrem solicitação.
- **Aprovar é administrador(40)+** — administrador e admin_técnico.
- **Admin e admin_técnico também passam pelo formulário.** Caminho único de escrita, sem porta lateral. Razão dada pela usuária: "assim fica mais organizado na parte de explicar para o usuário".
- **Autoaprovação é permitida.** Quem abre a solicitação pode aprová-la. O controle é de **registro**, não de dupla checagem. Não implementar bloqueio `reviewed_by != requested_by`.

### Auditoria (requisito central da fase)

- Tem que ficar registrado **se quem autorizou era admin ou admin_técnico** — e isso exige snapshot do papel no momento do ato, não resolução do papel atual do usuário. Papéis mudam; o histórico não pode mudar junto.
- Mesmo tratamento para quem solicitou.
- Guardar o **valor anterior** — sem antes→depois não é possível reconstruir qual tolerância valia quando uma OR foi gerada.
- `justification` passa a ser **obrigatória** no backend. Hoje o frontend manda texto fixo (`"Edição solicitada via painel"`), então o campo existe mas não informa nada.

### Aplicação do valor

- Aprovar **aplica** — na mesma transação que muda o status. Não existe passo manual depois.
- `PUT /api/v1/parametros/{chave}` deixa de ser o caminho normal: restrito a admin_técnico como válvula de emergência (ex.: reverter valor que quebrou o motor fora do horário), e também auditado.

### Registro de parâmetros conhecidos

- Uma declaração no backend das chaves que o motor consome, com tipo, faixa válida e onde é aplicada. Fonte única que serve a três consumidores: validação na escrita, campo de "ativo no motor" no `GET`, e documentação viva ao lado do código que aplica a regra.
- Hoje as chaves são duas: `tolerancia_adequacao` e `criterio_selecao`.

### Claude's Discretion

- Nome exato das colunas novas, formato do snapshot de papel (string vs. array de roles), e se o registro de parâmetros conhecidos vira dataclass, dict ou Enum.
- Como auditar o `PUT` de emergência (linha em `parametro_change_requests` com `change_type` próprio vs. tabela de audit log separada).
- Estratégia de migration para os registros históricos já existentes em `parametro_change_requests` (backfill de papel é impossível — decidir entre NULL explícito e marcador `desconhecido`).

</decisions>

<canonical_refs>

## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Módulo parâmetros (alvo principal)

- `app/modules/parametros/infrastructure/models.py` — `Parametro` e `ParametroChangeRequest`; é aqui que entram as colunas de auditoria
- `app/modules/parametros/infrastructure/repositorio_change_request.py` — `review_change_request` (linha ~90) é a função que hoje só troca status e precisa passar a aplicar
- `app/modules/parametros/infrastructure/repositorio_parametro.py` — `update_parametro`/`create_parametro`/`delete_parametro`, onde entra a validação de faixa
- `app/modules/parametros/application/schemas.py` — `ChangeRequestCreate` com `extra="forbid"` (causa do 422) e `ParametroUpdate` com `valor: str` livre
- `app/modules/parametros/infrastructure/http/routes.py` — guards por nível (`require_viewer` / `require_gestor` / `require_admin`)
- `app/modules/parametros/domain/leitura_resiliente.py` e `domain/coercao.py` — leitura com fallback seguro; **não quebrar essa propriedade**: o motor nunca pode falhar por parâmetro ausente ou corrompido

### Consumidor do parâmetro (não alterar comportamento, só respeitar)

- `app/modules/pedidos/processing/infrastructure/adapters.py` (~linha 304) — `load_adequation_config()`, ÚNICO ponto que lê parâmetro em produção
- `app/modules/pedidos/domain/motor_adequacao.py` (~linha 135) — onde a tolerância vira `limite_falta` (ceil) e `orcamento_aumento` (floor)

### Auth / RBAC

- `app/modules/auth/domain/roles.py` — níveis: basico 10, operacional 20, gestor 30, administrador 40, admin_tecnico 50
- `app/modules/auth/infrastructure/http/dependencies.py` — `require_min_role` e os atalhos; `CurrentUser.roles` é a fonte do snapshot de papel

### Padrões do repositório

- `alembic/env.py` — ⛔ nunca limpar os imports `# noqa: F401` (sem eles o autogenerate propõe DROP TABLE)
- `app/tests/test_parametros.py` — suíte existente do módulo, base para os testes novos
- `.planning/codebase/CONVENTIONS.md` e `.planning/codebase/TESTING.md`

</canonical_refs>

<specifics>
## Specific Ideas

### Achados verificados no código (2026-08-14) — o que está quebrado hoje

1. **A tela nunca escreve em `parametros`.** Todo botão (criar/editar/excluir) chama `POST /change-requests`. O `PUT` existe no backend e nenhuma tela o chama.
2. **A solicitação de edição/exclusão nem chega a ser criada.** O frontend manda `parameter_id` no nível raiz do payload; `ChangeRequestCreate` tem `extra="forbid"` → 422. Além disso `target_chave` nunca é enviado, então mesmo passando não daria para saber qual parâmetro alterar. (Criar parâmetro novo não tem esse problema — não manda `parameter_id`.)
3. **Aprovar não aplica.** O docstring de `review_change_request` diz literalmente "apenas muda o status, nunca aplica o valor".
4. **A `justification` é texto fixo do frontend** — o formulário nunca pergunta o motivo.
5. **Não há validação de faixa.** `ParametroUpdate.valor` é `str` livre. O modal do front descreve `float` como "percentuais, ex: 10,5", então digitar `10` querendo 10% grava `tolerancia = 10.0` → 1000%: `limite_falta` fica maior que a grade inteira e nada mais cai em Stand By.
6. **Chave nova é inerte.** O motor pede a chave literal `tolerancia_adequacao`; criar `margem_adequacao` ou `tolerancia_nova` não produz efeito nenhum. A usuária levantou exatamente essa dúvida — daí o requisito do selo "ativo no motor".
7. **O único caminho de escrita que funciona (`PUT`) é o único sem auditoria.**

### Comportamento que já está correto e não deve regredir

- Leitura do parâmetro é feita a cada job de processamento, sem cache — mudança vale na adequação seguinte, sem redeploy e sem reiniciar worker.
- `get_param_value` nunca propaga erro: parâmetro ausente ou corrompido cai no default de `settings.py`. O motor não pode passar a quebrar por causa desta fase.
- ORs já geradas não recalculam — ficam com a tolerância da época. Isso é desejado; é o que a auditoria de antes→depois permite explicar.

</specifics>

<deferred>
## Deferred Ideas

- **Motor de regras configurável** (criar regra nova pela tela e o sistema obedecer sem código). Fora de escopo — foi levantado e explicitamente descartado como mudança de produto muito maior. O que esta fase entrega é o oposto disciplinado: deixar visível quais chaves o motor honra, para a tela não dar essa impressão falsa.
- **Transformar a janela de 24h de edição em parâmetro** (`janela_edicao_horas`, hoje literal em ~12 pontos do front). Candidato natural ao registro criado aqui, mas é trabalho de outra fase.
- **Segregação de funções** (exigir dois administradores distintos). Avaliada e recusada por decisão de negócio nesta fase.
- **Todo o trabalho de frontend** — Phase 8, com a restrição dura de não alterar o design system (`frontend/design-system` é vendor `file:./design-system`, não é submódulo, não se edita no repo do app).

</deferred>

---

_Phase: 11-par-metros-fluxo-de-solicita-o-aplica-o-na-aprova-o-e-audito_
_Context gathered: 2026-08-14 — sessão de investigação, achados verificados no código_
