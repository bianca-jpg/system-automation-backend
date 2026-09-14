"""Chaves estáveis dos locks transacionais compartilhados entre contextos."""

# Full refresh substitui estoque/pedidos/read model; adequação, grade e
# aprovação leem e escrevem o mesmo estado. Todos precisam disputar o MESMO
# lock para que nenhum write valide uma foto e confirme sobre outra.
ORDER_STATE_MUTATION_LOCK = 728_193_045

# Lock de sessão mantido por uma conexão dedicada desde antes da primeira
# chamada ao Databricks. Ele coalesce jobs completos sem bloquear adequação ou
# edição durante a fase lenta de coleta. É deliberadamente diferente do lock de
# mutação acima, adquirido apenas no swap PostgreSQL.
INGESTION_JOB_LOCK = 728_193_046


__all__ = ["INGESTION_JOB_LOCK", "ORDER_STATE_MUTATION_LOCK"]
