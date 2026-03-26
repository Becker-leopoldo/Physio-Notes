# Physio Notes — Documentação do Projeto

POC de prontuário clínico por voz para fisioterapeutas.
Permite gravar sessões, transcrever com IA e consolidar notas automaticamente.

---

## Stack

| Camada     | Tecnologia                                      |
|------------|-------------------------------------------------|
| Backend    | Python 3.12 + FastAPI + SQLite                  |
| Transcrição| Groq Whisper (`whisper-large-v3-turbo`)         |
| IA clínica | Anthropic Claude (`claude-haiku-4-5-20251001`)  |
| Frontend   | HTML/JS vanilla — SPA, PWA offline-ready        |
| Deploy     | Docker + docker-compose                         |

---

## Estrutura de Arquivos

### Raiz

| Arquivo            | Descrição |
|--------------------|-----------|
| `Dockerfile`       | Build da imagem Python 3.12-slim. Copia backend e frontend, expõe porta 8000. Roda `uvicorn main:app` a partir de `/app/backend`. |
| `docker-compose.yml` | Sobe o serviço com volume persistente `/data` para o SQLite e carrega variáveis do `.env`. |
| `.env.example`     | Template das variáveis de ambiente necessárias (`ANTHROPIC_API_KEY`, `GROQ_API_KEY`). Copiar para `.env` e preencher antes de subir. |
| `.gitignore`       | Exclui `.env`, `*.db` e `__pycache__` do controle de versão. |
| `.dockerignore`    | Exclui os mesmos arquivos sensíveis/desnecessários da imagem Docker. |
| `DOC.md`           | Este arquivo. |

---

### Backend (`backend/`)

| Arquivo          | Descrição |
|------------------|-----------|
| `main.py`        | Aplicação FastAPI. Define todos os endpoints REST: pacientes, sessões, upload de áudio, transcrição avulsa, consolidação IA, billing e relatório CREFITO. Monta o frontend como `StaticFiles` ao final (deve ficar por último para não interceptar as rotas da API). |
| `database.py`    | Camada de acesso ao SQLite. Gerencia 5 tabelas: `paciente`, `sessao`, `audio_chunk`, `sessao_consolidada` e `api_uso` (billing). Inclui `init_db()` para criação inicial e `_migrate()` para adicionar colunas/tabelas sem quebrar bancos existentes. O caminho do banco é configurável via variável de ambiente `DB_PATH`. |
| `ai.py`          | Integração com a API Anthropic. Contém 4 funções principais: `consolidar_sessao` (estrutura notas da sessão em JSON clínico), `resumir_historico` (gera resumo narrativo do paciente), `extrair_dados_paciente` (extrai nome/data/anamnese de áudio de cadastro) e `responder_pergunta` (responde perguntas sobre o histórico). Registra automaticamente o uso de tokens e custo em `api_uso` após cada chamada via `_registrar()`. |
| `transcribe.py`  | Integração com Groq Whisper via SDK OpenAI (compatível). Recebe bytes de áudio e retorna transcrição em português. |
| `requirements.txt` | Dependências: `fastapi`, `uvicorn[standard]`, `python-multipart`, `openai`, `anthropic`, `python-dotenv`, `aiofiles`. |
| `start.bat`      | Script Windows para desenvolvimento local. Instala dependências e inicia o servidor em `localhost:8000` com hot-reload. |

---

### Frontend (`frontend/`)

| Arquivo         | Descrição |
|-----------------|-----------|
| `index.html`    | Aplicação completa em arquivo único (~2.300 linhas). SPA com as seguintes seções: lista de pacientes, perfil do paciente, gravação de sessão, detalhe de sessão encerrada, resumo clínico e billing. Funcionalidades principais: cadastro por voz (IA extrai campos do áudio), gravação com transcrição em tempo real, auto-encerramento de sessão por inatividade (10 min, countdown por sessão, persistido em localStorage), rastreamento de billing em R$ com cotação USD/BRL em tempo real. |
| `sw.js`         | Service Worker mínimo para registrar o app como PWA (instalável no celular). |
| `manifest.json` | Manifesto PWA: nome do app, ícones, cores e modo de exibição standalone. |
| `favicon.svg`   | Ícone do app — quadrado preto arredondado com as iniciais "PN". |
| `start.bat`     | Script Windows para desenvolvimento local. Serve o frontend estático em `localhost:3000` via `python -m http.server`. |

---

## Banco de Dados

```
paciente          → dados do paciente + anamnese inicial
sessao            → sessão de atendimento (aberta/encerrada)
audio_chunk       → cada trecho de áudio transcrito dentro de uma sessão
sessao_consolidada→ resultado da análise IA ao encerrar sessão
api_uso           → log de cada chamada à API de IA (tokens + custo)
```

---

## Variáveis de Ambiente

| Variável            | Obrigatória | Descrição |
|---------------------|-------------|-----------|
| `ANTHROPIC_API_KEY` | Sim         | Chave da API Anthropic (Claude) |
| `GROQ_API_KEY`      | Sim         | Chave da API Groq (Whisper) |
| `DB_PATH`           | Não         | Caminho do arquivo SQLite (padrão: `backend/physio_notes.db`; no Docker: `/data/physio_notes.db`) |

---

## Deploy com Docker

```bash
# 1. Configurar variáveis de ambiente
cp .env.example .env
# editar .env com as chaves reais

# 2. Build e iniciar
docker compose up -d --build

# 3. Acessar
# http://localhost:8000  (dev)
# http://IP_DO_SERVIDOR:8000  (produção)

# Comandos úteis
docker compose logs -f        # acompanhar logs
docker compose down           # parar
docker compose up -d --build  # atualizar após mudanças
```

---

## Desenvolvimento Local (sem Docker)

```bash
# Backend
cd backend
pip install -r requirements.txt
start.bat   # Windows
# ou: uvicorn main:app --reload

# Frontend
# Abrir frontend/index.html direto no navegador
# ou: cd frontend && python -m http.server 3000
```

> Em dev local, o frontend faz chamadas para `http://localhost:8000` automaticamente.
> No servidor, usa URLs relativas (mesmo origin).
