# Research: Migração completa de dados para Aurora

**Milestone:** v1.3 Migração completa de dados para Aurora  
**Date:** 2026-08-10

## Decisão de abordagem

O projeto usa PostgreSQL na origem e Aurora PostgreSQL 17 no destino. Para uma
cópia completa e pontual de um banco pequeno ou médio, a abordagem recomendada
é usar as ferramentas nativas `pg_dump` e `pg_restore`, com o aplicativo em
janela controlada para que a origem não mude durante o dump. Aurora PostgreSQL
suporta essas ferramentas. AWS DMS só é necessário se o corte precisar de
replicação contínua e quase nenhuma indisponibilidade.

## Fluxo seguro

1. Inventariar origem: versão, extensões, tabelas, contagens, sequências,
   schema Alembic e tamanho do dump.
2. Criar um dump recuperável no formato custom (`pg_dump -Fc`) e preservar o
   artefato fora do repositório.
3. Confirmar que o Aurora está vazio ou descartar/recriar somente o schema de
   destino sob controle, antes de importar.
4. Restaurar no Aurora usando TLS e o endpoint de escrita do cluster, sem
   restaurar ownership/roles que o RDS gerenciado não permite administrar.
5. Comparar estruturas, contagens de cada tabela, sequência/identidade e dados
   críticos; executar smoke test da aplicação.
6. Atualizar a configuração de produção para o endpoint do Aurora, reiniciar
   API, worker e beat, e disparar/observar uma ingestão Databricks.
7. Manter o Postgres local intacto como retorno até a validação final. Criar um
   snapshot Aurora após a validação.

## Implicações para este projeto

- A ingestão continua Databricks -> PostgreSQL a cada 2 horas. Após o corte,
  esse PostgreSQL é o Aurora; ela não sincroniza os dois bancos entre si.
- O worker Celery, o beat e o Redis precisam estar ativos e com as URLs remotas
  corretas. O health check da API sozinho não prova que a ingestão agendada
  funciona.
- O Aurora atual exige conexão TLS; usar `POSTGRES_SSL_MODE=require` em
  produção e preferir o endpoint writer do cluster em vez do endpoint de uma
  instância específica.
- A aplicação deve ficar sem escrita durante o dump final, ou a migração deve
  repetir a validação/restore para eliminar divergência.

## Riscos a evitar

- Usar a senha/local `automation` ou o host Docker como destino do comando.
- Rodar `pg_restore --clean` sem confirmar o alvo, pois pode apagar o schema
  do Aurora.
- Restaurar roles globais/ownership de um Postgres local no RDS/Aurora.
- Apontar a API para Aurora vazio antes de migration, restore e validação.
- Declarar sucesso apenas porque `/health/ready` retorna `database: ok`.

## Fontes

- AWS: Importing data into PostgreSQL on Amazon RDS
  https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/PostgreSQL.Procedural.Importing.html
- AWS: Migration support for Aurora PostgreSQL
  https://docs.aws.amazon.com/rds/latest/auroraextendedcontent/aurora-features-migration.html
- PostgreSQL: pg_dump
  https://www.postgresql.org/docs/current/app-pgdump.html
