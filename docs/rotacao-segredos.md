# Rotação de Segredos — Checklist Pós-Limpeza

> **Contexto**: Segredos foram expostos no histórico do Git (`.env` commitado
> em commits anteriores). Mesmo após remoção do histórico, qualquer pessoa que
> já fez `clone`/`fetch` pode ter acesso. **Todos devem ser rotacionados.**

> **Nunca cole valores reais de segredo neste documento.** Este arquivo é
> versionado no Git — descreva onde o segredo vive e como trocá-lo, não o
> valor em si. (Este arquivo já vazou uma vez com valores reais commitados
> por engano; foi purgado do histórico e reescrito sem eles.)

---

## 🔴 Prioridade CRÍTICA

### 1. Senha do Aurora PostgreSQL (Produção)
- **Onde está**: `PROD_DATABASE_URL` no `.env`
- **Como rotacionar**:
  1. Acesse o AWS Console → RDS → instância `your-rds-instance`
  2. Modifique a senha master do banco
  3. Atualize em: AWS Secrets Manager / ECS Task Definitions / `.env` local
- **Validação**: testar conexão ao banco com a nova senha

### 2. Token Databricks
- **Onde está**: `DATABRICKS_TOKEN` no `.env`
- **Como rotacionar**:
  1. Acesse o Databricks → Settings → Developer → Access tokens
  2. Revogue o token existente
  3. Gere um novo token e atualize no `.env` local e ECS
- **Validação**: executar uma query de teste via API

### 3. Client Secret do Microsoft Entra ID (Frontend)
- **Onde está**: `AUTH_MICROSOFT_ENTRA_ID_SECRET` no `.env` do frontend
- **Como rotacionar**:
  1. Azure Portal → App registrations → app do frontend
  2. Certificates & secrets → Client secrets → Delete o secret atual
  3. Adicionar novo client secret → copiar valor
  4. Atualizar no `.env` do frontend e AWS Amplify
- **Validação**: testar login SSO via Microsoft

---

## 🟡 Prioridade ALTA

### 4. JWT_SECRET
- **Onde está**: `JWT_SECRET` no `.env`
- **Como rotacionar**:
  1. Gerar novo segredo: `openssl rand -hex 64`
  2. Atualizar no `.env` local e ECS Task Definitions
- **Impacto**: todos os tokens JWT existentes serão invalidados (logout forçado)
- **Validação**: testar login e verificar que tokens antigos são rejeitados

### 5. SMTP_PASSWORD (Gmail App Password)
- **Onde está**: `SMTP_PASSWORD` no `.env`
- **Como rotacionar**:
  1. Conta Google do remetente → Segurança → Senhas de app
  2. Revogar a senha atual
  3. Gerar nova senha de app (16 dígitos)
  4. Atualizar no `.env` local e ECS
- **Validação**: testar envio de e-mail via "Comunicar Time Comercial"

### 6. Backstage PAT (Backend + Frontend)
- **Onde está**: `BACKSTAGE_PAT` no `.env` de ambos os projetos
- **Como rotacionar**:
  1. Backstage → Settings → API tokens → revogar os tokens atuais (backend e frontend)
  2. Gerar novos tokens
  3. Atualizar no `.env` e `.mcp.json` de ambos os projetos
- **Validação**: verificar integração com Backstage

---

## ✅ Checklist de conclusão

- [ ] Senha Aurora PostgreSQL rotacionada
- [ ] Token Databricks rotacionado
- [ ] Client Secret Entra ID rotacionado
- [ ] JWT_SECRET rotacionado
- [ ] SMTP_PASSWORD rotacionada
- [ ] Backstage PAT (backend) rotacionado
- [ ] Backstage PAT (frontend) rotacionado
- [ ] Todos os serviços ECS reiniciados com novos segredos
- [ ] Login SSO testado com sucesso
- [ ] Envio de e-mail testado com sucesso
- [ ] Conexão Databricks testada com sucesso
- [ ] Equipe notificada para re-clonar o repositório
