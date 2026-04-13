# Physio Notes — Documentação do Projeto

POC de prontuário clínico por voz para fisioterapeutas.
Permite gravar sessões, transcrever com IA e consolidar notas automaticamente.

---

## Stack

| Camada      | Tecnologia                                      |
|-------------|-------------------------------------------------|
| Backend     | Python 3.12 + FastAPI + SQLite                  |
| Transcrição | Groq Whisper (`whisper-large-v3-turbo`)         |
| IA clínica  | Google Gemini (`gemini-2.5-flash-lite`)          |
| Frontend    | HTML/JS vanilla — SPA, PWA instalável           |
| Deploy      | Docker + docker-compose                         |

---

## Estrutura de Arquivos

### Raiz

| Arquivo              | Descrição |
|----------------------|-----------|
| `Dockerfile`         | Build da imagem Python 3.12-slim. Copia backend e frontend, expõe porta 8000. Roda `uvicorn main:app` a partir de `/app/backend`. |
| `docker-compose.yml` | Sobe o serviço com volume persistente `/data` para o SQLite e carrega variáveis do `.env`. |
| `.env.example`       | Template das variáveis de ambiente (`GOOGLE_AI_KEY`, `GROQ_API_KEY`). Copiar para `.env` e preencher antes de subir. |
| `.gitignore`         | Exclui `.env`, `*.db` e `__pycache__` do controle de versão. |
| `.dockerignore`      | Exclui arquivos sensíveis/desnecessários da imagem Docker. |
| `DOC.md`             | Este arquivo. |
| `CHANGELOG.md`       | Histórico de versões do app por release (formato `Beta-0.XX`). |

### Automação Claude (`.claude/commands/`)

| Arquivo              | Descrição |
|----------------------|-----------|
| `sonar.md`           | Slash command `/sonar`: exporta issues do SonarCloud via `scripts/export_sonar.py`, lê o JSON gerado e entrega análise priorizada com recomendações de correção. |

### Scripts (`scripts/`)

| Arquivo              | Descrição |
|----------------------|-----------|
| `export_sonar.py`    | Exporta issues abertos do SonarCloud para `scripts/sonar_issues.json`. Requer `SONAR_TOKEN` via env ou argumento. |
| `sonar_issues.json`  | Cache local dos issues exportados pelo SonarCloud (gerado automaticamente, não versionado). |

---

### Backend (`backend/`)

| Arquivo            | Descrição |
|--------------------|-----------|
| `main.py`          | Aplicação FastAPI. Define todos os endpoints REST (ver seção Endpoints abaixo). Monta o frontend como `StaticFiles` ao final — deve ficar por último para não interceptar as rotas da API. |
| `database.py`      | Camada de acesso ao SQLite. Gerencia 7 tabelas: `paciente`, `sessao`, `audio_chunk`, `sessao_consolidada`, `api_uso`, `documento` e `pacote`. Inclui `init_db()` para criação inicial e `_migrate()` para adicionar colunas/tabelas sem quebrar bancos existentes. O caminho do banco é configurável via `DB_PATH`. Inclui `get_faturamento_pacientes()` com filtros por mês de competência e paciente. |
| `ai.py`            | Integração com Google Gemini via `google-genai`. Modelo padrão: `gemini-2.0-flash-lite`. Expõe wrapper compatível com a interface Anthropic para manter o restante do código inalterado. Funções: `consolidar_sessao`, `resumir_historico`, `extrair_dados_paciente`, `extrair_dados_pacote`, `responder_pergunta`, `resumir_documento`, `complementar_anamnese`, `complementar_conduta`, `sugerir_conduta`, `gerar_sugestao_paciente`, `formatar_anamnese_texto`, `formatar_conduta_texto`, `sugestao_do_dia`, `feedback_clinico`, `interpretar_agendamento`, `interpretar_atestado`. Registra uso de tokens e custo em `api_uso` após cada chamada. |
| `transcribe.py`    | Integração com Groq Whisper via SDK OpenAI (compatível). Recebe bytes de áudio e retorna transcrição em português. |
| `requirements.txt` | Dependências: `fastapi`, `uvicorn[standard]`, `python-multipart`, `openai`, `google-genai`, `python-dotenv`, `aiofiles`, `pypdf`. |
| `start.bat`        | Script Windows para desenvolvimento local. Instala dependências e inicia o servidor em `localhost:8000` com hot-reload. |

---

### Frontend (`frontend/`)

| Arquivo                    | Descrição |
|----------------------------|-----------|
| `index.html`               | SPA completo da fisioterapeuta. Seções: lista de pacientes, perfil, gravação de sessão, billing, faturamento, agenda, NFS-e. Inclui drawer com painel de convite de secretaria e configurações. |
| `login.html`               | Login unificado fisio + secretaria via Google SSO. Redireciona para `/` (fisio) ou `/secretaria/` (secretaria) conforme o `role` retornado pelo backend. |
| `admin.html`               | Painel de administração: aprovar/revogar usuários fisio, aprovar/rejeitar convites de secretaria pendentes. |
| `secretaria/index.html`    | SPA da secretaria. Abas: Agenda, Atestado, Pacientes, Pacotes. Toda operação é escopada ao fisio vinculado. |
| `secretaria/login.html`    | Redirect simples para `/login.html` (login unificado). |
| `sw.js`                    | Service Worker para registrar o app como PWA instalável com suporte a atualização automática. |
| `manifest.json`            | Manifesto PWA: nome, ícones PNG (192×512), cor de tema, modo standalone. |
| `favicon.svg`              | Ícone SVG do app — iniciais "PN" em fundo preto. |
| `icon-192.png`             | Ícone PNG 192×192 para PWA (install prompt e tela inicial). |
| `icon-512.png`             | Ícone PNG 512×512 para PWA (splash screen). |

---

## Funcionalidades do Frontend

### Cadastro de paciente
- Fluxo por voz: fisioterapeuta fala o nome, data de nascimento, CPF e endereço
- IA extrai os campos e apresenta confirmação; anamnese e conduta de tratamento são registradas separadamente no perfil do paciente
- Alternativa: formulário manual ("Prefiro digitar")

### Anamnese e Conduta de Tratamento
- Seções independentes no perfil do paciente, acessíveis após o cadastro
- Cada seção possui accordion expansível com preview do conteúdo
- Complementação via voz: IA integra nova transcrição com o conteúdo existente
- Edição manual disponível como alternativa

### Sessão de atendimento
- Botão "+ Nova sessão" cria sessão e inicia o timer automaticamente
- Múltiplos áudios por sessão — cada um transcrito em tempo real pelo Groq
- Timer de auto-encerramento (configurável via `AUTO_CLOSE_MINUTES`) com countdown visual por sessão
- Timer persiste em `localStorage` por sessão (`physio_ac_{id}`) — sobrevive a F5 e troca de paciente
- Ao encerrar: IA consolida todos os chunks em nota clínica profissional (texto corrido)
- **Regra de pacote:** encerrar uma sessão abate 1 sessão do pacote ativo do paciente
- **Adicionar nota em sessão do dia:** botão "+ Nota" aparece em sessões encerradas do mesmo dia — adiciona áudio sem abater do pacote, re-consolida a nota

### Perfil do paciente
- Anamnese exibida no topo
- **Pacote de sessões:** card com barra de progresso, sessões restantes, alerta visual quando ≤ 2 restam
- Sessão aberta (se houver) com botões Gravar / Encerrar
- Duas abas: **Histórico de sessões** (padrão) e **Documentos**
- Histórico com filtro por texto/data e botão de limpar busca
- Pergunta ao histórico: chips de perguntas rápidas + input livre + resposta lida em voz alta

### Documentos (PDFs)
- Upload de PDF no prontuário do paciente
- IA gera resumo clínico do conteúdo
- Resumo recolhível (clica para expandir)
- Visualizador de PDF inline (iframe)
- Exclusão lógica (soft delete — mantém no banco)

### Billing
- Rastreamento de tokens e custo por chamada de IA
- Exibição em R$ com cotação USD/BRL em tempo real (AwesomeAPI), fallback R$5,70
- Filtro por mês/ano
- Cards: total de chamadas, tokens de entrada, tokens de saída, custo total
- Tabela detalhada por tipo de chamada
- Projeção de custo até fim do mês (baseada na média diária)
- Histórico de meses anteriores

### Faturamento
- Seção acessível via menu lateral (drawer)
- Filtros por mês de competência e paciente
- KPI com total recebido (pacotes + procedimentos extras)
- Cards de pacotes com botão **Emitir NFS-e** — abre preview com ISS calculado
- Cards de procedimentos extras cobrados por sessão
- Botão "Gerar extrato" → `#print-view` com tabela imprimível

### Notas Fiscais de Serviço (NFS-e demo)
- Seção dedicada acessível via menu lateral (drawer)
- Lista de NFS-e emitidas com busca por paciente/número/descrição
- Emissão via botão no card do pacote no faturamento: preview antes de confirmar
- NFS-e contém: número sequencial 7 dígitos, código de verificação hex, prestador/tomador, ISS 2%, valor líquido
- Badge "DEMONSTRAÇÃO" — dados fictícios sem validade fiscal
- Opção de cancelar nota emitida
- Visualização detalhada com layout de nota fiscal real imprimível

### PWA
- Instalável via Chrome/Edge ("Add to Home Screen")
- Ícone personalizado (PN) com PNG nas resoluções 192 e 512
- Service worker com verificação de atualização a cada abertura

### Offline
- Exibe overlay "Sem internet" quando não há conexão com o servidor
- Funcionalidades que exigem internet: transcrição (Groq), consolidação IA (Anthropic)
- Funcionalidades que funcionam sem internet (se backend for local): cadastro, listagem, histórico, PDFs

---

## Endpoints da API

### Pacientes
| Método | Rota | Descrição |
|--------|------|-----------|
| `GET` | `/pacientes` | Lista pacientes ativos com `sessoes_restantes` do pacote ativo |
| `POST` | `/pacientes` | Cria paciente |
| `GET` | `/pacientes/{id}` | Busca paciente por ID |
| `PUT` | `/pacientes/{id}` | Atualiza nome, data nascimento, anamnese e conduta de tratamento |
| `DELETE` | `/pacientes/{id}` | Soft delete do paciente e suas sessões |
| `POST` | `/pacientes/{id}/complementar-anamnese` | Integra nova transcrição à anamnese existente via IA |
| `POST` | `/pacientes/{id}/complementar-conduta` | Integra nova transcrição à conduta de tratamento existente via IA |

### Sessões
| Método | Rota | Descrição |
|--------|------|-----------|
| `POST` | `/sessoes` | Cria sessão aberta para um paciente |
| `GET` | `/sessoes/{id}` | Busca sessão com chunks e consolidado |
| `POST` | `/sessoes/{id}/audio` | Upload de áudio → transcreve e salva chunk |
| `POST` | `/sessoes/{id}/encerrar` | Encerra sessão → consolida com IA → abate 1 do pacote |
| `POST` | `/sessoes/{id}/adicionar-audio` | Adiciona áudio a sessão encerrada do mesmo dia (sem abate) |
| `DELETE` | `/sessoes/{id}` | Cancela (sem áudio) ou soft delete |
| `GET` | `/pacientes/{id}/sessoes` | Lista sessões do paciente com consolidados |

### Documentos
| Método | Rota | Descrição |
|--------|------|-----------|
| `POST` | `/pacientes/{id}/documentos` | Upload de PDF → extrai texto → resumo IA |
| `GET` | `/pacientes/{id}/documentos` | Lista documentos do paciente |
| `GET` | `/documentos/{id}/arquivo` | Serve o PDF para visualização |
| `DELETE` | `/documentos/{id}` | Soft delete do documento |

### Pacotes de Sessões
| Método | Rota | Descrição |
|--------|------|-----------|
| `POST` | `/pacientes/{id}/pacotes` | Cria pacote (total_sessoes, valor_pago, data_pagamento, descricao) |
| `GET` | `/pacientes/{id}/pacotes` | Lista pacotes do paciente |
| `DELETE` | `/pacotes/{id}` | Soft delete do pacote |

### IA / Outros
| Método | Rota | Descrição |
|--------|------|-----------|
| `POST` | `/transcrever` | Transcrição avulsa de áudio |
| `POST` | `/extrair-paciente` | Extrai dados do paciente de uma transcrição |
| `POST` | `/extrair-pacote` | Extrai dados do pacote (sessões, valor, data, descrição) de uma transcrição |
| `GET` | `/pacientes/{id}/resumo` | Gera resumo clínico completo (histórico + documentos) |
| `POST` | `/pacientes/{id}/perguntar` | Responde pergunta sobre o histórico do paciente |
| `POST` | `/relatorio/crefito` | Gera relatório CREFITO para múltiplos pacientes |
| `GET` | `/billing?mes=YYYY-MM` | Retorna uso e custo de IA do mês |
| `GET` | `/faturamento/pacientes?mes=YYYY-MM&paciente_id=X` | Retorna pacotes com valor pago, filtrado por mês de competência e/ou paciente |

### Secretaria
| Método | Rota | Descrição |
|--------|------|-----------|
| `POST` | `/admin/secretaria/vincular` | Fisio convida secretaria — cria vínculo com `status=pendente` |
| `DELETE` | `/admin/secretaria/desvincular` | Fisio cancela convite ou remove vínculo |
| `GET` | `/admin/secretaria` | Retorna secretaria convidada/vinculada ao fisio logado (com status), ou null |
| `GET` | `/admin/secretaria/pendentes` | Admin lista todos os convites aguardando aprovação |
| `POST` | `/admin/secretaria/{email}/aprovar` | Admin aprova convite — status passa para `ativa` |
| `DELETE` | `/admin/secretaria/{email}/rejeitar` | Admin rejeita convite — remove registro |
| `GET` | `/sec/pacientes` | Secretaria lista pacientes do fisio vinculado |
| `POST` | `/sec/pacientes` | Secretaria cria paciente para o fisio vinculado |
| `GET` | `/sec/pacientes/{id}/pacotes` | Secretaria lista pacotes do paciente |
| `POST` | `/sec/pacientes/{id}/pacotes` | Secretaria cria pacote para o paciente |

### Notas Fiscais de Serviço (demo)
| Método | Rota | Descrição |
|--------|------|-----------|
| `POST` | `/notas-fiscais` | Emite NFS-e demo (gera número sequencial, código de verificação, dados_json) |
| `GET` | `/notas-fiscais?q=texto&paciente_id=X&competencia=YYYY-MM` | Lista NFS-e filtrada; retorna também `competencias_disponiveis` e `pacientes_disponiveis` para popular os pickers |
| `GET` | `/notas-fiscais/{id}` | Busca NFS-e por ID |
| `DELETE` | `/notas-fiscais/{id}` | Cancela a nota (status = 'cancelada') |

---

## Banco de Dados

```
paciente           → dados + anamnese + soft delete (deletado_em)
sessao             → atendimento (aberta/encerrada) + soft delete
audio_chunk        → cada trecho de áudio transcrito
sessao_consolidada → nota clínica gerada pela IA ao encerrar sessão
api_uso            → log de chamadas IA (tokens + custo_usd)
documento          → PDFs enviados ao prontuário + resumo IA + soft delete
pacote             → pacotes de sessões comprados pelo paciente + soft delete
procedimento_extra → procedimentos extras cobrados por sessão (detecção automática IA + manual) + soft delete
nota_fiscal        → NFS-e demo emitida pelo sistema (número sequencial, código verificação, dados_json)
secretaria_link    → vínculo fisio↔secretaria com status (pendente/ativa) e data de criação
usuario_google     → config por usuário (valor_sessao_avulsa, cobrar_avulsa, google_refresh_token)
```

### Regras de negócio do pacote
- Ao **encerrar** uma sessão → abate 1 do pacote ativo (se houver)
- Ao **adicionar nota** em sessão encerrada do mesmo dia → **não abate**
- Pacote ativo = mais recente com `sessoes_usadas < total_sessoes`
- Badge de alerta aparece quando restam ≤ 2 sessões

---

## Variáveis de Ambiente

| Variável            | Obrigatória | Descrição |
|---------------------|-------------|-----------|
| `ANTHROPIC_API_KEY` | Sim         | Chave da API Anthropic (Claude) |
| `GROQ_API_KEY`      | Sim         | Chave da API Groq (Whisper) |
| `DB_PATH`           | Não         | Caminho do SQLite (padrão: `backend/physio_notes.db`; Docker: `/data/physio_notes.db`) |

---

## Deploy com Docker

```bash
# 1. Clonar o repositório
git clone https://github.com/seu-usuario/seu-repo.git
cd "Physio Notes"

# 2. Configurar variáveis de ambiente
cp .env.example .env
# editar .env com as chaves reais

# 3. Build e iniciar
docker compose up -d --build

# 4. Acessar
# http://localhost:8000  (local)
# http://IP_DO_SERVIDOR:8000  (servidor)

# Comandos úteis
docker compose logs -f         # acompanhar logs
docker compose down            # parar
docker compose up -d --build   # atualizar após mudanças no código
```

---

## Desenvolvimento Local (sem Docker)

```bash
# Backend
cd backend
pip install -r requirements.txt
uvicorn main:app --reload --port 8000

# Acessar: http://localhost:8000
# (o backend serve o frontend também via StaticFiles)
```

> **Nota:** Em dev local, o frontend detecta automaticamente que está na porta 8000 e usa `localhost:8000` como `API_BASE`. No Docker/servidor, usa URLs relativas (mesmo origin).
