#!/usr/bin/env bash
# =============================================================================
# purge-secrets-from-history.sh
#
# Remove arquivos sensíveis de TODO o histórico do Git usando git-filter-repo.
#
# ⚠️  CUIDADO: este script REESCREVE o histórico do repositório.
#     - Faça backup do repositório antes de executar.
#     - Após execução, TODOS os colaboradores precisam re-clonar.
#     - O remote origin é removido; re-adicione e faça force push.
#
# Pré-requisitos:
#   pip install git-filter-repo
#   (ou: brew install git-filter-repo / apt install git-filter-repo)
#
# Uso:
#   cd /caminho/do/repo
#   bash scripts/purge-secrets-from-history.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${YELLOW}║  PURGE DE SEGREDOS DO HISTÓRICO GIT                     ║${NC}"
echo -e "${YELLOW}║  Este script REESCREVE todo o histórico do repositório.  ║${NC}"
echo -e "${YELLOW}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""

# ── Verificar que estamos na raiz do repo ────────────────────────────────────
if [ ! -d ".git" ]; then
    echo -e "${RED}ERRO: Execute este script na raiz do repositório Git.${NC}"
    exit 1
fi

# ── Verificar que git-filter-repo está instalado ─────────────────────────────
if ! command -v git-filter-repo &> /dev/null; then
    echo -e "${RED}ERRO: git-filter-repo não encontrado.${NC}"
    echo "Instale com: pip install git-filter-repo"
    exit 1
fi

# ── Confirmar com o usuário ──────────────────────────────────────────────────
REPO_URL=$(git remote get-url origin 2>/dev/null || echo "(nenhum remote)")
echo -e "Repositório: ${GREEN}$(pwd)${NC}"
echo -e "Remote origin: ${GREEN}${REPO_URL}${NC}"
echo ""
echo "Arquivos que serão REMOVIDOS de todo o histórico:"
echo "  1. backups/database_backup.dump  (~25 MB)"
echo "  2. .env  (contém segredos de produção)"
echo "  3. docs/rotacao-segredos.md  (versão antiga com segredos reais em texto plano)"
echo ""
echo -e "${RED}Esta operação é IRREVERSÍVEL após force push.${NC}"
read -p "Deseja continuar? (digite 'SIM' para confirmar): " CONFIRM

if [ "$CONFIRM" != "SIM" ]; then
    echo "Operação cancelada."
    exit 0
fi

echo ""
echo -e "${YELLOW}[1/5] Salvando remote origin...${NC}"
ORIGIN_URL=$(git remote get-url origin 2>/dev/null || echo "")

echo -e "${YELLOW}[2/5] Removendo dump do histórico...${NC}"
git filter-repo --invert-paths \
    --path backups/database_backup.dump \
    --force

echo -e "${YELLOW}[3/5] Removendo .env do histórico...${NC}"
git filter-repo --invert-paths \
    --path .env \
    --force

echo -e "${YELLOW}[4/5] Removendo docs/rotacao-segredos.md (versão com segredos reais) do histórico...${NC}"
git filter-repo --invert-paths \
    --path docs/rotacao-segredos.md \
    --force

echo -e "${YELLOW}[5/5] Re-adicionando remote origin...${NC}"
if [ -n "$ORIGIN_URL" ]; then
    git remote add origin "$ORIGIN_URL"
    echo -e "Remote re-adicionado: ${GREEN}${ORIGIN_URL}${NC}"
else
    echo -e "${YELLOW}Nenhum remote origin anterior encontrado. Adicione manualmente:${NC}"
    echo "  git remote add origin <URL>"
fi

echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  LIMPEZA CONCLUÍDA COM SUCESSO!                         ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "Próximos passos:"
echo ""
echo "  1. FORCE PUSH para o remote (todas as branches afetadas):"
echo "     git push origin --force --all"
echo "     git push origin --force --tags"
echo ""
echo "  2. NOTIFICAR todos os colaboradores para RE-CLONAR:"
echo "     git clone <URL> (não use git pull!)"
echo ""
echo "  3. ROTACIONAR todos os segredos expostos (ver docs/rotacao-segredos.md):"
echo "     - Senha do Aurora PostgreSQL (PROD_DATABASE_URL)"
echo "     - Token Databricks (DATABRICKS_TOKEN)"
echo "     - JWT_SECRET"
echo "     - SMTP_PASSWORD (Gmail App Password)"
echo "     - BACKSTAGE_PAT"
echo "     - AUTH_MICROSOFT_ENTRA_ID_SECRET (frontend)"
echo ""
echo "  4. VERIFICAR que o dump não existe mais no histórico:"
echo "     git log --all --full-history -- backups/"
echo "     git log --all --full-history -- .env"
echo "     git log --all --full-history -- docs/rotacao-segredos.md"
echo "     (todos devem retornar vazio)"
echo ""
echo "  5. RECRIAR docs/rotacao-segredos.md sem valores reais de segredo"
echo "     (o filter-repo removeu TODAS as versões do arquivo, incluindo a atual)"
