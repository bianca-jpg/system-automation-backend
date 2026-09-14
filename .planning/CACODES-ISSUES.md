# CaCodes — automation OR Backend (automationBE)

**Workspace:** automation  
**Project:** automation OR Backend (`8eea15a1-6946-41a5-81e0-3c840c8b3f5c`)  
**Assignee:** Victória Macedo (`aa15c5bb-faa7-476f-be3c-af695f69decc`)  
**Branch:** `develop`  
**Nota:** Pages API 404 — índice neste arquivo + work item `automationBE-8`.

## Issues

| ID | Título | Prioridade | Parent |
|----|--------|------------|--------|
| automationBE-1 | [EPIC] Platform Hardening v1.2 — CI/Auth/Ops | urgent | — |
| automationBE-2 | [P0] CI quality+tests before ECS deploy | urgent | automationBE-1 |
| automationBE-3 | [P0] Auth: OTP delivery + seed/JWT fail-fast | urgent | automationBE-1 |
| automationBE-4 | [P0] Deploy Celery worker/beat + Alembic migrations | urgent | automationBE-1 |
| automationBE-5 | [P1] Docker prod hygiene | high | automationBE-1 |
| automationBE-6 | [P1] Contratos front↔API + docs truth + CPF/phone | high | automationBE-1 |
| automationBE-7 | [P1] Observability + CORS/metrics + token revoke/rate-limit | high | automationBE-1 |
| automationBE-8 | [PAGE/PLANNING] Auditoria Backend Platform Hardening 2026-08-07 | medium | automationBE-1 |
| automationBE-9 | [P1] GRADE-01: grade editada deve alimentar OR/Linx | high | automationBE-1 |
| automationBE-10 | [P1] Sync GSD/TechDocs (LINX-02 done, Alembic 017) | high | automationBE-1 |

## UUIDs

- Epic: `4c9ddcc8-b9a3-4393-90f3-8ef16e9f0254`
- CI: `d10b0c43-1994-4a9a-9464-3c88110051e5`
- Auth: `901753c8-cc61-46c8-96a0-070afc205dd6`
- Worker/migrations: `148b69b3-35ee-4100-b86a-37368f49e321`
- Docker: `f2894cb0-c9d6-4358-b94b-f82234eb7d59`
- Contratos: `795823f6-dd8b-4fd9-a6d2-91e731b28368`
- Observability: `8c5666d0-393e-40ef-a646-86bcf5f90dc6`
- Planning: `aeb4436d-00b6-47de-88ba-a6733f64da13`
- GRADE-01: `af22e0f9-a8b6-41b8-a870-44a08ce55ce9`
- GSD sync: `79880db5-6bfc-40cb-a66d-37259a52f9ec`

## Execução Claude

1. Ler `CLAUDE-EXECUTION-BRIEF.md` + `milestones/v1.2-PLATFORM-HARDENING.md`
2. Phases 4→9 (Wave A: 4∥5; B: 6; C: 8∥9; 7 paralelo)
3. Espelhar Collab API CI/deploy
4. Coordenar CONTRACT parâmetros com automationFT-7

**Sem correção na sessão de auditoria** — só planning/issues.

**Nota 2026-08-12:** automationBE-9 (GRADE-01) e automationBE-10 (sync GSD/TechDocs) foram investigados e a documentação local já foi corrigida — ver `.planning/REQUIREMENTS.md` § Edição manual de grade e `.planning/research/AUDIT-2026-08-07.md` § Status update. Isso não fecha os work items no tracker (Victória decide isso); é só o registro de que o planning local já reflete a investigação.
