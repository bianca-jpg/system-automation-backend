# System Automation - Backend

![Python](https://img.shields.io/badge/Python-3.13-blue.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-green.svg)
![Celery](https://img.shields.io/badge/Celery-Distributed_Tasks-lightgreen.svg)
![Redis](https://img.shields.io/badge/Redis-Cache_%26_Message_Broker-red.svg)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-Async-blue.svg)

## 📌 Sobre o Projeto

Este repositório contém a API e os workers de processamento em segundo plano para um **Sistema de Automação de Pedidos B2B**. A arquitetura foi desenhada para suportar alta volumetria de dados, processamento assíncrono de integrações e roteamento inteligente.

O ecossistema é dividido em instâncias independentes:
- **API Server:** Gerencia as requisições HTTP, autenticação e operações síncronas.
- **Workers (Celery):** Processamento assíncrono de filas pesadas (cálculos, integrações com ERP, disparos de e-mail).
- **Beat (Celery):** Agendador de tarefas recorrentes (cron jobs).

## 🚀 Tecnologias Utilizadas

- **Framework:** [FastAPI](https://fastapi.tiangolo.com/) (Python 3.13)
- **ORM & Banco de Dados:** SQLAlchemy (Async) + PostgreSQL (migrações via Alembic)
- **Mensageria & Filas:** Celery com Redis (Broker e Result Backend)
- **Cache:** Redis Serverless
- **Gerenciador de Dependências:** uv

## ⚙️ Arquitetura e Funcionalidades

- **Autenticação:** Suporte a OAuth2 e JWT com verificação rápida baseada em Redis.
- **Processamento de Pedidos:** Fila de processamento isolada garantindo resiliência em falhas.
- **Health Checks:** Rotas dedicadas para probes de Liveness e Readiness para orquestradores (K8s/ECS).
- **Tratamento de Erros:** Handlers customizados para padronização de respostas de erro da API.

## 🛠️ Como Executar Localmente

1. Clone o repositório.
2. Instale as dependências usando uv:
   \\\ash
   uv sync
   \\\
3. Configure as variáveis de ambiente baseando-se no arquivo \.env.example\.
4. Suba os serviços auxiliares (PostgreSQL e Redis) via Docker:
   \\\ash
   docker-compose up -d
   \\\
5. Execute as migrações do banco:
   \\\ash
   alembic upgrade head
   \\\
6. Inicie o servidor FastAPI:
   \\\ash
   uvicorn app.main:app --reload
   \\\
"@

 = @"
# System Automation - Frontend

![Next.js](https://img.shields.io/badge/Next.js-14%2B-black.svg)
![TypeScript](https://img.shields.io/badge/TypeScript-Strict-blue.svg)
![FSD](https://img.shields.io/badge/Architecture-Feature_Sliced_Design-purple.svg)
![Vitest](https://img.shields.io/badge/Testing-Vitest-yellow.svg)

## 📌 Sobre o Projeto

Este repositório contém o painel corporativo (Dashboard) para o **Sistema de Automação de Pedidos B2B**. A aplicação fornece uma interface moderna e acessível para que os operadores do sistema gerenciem ordens, aprovem solicitações e visualizem métricas em tempo real.

## 🚀 Tecnologias Utilizadas

- **Framework:** [Next.js](https://nextjs.org/) (App Router)
- **Linguagem:** TypeScript (Strict mode)
- **Gerenciamento de Pacotes:** pnpm
- **Design System:** Componentes customizados baseados em Tailwind CSS, focados em acessibilidade (a11y).
- **Testes:** Vitest e React Testing Library

## 📐 Arquitetura: Feature-Sliced Design (FSD)

O projeto segue estritamente a arquitetura **Feature-Sliced Design**, dividindo o código em camadas de responsabilidade bem definidas:
- pp/: Roteamento, layouts globais e inicialização.
- widgets/: Blocos de UI independentes e compostos (ex: \pedido-dashboard\).
- eatures/: Lógica de negócio e interações do usuário (ex: \uth\, \parametros\).
- entities/: Entidades de domínio isoladas.
- shared/: Código reutilizável, bibliotecas, UI base (Design System) e hooks.

## ⚡ Funcionalidades Destacadas

- **Atualizações em Tempo Real:** Uso de WebSockets/Polling inteligente para refletir mudanças de estado dos pedidos instantaneamente.
- **Design System Próprio:** Biblioteca de componentes encapsulada com suporte a múltiplos temas (Dark/Light) via data-attributes.
- **Performance:** Renderização híbrida (SSR e CSR) otimizando o TTI (Time to Interactive).

## 🛠️ Como Executar Localmente

1. Clone o repositório.
2. Instale as dependências usando \pnpm\:
   \\\ash
   pnpm install
   \\\
3. Crie um arquivo \.env\ baseado no \.env.example\.
4. Inicie o servidor de desenvolvimento:
   \\\ash
   pnpm dev
   \\\
5. Acesse \http://localhost:3000\.
