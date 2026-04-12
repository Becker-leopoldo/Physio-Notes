# Changelog — Physio Notes

Todas as mudanças relevantes por versão. Usado como corpo do commit/tag de release.

---

## Beta-0.290 — 2026-04-12

### Correções
- **CSS:** removida propriedade `border: none` duplicada em `.top-bar-action` (`css:S4656`)
- **CSS:** removida propriedade `display: inline-block` duplicada em `.ai-bubble::before` (`css:S4656`)
- **JS:** removida expressão morta `.parentElement` sem efeito na lista de pacientes (`javascript:S905`)

---

## Beta-0.289 — 2026-04-12

### Melhorias
- **Backend:** 4 endpoints FastAPI migrados para `Annotated` type hints na injeção de dependência (`UploadFile`) — elimina 4 BLOCKERs Sonar (`python:S8410`)

---

## Beta-0.288 — 2026-04-12

### Correções
- **Segurança:** validação de `event_id` nas rotas `/agenda/google/{event_id}` e `/sec/agendamento/{event_id}` para prevenir path traversal (Sonar `pythonsecurity:S7044`)
- **Backend:** escrita de arquivo em upload de documentos convertida para API assíncrona (`aiofiles`) — não bloqueia mais o event loop do FastAPI (`python:S7493`)
- **Frontend:** variável `_agendaListaExpandida` declarada explicitamente com `let` — eliminada variável global implícita (`javascript:S2703`)

---

## Beta-0.287 — 2026-04-12

### Funcionalidades
- **Fluxo de convite para secretaria**: fisio convida secretaria pelo e-mail; admin aprova/rejeita; secretaria loga pelo SSO normalmente após aprovação
- **Coluna `status` em `secretaria_link`**: suporte a status `pendente` e `ativa` com migração automática do banco existente
- **Login unificado** (`/login.html`): fisio e secretaria usam a mesma tela; token e redirecionamento são definidos pelo papel (`role`) retornado pelo backend
- **Painel de convite no drawer**: estado dinâmico mostra formulário de convite ou status atual do convite (⏳ aguardando / ✓ ativa)
- **Novos endpoints admin**: `GET /admin/secretaria/pendentes`, `POST /admin/secretaria/{email}/aprovar`, `DELETE /admin/secretaria/{email}/rejeitar`
- **Painel admin atualizado**: seção de convites de secretaria pendentes com aprovação/rejeição
- **Secretaria — pacientes e pacotes**: abas Pacientes e Pacotes no app da secretaria, com CRUD completo escopo ao fisio vinculado

### Correções
- Null-safety em `get_secretaria_do_fisio` e `admin_get_secretaria`: evita crash quando `fisio_email` é `None`
- `secretaria/login.html` redireciona para `/login.html` unificado

---

## Beta-0.286 — 2026-04-08

### Segurança — Q.A de Segurança Finalizado

- **Fail-fast no JWT_SECRET (`google_auth.py`)**: Inicialização da aplicação agora é sumariamente bloqueada caso a variável de ambiente não esteja configurada, prevenindo o uso de chaves hardcoded e falsificação de tokens.
- **Defesa Antispoofing de IP (`main.py`)**: Extração de IP em `_client_ip()` não confia mais no header livre `X-Forwarded-For`, utilizando a conexão nativa socket via delegamento explícito para o uvicorn, protegendo logs de auditoria contra contorno malicioso.
- **Mitigação de DoS / Denial of Wallet (`main.py`)**: Inseridas travas de *Rate Limit* (`@limiter.limit("20/minute")`) em todos os endpoints remanescentes de extração LLM, barrando explorações assíncronas de faturamento da API (Groq/Anthropic).
- **Sandboxing XML contra Prompt Injection (`ai.py`)**: Adicionado isolamento de instrução mestre via parâmetro `system_prompt` da Anthropic e tags delimitadoras (`<transcricao_crua>`) sobre fala processada garantindo que a IA lide com transcrições de usuários meramente como dados passivos inexecutáveis.
- **Proteção PII Rigorosa nas Bases (LGPD) (`database.py`)**: Mecanismo de fallback que salvava silenciosamente em *plaintext* desabilitado. Leituras e escritas sem posse legível do `FIELD_ENCRYPTION_KEY` geram *ValueError/Fail-Fast*, não burlando lei de dados privados.

---

## Beta-0.285 — 2026-04-06

### Melhorias
- Chips "Perguntar ao Histórico": "Check clínico" renomeado para "Evolução do paciente" e "Feedback clínico" renomeado para "Pendências"

---

## Beta-0.284 — 2026-04-06

### Funcionalidades

**Módulo Secretaria — MVP (Fase 1)**

_Backend_
- Tabela `secretaria_link`: vínculo 1:1 entre e-mail da secretaria e o fisio
- JWT com campo `role` (`fisio` ou `secretaria`) e `fisio_email` para secretaria
- Login Google auto-detecta secretaria vinculada → emite token com role correto
- `POST /admin/secretaria/vincular` — fisio vincula e-mail da secretaria
- `DELETE /admin/secretaria/desvincular` — remove vínculo
- `GET /admin/secretaria` — consulta secretaria vinculada
- `GET /sec/pacientes` — lista nomes + IDs (sem dados clínicos)
- `GET /sec/agenda` — agenda do fisio (sessões + Google Calendar)
- `POST /sec/agendamento/interpretar` — IA interpreta pedido de agendamento
- `POST /sec/agendamento/confirmar` — cria evento no GCal do fisio
- `DELETE /sec/agendamento/{id}` — cancela evento no GCal do fisio
- `POST /sec/atestado/interpretar` — IA interpreta atestado em nome do fisio

_Frontend — `/secretaria/login.html`_
- Login Google com verificação de role (`secretaria`)
- Redireciona para o app principal após autenticação

_Frontend — `/secretaria/index.html`_
- App web responsivo mobile-first (não PWA)
- Bottom navigation: **Agenda** + **Atestado**
- Agenda: calendário mensal com dots coloridos + painel do dia + cancelar evento Google Calendar
- Novo agendamento: voz (microfone) ou texto → IA interpreta → confirma/sugestões alternativas
- Atestado: seletor de paciente + voz ou texto → IA interpreta → revisão → gera PDF

_App do fisio_
- Drawer → item "Secretaria" → painel inline para vincular/desvincular e-mail da secretaria

---

## Beta-0.283 — 2026-04-06

### Correções

- **Backend:** `db.get_paciente` chamado com argumento `owner` inválido — causava TypeError e 500
- **Frontend:** requisição `/atestado/interpretar` enviada sem `Content-Type: application/json` — FastAPI retornava 422
- Removido botão "Tentar interpretar" — fluxo agora é 100% automático: voz → transcrição → IA → formulário

---

## Beta-0.282 — 2026-04-06

### Correções

- Transcrição do atestado usava campo `texto` — backend retorna `transcricao`; sempre retornava string vazia

---

## Beta-0.281 — 2026-04-06

### Correções

- Transcrição do atestado usava `fetch` manual sem `API_BASE` — substituído por `apiFetch` igual ao restante do app

---

## Beta-0.280 — 2026-04-06

### Correções

- Atestado não travava mais em "Interpretando…": adicionado tratamento de erro robusto em toda a cadeia de transcrição → interpretação
- Transcrição vazia agora exibe mensagem e reseta o microfone

---

## Beta-0.279 — 2026-04-06

### Melhorias

- Atestado interpreta automaticamente após transcrição — sem necessidade de clicar em "Interpretar"
- Bubble exibe o texto transcrito corretamente antes de interpretar

---

## Beta-0.278 — 2026-04-06

### Correções

- Ícone do microfone visível (SVG com `fill="white"` igual aos outros modais)
- UX do sheet de atestado alinhado ao padrão "Novo Paciente": bubble IA, botão "Prefiro digitar", animação de gravação

---

## Beta-0.277 — 2026-04-06

### Correções

- Sheet do atestado movido para fora da `<section id="sec-agenda">` — estava oculto pelo `display:none` da section ao abrir um paciente

---

## Beta-0.276 — 2026-04-06

### Correções

- CSS do `#at-sheet` movido para o `<style>` global — antes era injetado apenas ao abrir a Agenda

---

## Beta-0.275 — 2026-04-06

### Funcionalidades

**Atestado de Fisioterapia**
- Botão "Atestado" no header do paciente (ao lado de "Editar")
- Bottom sheet com entrada por voz (microfone) ou texto livre
- IA especializada em fisioterapia interpreta o relato e extrai: data, horário de início/fim, motivo e conduta realizada
- Termos técnicos do fisio (TENS, RPG, Pilates clínico, laserterapia etc.) são preservados e/ou convertidos para linguagem clínica formal
- Formulário de revisão completo antes de gerar — todos os campos editáveis
- Campo CREFITO salvo no localStorage para reutilização
- "Gerar PDF" abre nova aba com atestado formatado e botão de imprimir/salvar PDF
- Assinatura inclui nome do fisio logado e CREFITO (quando informado)

---

## Beta-0.274 — 2026-04-06

### Correções

- Corrigido erro `_agendaCarregarGoogle is not defined` ao confirmar cancelamento de evento — substituído pela chamada correta `_agendaCarregar()`

---

## Beta-0.273 — 2026-04-06

### Funcionalidades

**Cancelar agendamento Google Calendar**
- Clicar em evento Google (marcado com ícone de 3 pontos) abre sheet de confirmação
- Sheet exibe nome do evento, data e horário antes de confirmar
- Botão "Cancelar agendamento" chama `DELETE /agenda/google/{event_id}` e remove o evento
- Botão "Voltar" fecha sem ação
- Após cancelamento: agenda recarrega automaticamente (evento some do grid e lista)

### Correções

**Busca por nome insensível a acentos no Google Calendar**
- Digitar "Erica" agora encontra eventos com "Érica" no título
- Backend envia a query original E a versão normalizada (sem acentos) para a API do Google
- Filtro adicional server-side valida os resultados por correspondência normalizada

---

## Beta-0.263 — 2026-04-06

### Melhorias

**Bottom sheet "Novo evento" — redesign mobile-first**
- Sheet ocupa ≥60% da tela, bordas arredondadas 24px, animação fluida
- Microfone como herói central (80px, pulsante quando gravando, label dinâmico: "Gravando…" / "Processando…")
- Campo de texto abaixo como alternativa, fonte 16px (evita zoom automático do iOS)
- Botão "Verificar disponibilidade" de toque fácil (56px de altura, borda 14px)
- Após análise: área de input colapsa, resultado ocupa toda a tela — botão "Alterar pedido" para voltar
- Resultado: card 18px, sugestões com área de toque mínimo 60px
- Botão "Confirmar agendamento" centralizado, 56px altura
- Sem zoom/scroll desnecessário — tudo cabe na tela sem rolagem extra

---

## Beta-0.262 — 2026-04-06

### Melhorias

- Botão "Novo" movido para a top-bar da agenda (ao lado do título), integrado ao design — removido o FAB fixo no canto da tela

---

## Beta-0.261 — 2026-04-06

### Funcionalidades

**Novo agendamento por voz ou texto na Agenda**
- Botão FAB "+" fixo na tela de agenda — abre bottom sheet deslizante
- Fisio fala ou digita: nome da pessoa, data e horário (ex: "Sessão com Ana amanhã das 14h às 15h")
- IA interpreta a linguagem natural e extrai nome, data e horários exatos
- Verifica disponibilidade em tempo real no Google Calendar via API freebusy
- Se disponível: exibe card verde com confirmação
- Se ocupado: exibe card vermelho + até 4 sugestões de horário livre (mesmo dia ±1h e ±2h, próximos dias mesmo horário)
- Usuário seleciona uma sugestão tocando nela; botão "Confirmar" cria o evento no Google Calendar
- Após confirmação, agenda recarrega automaticamente com o novo evento
- Novos endpoints backend: `POST /agenda/interpretar` e `POST /agenda/confirmar`
- Nova função AI: `interpretar_agendamento()` com parsing de expressões relativas ("amanhã", "sexta", "semana que vem")

---

## Beta-0.260 — 2026-04-06

### Melhorias

**Agenda — UX mobile redesenhada (v2)**
- Grid: células agora mostram apenas dots coloridos (sem texto), muito mais limpas em mobile
- Painel do dia selecionado: cabeçalho com nome do dia + contador de eventos; strip lateral colorida por tipo; badge de status (Em aberto / Encerrada)
- Lista de agenda abaixo: cabeçalho de data no estilo Google Calendar (número grande + dia/mês/semana); strip lateral colorida; badge inline de status
- Remoção de todos os elementos com texto no grid que causavam overflow no mobile
- Animação suave `fadeSlideIn` ao abrir painel do dia
- Botões de navegação de mês com efeito hover em círculo

---

## Beta-0.259 — 2026-04-06

### Funcionalidades

**Integração com Google Calendar**
- Login agora solicita permissão de acesso ao Google Calendar (escopo `calendar`) via OAuth2 popup
- `refresh_token` salvo por usuário no banco, trocado por `access_token` a cada uso
- Ao encerrar uma sessão, evento é criado automaticamente no Google Calendar primário do fisio: título "Physio — {paciente}", duração 1 hora, cor verde, descrição com resumo da sessão
- Novo endpoint `GET /agenda/google?mes=YYYY-MM` busca eventos do mês no Google Calendar do usuário logado
- Novo arquivo `calendar_service.py` (fire-and-forget, nunca quebra o fluxo principal)

**Agenda redesenhada — UX mobile-first estilo Google Calendar**
- Eventos demo/fictícios removidos completamente
- Agenda exibe todos os eventos reais do Google Calendar do fisio + sessões internas do Physio Notes
- Grid do mês com indicadores de ponto coloridos (dots) no lugar de labels de texto
- Seleção de dia: tocar na data exibe painel com os eventos daquele dia
- Lista abaixo do grid agrupada por data com cabeçalhos, horário e ponto colorido por tipo
- Cores dos eventos do Google Calendar mapeadas pelo `colorId` (11 cores)
- Badge de conexão com Google Calendar na barra do mês

### Melhorias

- `google_auth.py` reescrito para fluxo de código de autorização (substitui o fluxo de ID token que não concedia escopo de Calendar)
- `login.html` usa `initCodeClient` com `ux_mode: 'popup'` — sem redirecionamento de página
- `database.py`: coluna `google_refresh_token` adicionada via migração automática
- `requirements.txt`: adicionado `httpx`

---

## Beta-0.258 — 2026-04-03

### Funcionalidades

**Chip "Feedback clínico" no Perguntar ao histórico**
- Novo chip "✦ Feedback" em destaque âmbar, diferenciado dos chips de IA azuis
- Analisa a conduta de tratamento planejada versus o que foi registrado nas evoluções diárias (últimas 10 sessões)
- Retorna 3 seções: itens da conduta ainda não registrados nas sessões, pontos das evoluções que merecem revisão no plano, e aspectos bem conduzidos
- Tom sutil e construtivo — usa "ainda não registrado", "vale considerar", nunca linguagem acusatória
- Novo endpoint `POST /pacientes/{id}/feedback-clinico` no backend
- Novo prompt `feedback_clinico` no `ai.py` com sliding window de 10 sessões

---

## Beta-0.257 — 2026-04-03

### Funcionalidades

**Check clínico e Sugestão do Dia (chips de IA)**
- Chips "✦ Sugestão do Dia" e "✦ Check clínico" adicionados no início da seção "Perguntar ao histórico", com visual azul de destaque
- **Check clínico**: gera sugestões estruturadas de reavaliação, testes fisioterapêuticos e exames clínicos com base na anamnese e últimas 8 sessões
- **Sugestão do Dia**: gera orientação prática para a sessão atual — foco, técnicas, progressão e pontos de atenção — com base na anamnese e últimas 3 sessões

**Formatação automática de anamnese e conduta**
- Ao salvar anamnese manualmente, a IA reorganiza o texto em tópicos no padrão `**TÓPICO:**` sem alterar o conteúdo
- Ao salvar conduta manualmente, a IA formata o texto da mesma forma antes de gravar
- Renderização visual de tópicos no frontend (`renderTopicos`): cada `**TÓPICO:**` vira um bloco visual com label e conteúdo separados

**Conduta gerada automaticamente após anamnese**
- Ao salvar anamnese (manual ou por voz) em paciente sem conduta, a IA gera a conduta de tratamento de forma síncrona
- Frontend atualiza o card de conduta imediatamente após o retorno do endpoint — sem necessidade de reload

### Melhorias

- "Perguntar ao histórico" agora inclui conduta de tratamento no contexto enviado à IA
- Histórico vazio passou a exibir mensagem explícita ao invés de string vazia no prompt da IA
- Check clínico e Sugestão do Dia não bloqueiam mais a geração quando a anamnese é `null` ou vazia — passam o contexto disponível e a IA responde com base no histórico de sessões

### Correções

- Bug: anamnese salva por voz não gerava conduta automaticamente — corrigido com geração síncrona no endpoint `complementar-anamnese`
- Bug: "Check clínico" e "Sugestão do Dia" retornavam erro 400 mesmo com anamnese preenchida — removido gate desnecessário; endpoints agora sempre geram com o que estiver disponível

---

## Beta-0.256 — 2026-04-03

### Melhorias

**Agenda: sessões demo + paginação**
- Sessões fictícias geradas no frontend a partir dos pacientes do próprio fisio (cada um vê apenas os seus)
- Geração determinística via LCG com seed = patient ID — resultados consistentes entre reloads
- 8–18 sessões por paciente com intervalo de 3–7 dias distribuídas até 31/12/2026
- Sessões demo exibidas em cinza, distinguíveis das reais (verde/azul)
- KPIs do mês incluem demo para apelo visual
- Lista do mês paginada em 20 itens com botão "Ver mais N sessões"
- Legenda de cores adicionada abaixo do grid
- Demo events não clicáveis; eventos reais continuam abrindo o paciente

---

## Beta-0.255 — 2026-04-03

### Funcionalidades

**Área de Agenda / Calendário**
- Nova seção "Agenda" no menu lateral
- Vista mensal com grade de 7 colunas (Seg–Dom) e navegação por mês/ano
- Sessões existentes aparecem como eventos coloridos no calendário (verde = encerrada, azul = aberta)
- KPIs do mês: total de sessões, em aberto e encerradas
- Lista de sessões do mês abaixo do calendário com link direto ao paciente
- Clique em qualquer evento ou linha da lista → abre o paciente
- Banner "Integrar com Google Calendar" com badge "Em breve" e botão desabilitado
- Novo endpoint `GET /agenda?mes=YYYY-MM` retornando sessões do owner autenticado

---

## Beta-0.254 — 2026-04-03

### Correções

**Bug: faturamento quebrando após fix do Beta-0.253**
- JOIN com tabela `paciente` tornava `criado_em` e `data_pagamento` ambíguos no SQLite
- Corrigido qualificando todas as colunas com nome da tabela (`pacote.criado_em`, `procedimento_extra.data`, etc.)

---

## Beta-0.253 — 2026-04-03

### Correções

**Bug: faturamento exibia pacientes e meses de outros usuários**
- `get_faturamento_pacientes`: queries de `meses_disponiveis` e `pacientes_disponiveis` não aplicavam filtro por `owner_email`
- Usuário autenticado via Google SSO via dados cruzados de outra conta
- Corrigido com JOIN na tabela `paciente` filtrando por `p.owner_email` em ambas as queries

---

## Beta-0.252 — 2026-04-02

### Segurança

**Logging de auditoria**
- Tabela `audit_log` no banco: `criado_em`, `owner_email`, `acao`, `detalhe`, `ip`
- Eventos registrados: login (sucesso/falha/negado), criar/atualizar/deletar paciente, criar/encerrar/cancelar/deletar sessão, upload/deletar documento, criar/deletar pacote, emitir/cancelar nota fiscal, admin aprovar/revogar usuário
- IP extraído do header `X-Forwarded-For` (proxy reverso) com fallback para `request.client.host`
- Endpoint admin `GET /admin/audit-log?owner=&limit=` para visualização
- Fire-and-forget: falha no log nunca interrompe a operação principal

---

## Beta-0.251 — 2026-04-02

### Segurança

**Blind Index para unicidade de CPF criptografado**
- Substituído check O(n) em memória por índice único no banco via HMAC-SHA256 (`cpf_hash`)
- Derivação de chave com separação de contexto: `SHA256("enc:" + key)` para Fernet, `SHA256("hash:" + key)` para HMAC
- Coluna `cpf_hash` adicionada à tabela `paciente` com unique index `(cpf_hash, owner_email) WHERE cpf_hash IS NOT NULL AND deletado_em IS NULL`
- Migração automática: `_migrar_criptografar_pii()` agora também popula `cpf_hash` para registros existentes
- `atualizar_paciente` captura `IntegrityError` do DB (constraint violada) em vez de `ValueError`

---

## Beta-0.250 — 2026-04-02

### Segurança

**Criptografia de CPF e endereço em repouso (LGPD)**
- Campos `cpf` e `endereco` da tabela `paciente` agora são criptografados com Fernet (AES-128-CBC + HMAC)
- Chave configurável via `FIELD_ENCRYPTION_KEY` no `.env`
- Prefixo `enc:` permite detectar campos já criptografados (migração transparente)
- Migração automática: na primeira inicialização com a chave configurada, criptografa todos os dados plaintext existentes
- Índice único em CPF removido (incompatível com criptografia não-determinística)
- Se `FIELD_ENCRYPTION_KEY` não estiver configurada, dados continuam em plaintext com aviso no log

---

## Beta-0.249 — 2026-04-02

### Segurança

**Rate limiting nos endpoints de autenticação WebAuthn**
- `POST /auth/register/begin` → 5/minuto por IP
- `POST /auth/register/complete` → 5/minuto por IP
- `POST /auth/login/begin` → 10/minuto por IP
- `POST /auth/login/complete` → 10/minuto por IP
- Protege contra brute force e enumeração de usuários

---

## Beta-0.248 — 2026-04-02

### Melhorias

**Modal de valor avulsa integrado ao fluxo de encerramento**
- Pergunta "Qual o valor da sessão?" aparece como modal centralizado ANTES de processar a IA
- Fluxo: clicar Encerrar → modal de valor → confirmar → IA processa e fecha
- Botões "Confirmar e encerrar" e "Encerrar sem valor"
- Campo pré-preenchido com valor configurado nas configurações (se houver)
- Se checkbox "Cobrar automaticamente" marcado E valor configurado → auto-cobra sem modal
- Se checkbox desmarcado OU sem valor configurado → modal sempre abre

---

## Beta-0.247 — 2026-04-02

### Melhorias

**Faturamento: horário e ordenação por criação**
- Cada item do faturamento exibe data + horário (HH:MM) de criação — ex: "02/04 · 14:30"
- Itens dentro de cada grupo ordenados por `criado_em` (mais recente primeiro)
- Sem mudanças no backend — `criado_em` já era retornado, apenas passou a ser usado no frontend

---

## Beta-0.246 — 2026-04-02

### Correções

**Prompt de valor avulsa sempre aparece quando checkbox desmarcado**
- Checkbox desmarcado → sessão encerra sem auto-cobrar, mas prompt flutuante sempre abre para o fisio digitar o valor manualmente
- Checkbox marcado + valor configurado → cobra automaticamente, sem prompt
- Checkbox marcado + sem valor → prompt abre com valor detectado pela IA pré-preenchido (se houver)
- Backend retorna `valor_ai_detectado` sempre (mesmo quando não cobrou) para pré-preencher o prompt
- Prompt exibe dica "IA detectou R$ X no áudio" quando detectou valor

---

## Beta-0.245 — 2026-04-02

### Funcionalidades

**Checkbox "Cobrar automaticamente" para sessão avulsa**
- Novo checkbox no painel de Configurações → "Cobrar automaticamente ao encerrar" (padrão: marcado)
- Se marcado + valor configurado → ao encerrar sessão avulsa, cobrança gerada automaticamente
- Se marcado + sem valor → IA tenta detectar valor do áudio; se não encontrar, aparece prompt flutuante "Qual o valor da sessão?" com 30s antes de fechar
- Se desmarcado → sessão encerra sem gerar cobrança
- Campo de valor fica desabilitado/opaco quando checkbox desmarcado
- Configuração persistida no banco (`cobrar_avulsa` em `usuario_google`)

---

## Beta-0.244 — 2026-04-02

### Funcionalidades

**Checkbox "Cobrar automaticamente" nas Configurações**
- Novo campo no painel de Configurações → Sessão avulsa: checkbox "Cobrar automaticamente" (padrão: marcado)
- Se marcado + valor configurado → ao encerrar sessão avulsa, cobrança gerada automaticamente sem nenhum modal
- Se marcado + sem valor → após fechar a sessão, aparece prompt flutuante "Qual foi o valor?" (30s)
- Se desmarcado → sessão encerra sem cobrança, sem prompt
- Campo de valor fica desabilitado/opaco quando checkbox desmarcado
- Configuração salva no banco (`cobrar_avulsa` em `usuario_google`)

### Correções

- **CSP**: adicionado `https://accounts.google.com` ao `style-src` — corrige erro de bloqueio do stylesheet do Google Sign-In após o endurecimento de headers de segurança da Beta-0.242
- Removido modal por sessão (`abrirModalEncerrarAvulsa`) — substituído pelo comportamento configurado globalmente

---

## Beta-0.243 — 2026-04-02

### Funcionalidades

**Cobrança de sessão avulsa com controle do fisio**

Ao encerrar uma sessão sem pacote, o app agora exibe um modal antes de processar:
- **Checkbox "Cobrar esta sessão?"** — marcado por padrão
- **Campo de valor** pré-preenchido com o valor configurado em Configurações (editável)
- Se o fisio deixou o valor em branco, a **IA tenta detectar o valor na transcrição** (ex: "cobrei 120 reais de sessão")
- Se nenhum valor foi encontrado e a cobrança está ativa, exibe **prompt flutuante pós-fechamento** ("Qual foi o valor desta sessão?") com 30s para responder
- Se checkbox desmarcado: sessão encerra normalmente sem cobrança
- O fluxo de sessões **com pacote não é alterado**

Mudanças técnicas:
- `ai.py`: nova função `extrair_valor_sessao()` — extração leve (max_tokens=32) de valor monetário na transcrição
- `database.py`: `encerrar_sessao()` aceita `cobrar: bool` e `valor_override: float | None`
- `main.py`: `EncerrarBody` model; endpoint encerrar aceita body JSON; AI extraction antes de chamar o banco
- `frontend`: `encerrarSessao()` intercepta avulsas → `abrirModalEncerrarAvulsa()` → `_executarEncerrarSessao()` → `_abrirPromptValorAvulsa()` (se necessário)

---

## Beta-0.242 — 2026-04-02

### Segurança — Q.A completo (9 correções)

1. **JWT_SECRET obrigatório**: servidor falha na inicialização se `JWT_SECRET` não estiver definida como variável de ambiente — elimina o risco de usar o fallback fraco hardcoded
2. **Ownership em 5 endpoints descobertos**: `PUT /procedimentos/{id}`, `DELETE /procedimentos/{id}`, `GET /notas-fiscais/{id}`, `DELETE /notas-fiscais/{id}` e `POST /sessoes` agora verificam se o recurso pertence ao usuário autenticado antes de operar
3. **Novos helpers de autorização**: `_verificar_dono_procedimento` e `_verificar_dono_nf` adicionados; `get_procedimento(proc_id)` adicionado ao `database.py`
4. **Limite de tamanho em uploads**: áudios limitados a 50 MB, documentos PDF a 30 MB; leitura via streaming com rejeição imediata ao exceder o limite (HTTP 413); helper `_ler_audio()` centraliza a lógica
5. **Sanitização de erros**: todos os `detail=f"Erro: {str(e)}"` substituídos por mensagens genéricas — exceções reais agora vão para o log do servidor, não para o cliente
6. **Validação de input com `Field`**: todos os Pydantic models (`PacienteCreate`, `ProcedimentoCreate`, `PacoteCreate`, `NotaFiscalCreate`, etc.) agora têm `max_length`, `min_length`, `ge/le` e `pattern` para datas
7. **Rate limit no login**: reduzido de `20/minute` para `5/minute` no endpoint `/auth/google-login`
8. **HSTS**: adicionado header `Strict-Transport-Security: max-age=31536000; includeSubDomains` em todas as respostas; também adicionado `Permissions-Policy: camera=(), geolocation=()`
9. **Versões fixas no requirements.txt**: todas as dependências com versão `==` explícita para evitar atualizações automáticas que possam introduzir CVEs

---

## Beta-0.241 — 2026-04-02

### Funcionalidades

1. **Cancelar sessão com cobrança (sessões avulsas)**: Sessões sem pacote ganham botão "Cancelar sessão" no banner de sessão aberta. Abre modal com toggle de cobrança (padrão: ativado), valor pré-preenchido com 50% da sessão avulsa configurada, campo de texto complementar e opção de áudio (transcrição automática). Gera nota automática "Sessão cancelada pelo paciente." + complemento e lançamento financeiro de taxa de cancelamento
2. **Extrato financeiro com PDF e Compartilhar**: Substituído o fluxo de impressão legado (`window.print()` via `#print-view`) por visualização in-app com botões "Baixar PDF" (abre HTML standalone em nova janela com auto-print) e "Compartilhar" (usa `navigator.share` com arquivo HTML ou texto, com fallback para clipboard). Mesma arquitetura do relatório clínico
3. **Badge "Cancelada" no histórico de sessões**: Sessões canceladas exibem badge vermelho distinto de "Encerrada" na lista do paciente, com preview da nota de cancelamento

### Correções

- `renderOpenSessionBanner` agora recebe `temPacote` corretamente em todos os fluxos (incluindo `iniciarNovaSessao`)
- Módulo-level `_temPacoteAtivo` centraliza o estado do pacote para evitar race conditions entre criação de sessão e renderização do banner

---

## Beta-0.240 — 2026-04-02

### Correções — Q.A Gravação de Áudio
8 bugs na lógica de gravação corrigidos:

1. **iOS/Safari — anamnese e conduta quebravam**: fallback era `audio/ogg;codecs=opus`, que Safari não suporta. Corrigido para cadeia `webm;codecs=opus → webm → mp4 → browser default`, mesma usada no recorder principal
2. **Stream vazava se `new MediaRecorder()` falhava**: após `getUserMedia`, se a construção do `MediaRecorder` lançava erro (ex: mimeType inválido), o stream ficava ativo com microfone aberto. Adicionado `stream.getTracks().forEach(t => t.stop())` no catch
3. **Nota extra — chunks inválidos no blob**: `ondataavailable` não filtrava `e.data.size > 0`, incluindo frames vazios. Adicionado o check (padrão dos outros fluxos)
4. **Nota extra — mimeType não especificado**: `new MediaRecorder(stream)` sem mimeType usava default do browser, mas blob era criado como `'audio/webm'` fixo. Corrigido para usar `recorder.mimeType` real
5. **`fecharGravacaoAnamnese/Conduta` — `.stop()` sem try/catch**: podia lançar se recorder estivesse em estado de erro. Envolvido em try/catch
6. **`pendingRetries` — update de elemento removido do DOM**: ao reconectar, retries tentavam atualizar `innerHTML` de modais já fechados. Adicionado `filter(r => document.contains(r.feedbackEl))` antes de processar
7. **Logout sem parar gravação**: `fazerLogout()` redirecionava sem liberar o microfone. Adicionado `resetRecorder()` antes do redirect
8. **Nota extra — sem proteção no `new MediaRecorder()`**: igual ao fix 2, adicionado try/catch com cleanup do stream

---

## Beta-0.239 — 2026-04-02

### Correções — Q.A Conexão de Internet
7 bugs na lógica de detecção de rede corrigidos:

1. **`enviarComRetry` não bloqueia mais em `navigator.onLine`**: a função sempre tenta enviar primeiro; só enfileira retry se o envio realmente falhar com erro de rede. Corrige o caso de usuário com internet sendo tratado como offline (falso negativo do browser ao trocar WiFi→4G)

2. **`apiFetch` distingue erro de rede de outros erros**: erros de `fetch()` (nível de rede) agora carregam flag `_network = true`; erros 4xx/5xx do servidor não são mais mascarados como "sem conexão"

3. **`isNetworkError` usa flag `_network`**: não depende mais de `navigator.onLine`; classifica corretamente erros de rede vs. erros de servidor

4. **`friendlyError` não usa `navigator.onLine` para classificar**: mensagem de "sem conexão" só aparece para erros realmente de rede; errors de servidor mostram mensagem adequada

5. **Debounce de 2s no evento `online`**: ao reconectar (especialmente troca WiFi→4G), aguarda 2 segundos antes de reenviar áudios pendentes — evita retry prematuro com rede instável

6. **`retrySendPendingAudio` não bloqueia em `navigator.onLine`**: deixa o `enviarComRetry` decidir se reenfileira; elimina loop de falsos "ainda sem internet"

7. **Mensagens de aviso ao gravar**: substituído "Sem internet" por "Conexão instável" — mais preciso, pois `navigator.onLine = false` não garante ausência de internet

---

## Beta-0.238 — 2026-04-02

### Melhorias
- **Barra de progresso do pacote**: faixa intermediária alterada de amarelo para azul (`--color-info`)

---

## Beta-0.237 — 2026-04-02

### Melhorias
- **Barra de progresso do pacote — cores dinâmicas**: a barra agora representa sessões **restantes** (depleta à direita) com cor baseada no percentual restante: verde (75–100%), amarelo (20–74%), vermelho (0–19%)

---

## Beta-0.236 — 2026-04-02

### Melhorias
- **Procedimentos detectados — salvo automaticamente**: removido o modal de confirmação. Quando a IA detecta procedimentos na transcrição (ao encerrar sessão ou ao adicionar nota extra), eles são salvos diretamente no sistema. O fisio pode editar ou remover após. Uma notificação flutuante confirma o que foi detectado
- **Procedimentos — exibição corrigida**: campo de descrição com JSON acidental (dados antigos corrompidos) agora é sanitizado na renderização — o nome real é extraído do JSON e exibido corretamente

### Correções
- `_descricaoProc()`: sanitizador que detecta se a descrição contém JSON e extrai os nomes legíveis
- `_showSnack()`: nova função de notificação flutuante temporária (snackbar)

---

## Beta-0.235 — 2026-04-02

### Melhorias
- **Relatório — PDF corrigido**: "Baixar PDF" agora abre janela dedicada com layout standalone; o `window.print()` da página principal escondia o conteúdo por conflito com o CSS de impressão existente
- **Relatório — identidade visual**: novo design tipo documento clínico com cabeçalho escuro (monograma PN + "PhysioNotes"), bloco de paciente destacado em fundo suave, seções com rótulos em small caps, rodapé com branding
- **Relatório — nome do fisioterapeuta**: adicionado ao bloco de identificação do paciente (recuperado do `localStorage.physio_user`)
- **Compartilhar**: tenta compartilhar o arquivo HTML do relatório via `navigator.share({ files })` (funciona no mobile); fallback para compartilhar texto; fallback final para copiar para clipboard
- **Visualização in-app**: redesenhada com card-documento estilizado, consistente com o PDF exportado

---

## Beta-0.234 — 2026-04-02

### Melhorias
- **Prompts de IA reforçados**: todos os agentes agora têm persona de "fisioterapeuta clínico experiente, com domínio completo de anatomia, biomecânica, reabilitação musculoesquelética, neurológica e respiratória, e dos jargões técnicos da fisioterapia brasileira" — eliminados os 4 prompts com persona genérica (`resumir_historico` resumido/completo, `extrair_dados_paciente`, `extrair_dados_pacote`)

---

## Beta-0.233 — 2026-04-02

### Melhorias
- **Relatório Resumido reformulado**: prompt da IA agora instrui máximo 20 linhas com estrutura de snapshot clínico rápido (queixa principal, histórico relevante, técnicas aplicadas, evolução, situação atual) — `max_tokens` reduzido de 2048 para 512
- **Relatório Completo mantido**: prompt formal detalhado, sem alteração
- **Backend**: endpoint `GET /pacientes/{id}/resumo` aceita `?tipo=resumido|completo`; frontend passa `?tipo=resumido` para o relatório resumido

---

## Beta-0.232 — 2026-04-02

### Correções
- **Procedimentos duplicados**: corrigido em dois níveis — (1) backend: `detectar-procedimentos` agora filtra sugestões cujo nome já existe como procedimento salvo na sessão (comparação normalizada); (2) frontend: botão "Salvar selecionados" é desabilitado imediatamente ao clicar, evitando duplo envio

---

## Beta-0.231 — 2026-04-02

### Melhorias
- **Paginação no histórico de sessões**: exibe 10 sessões por página com controles "← Anterior / X de Y / Próximas →"; ao mudar de página faz scroll para o topo; página reseta ao aplicar ou limpar filtro de busca; menos de 10 itens não exibe controles

---

## Beta-0.230 — 2026-04-02

### Correções
- **"+ Nota" no detalhe da sessão**: botão agora aparece dentro da tela de detalhe (cabeçalho da nota clínica), não apenas no card da lista — disponível somente para sessões encerradas do dia atual
- **Detecção de procedimentos na nota extra**: após envio de áudio via "+ Nota", o sistema chama automaticamente `/detectar-procedimentos` e, se identificar procedimentos, abre o modal de revisão antes de fechar
- **Detecção de procedimentos ao encerrar sessão**: ao encerrar qualquer sessão, o sistema agora detecta procedimentos extras e exibe o modal de confirmação se houver sugestões da IA

---

## Beta-0.229 — 2026-04-02

### Funcionalidades
- **Relatório Paciente redesenhado**: removido o relatório CREFITO (não utilizado). O botão "Relatório" no topo do paciente agora gera um relatório clínico focado no paciente, fisio e outros profissionais
  - **Resumido**: identificação (nome, idade, CPF) + síntese clínica gerada por IA com os principais pontos
  - **Completo**: identificação completa (nome, idade, CPF, endereço, observações), anamnese, conduta de tratamento, todas as sessões com notas clínicas e procedimentos (carregados em paralelo), síntese IA ao final
  - Idade calculada automaticamente a partir da data de nascimento
  - Botões **Compartilhar** (`navigator.share` nativo, fallback cópia) e **Baixar PDF** (`window.print()`)
- **Remoção do Relatório CREFITO**: drawer, modal, endpoint `/relatorio/crefito` e CSS associado removidos

### Backend
- Removido: `RelatorioCREFITOBody` e endpoint `POST /relatorio/crefito`

---

## Beta-0.228 — 2026-04-02

### Funcionalidades
- **Editar procedimentos extras**: cada procedimento na evolução diária agora tem botão "Editar" que abre um bottom sheet para alterar descrição e valor — salva via `PUT /procedimentos/{id}`
- **Relatório Paciente (resumido/completo)**: botão "Resumo IA" renomeado para "Relatório" — ao clicar abre modal de escolha entre "Resumido" (síntese IA) e "Completo" (histórico de todas as sessões com notas e status)
- **Compartilhar / Baixar PDF**: relatório do paciente agora exibe barra de ações com botão "Compartilhar" (`navigator.share` nativo, fallback cópia para clipboard) e "Baixar PDF" (`window.print()`)

### Backend
- `PUT /procedimentos/{id}` — atualiza descrição e valor de procedimento extra
- `atualizar_procedimento()` adicionado em `database.py`

---

## Beta-0.227 — 2026-04-02

### Melhorias
- **Verificação de compatibilidade do browser**: ao carregar o app, detecta se `MediaRecorder`, `getUserMedia`, `fetch` e `Promise` estão disponíveis — se algum estiver ausente, exibe tela de bloqueio com instrução para atualizar (Chrome 90+, Safari 14.5+, Firefox 85+); usa feature detection, não user-agent

---

## Beta-0.226 — 2026-04-02

### Segurança
- **CORS restrito**: origens, métodos e headers limitados via variável `ALLOWED_ORIGINS`
- **Autorização em todas as rotas**: `_verificar_dono_sessao` e `_verificar_dono_documento` aplicados em todas as rotas que antes não tinham verificação de propriedade
- **Race condition encerramento**: `encerrar_sessao` usa `WHERE status = 'aberta'` + retorna 409 se já encerrada
- **Rate limiting**: `slowapi` em `/transcrever` (20/min), `/auth/google-login` (20/min) e `/sessoes/{id}/encerrar` (10/min)
- **CSP headers**: `Content-Security-Policy`, `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy` via middleware
- **XSS protection**: função `escHtml()` aplicada em todos os template literals com dados do backend no frontend (nome, observações, transcrições, nota clínica, procedimentos, relatório CREFITO)
- **Delete físico de PDF**: ao excluir documento, o arquivo físico é removido do disco além do soft-delete no banco
- **Índices DB**: 8 índices adicionados para acelerar queries frequentes (sessao, audio_chunk, pacote, paciente, api_uso, documento)
- **Logging estruturado**: `logging` configurado em `main.py`; todos os `except Exception: pass` agora logam via `logger.warning/info`
- **WebAuthn owner_email**: variável `WEBAUTHN_OWNER_EMAIL` associa usuário WebAuthn legado a um email real

### Melhorias
- **N+1 query eliminada**: `GET /pacientes/{id}/sessoes` usa novo `get_sessoes_com_consolidado()` — uma query com LEFT JOIN ao invés de N queries separadas

---

## Beta-0.225 — 2026-04-02

### Melhorias
- **Botão ↻ com animação de spin**: ao clicar, o ícone gira por 1,5s (feedback visual de "tentando") — se ainda offline, após a animação mostra "Ainda sem internet"; se online, dispara o envio

---

## Beta-0.224 — 2026-04-02

### Correções
- **502/503/504 agora disparam retry**: `apiFetch` agora anexa `err.status` ao erro lançado; `isNetworkError` trata 502/503/504 como erros retryáveis (servidor temporariamente indisponível)
- **Banner offline redesenhado**: layout com ícone de wifi cortado, texto descritivo e botão circular com ícone ↻ (sem texto) — sem "Tentar agora" escrito; estado "ainda offline" remove o botão e ajusta a mensagem

---

## Beta-0.223 — 2026-04-02

### Correções
- **Botão "Tentar agora" não funcionava**: `retrySendPendingAudio` não era acessível via `onclick` HTML por estar fora do escopo global — movida para `window.retrySendPendingAudio`
- **Feedback ao clicar offline**: se clicar em "Tentar agora" ainda sem internet, exibe "Ainda sem internet. O áudio está salvo — será enviado automaticamente ao reconectar." em vez de não fazer nada

### Melhorias
- Botão de retry com ícone de refresh

---

## Beta-0.222 — 2026-04-02

### Correções
- **Race condition no retry de áudio**: `enviarComRetry` agora detecta erros de rede pela mensagem (`failed to fetch`, `conexão`, etc.) além de `navigator.onLine` — resolve o caso em que o browser ainda não atualizou `onLine=false` quando o fetch já falhou, mostrando o banner correto em vez do erro genérico

---

## Beta-0.221 — 2026-04-02

### Correções
- **Gravação bloqueada quando offline**: todos os 7 botões de microfone agora exibem aviso leve ("Sem internet — grave normalmente. O áudio será enviado ao reconectar.") e iniciam a gravação normalmente sem depender de rede — a conexão só é necessária no momento de envio

---

## Beta-0.220 — 2026-04-02

### Correções
- **Overlay offline bloqueava gravação**: tela "sem internet" ocupava tela inteira e impedia clicar no microfone — substituída por banner sutil no topo ("Sem internet — você pode continuar gravando. O áudio será enviado quando a conexão voltar.") que não bloqueia a interação

---

## Beta-0.219 — 2026-04-02

### Melhorias
- **Retry de áudio em todos os fluxos de voz**: a proteção contra falta de internet agora cobre todos os 7 fluxos (gravador principal, novo paciente, anamnese, conduta, sessão detalhe, procedimento extra, pacote) — áudio preservado em memória com retry automático ao reconectar
- **Mensagens de erro humanizadas**: `friendlyError` reescrito com linguagem acessível para não-técnicos, cobrindo offline, erros de áudio, sessão expirada, servidor indisponível e duplicidade de cadastro

---

## Beta-0.218 — 2026-04-02

### Melhorias
- **Áudio pendente por falta de internet**: se a conexão cair durante ou antes do envio, o áudio gravado é preservado em memória com aviso "Sem internet — áudio salvo, aguardando reconexão..."; ao reconectar, o envio é retentado automaticamente (ou manualmente via botão "Tentar agora")

---

## Beta-0.217 — 2026-04-02

### Correções
- **Drawer: estrutura nav/footer corrigida** — "Gerenciar usuários" e "Configurações" movidos para o nav (área scrollável); footer fixo contém apenas usuário logado + Sair + versão

---

## Beta-0.216 — 2026-04-02

### Correções
- **Drawer mobile sem scroll**: painel de Configurações expandia além da tela e não era possível rolar — adicionado `overflow-y: auto` + `min-height: 0` + `-webkit-overflow-scrolling: touch` no `.drawer-nav`

---

## Beta-0.215 — 2026-04-02

### Melhorias
- **Sessão avulsa padrão R$ 280**: valor padrão aplicado automaticamente para novos usuários e para quem ainda não configurou — sem precisar preencher manualmente no drawer

---

## Beta-0.214 — 2026-04-02

### Melhorias
- **Botões da seção Conduta**: layout refinado com ícones SVG, `white-space:nowrap`, `padding` uniforme e `flex-wrap` — sem quebra de linha no label "Complementar por voz", ícone de sparkle correto no "Sugestão IA"

---

## Beta-0.213 — 2026-04-02

### Correções
- **Bug crítico: botão "+ Paciente" não abria o modal** — referência a `input-data-atendimento` removido causava TypeError silencioso antes de abrir o modal
- **"Nenhuma anamnese registrada." no card de Conduta** — corrigido para "Nenhuma conduta registrada." usando função `condutaPreviewText` separada

### Funcionalidades
- **Sugestão de Conduta por IA**: botão "Sugestão IA" no card de Conduta lê a anamnese e gera uma proposta de conduta — fisioterapeuta revisa e decide se aceita antes de salvar
- Aviso visual destacado ("⚠️ Sugestão gerada pela IA...") para deixar claro que é uma proposta, não um registro final

---

## Beta-0.212 — 2026-04-02

### Funcionalidades
- **Billing admin**: administrador vê custo de IA individual por fisioterapeuta no mês selecionado — barra de progresso, chamadas e tokens por usuário, total consolidado
- Endpoint `GET /admin/billing?mes=YYYY-MM` restrito ao admin
- Seção "Custo por fisioterapeuta" aparece automaticamente na tela de Billing IA quando logado como admin

---

## Beta-0.211 — 2026-04-02

### Melhorias
- Botão "Nova sessão" renomeado para "+ Evolução Diária"

---

## Beta-0.210 — 2026-04-02

### Correções
- **iOS Safari: modal de pacote não rolava** — overflow do overlay bloqueava scroll quando teclado abria. Corrigido: overlay com `overflow-y: scroll; -webkit-overflow-scrolling: touch`, card interno com `max-height: 92dvh` e `padding-bottom: env(safe-area-inset-bottom)` para home bar do iPhone
- Todos os modais (`.modal-overlay`) receberam as mesmas correções de scroll para iOS
- Modal de pacote virou bottom-sheet (igual ao de paciente) com drag handle visual

---

## Beta-0.20 — 2026-04-02

### Funcionalidades
- **Web Push Notifications completo**: push real que funciona com app fechado no celular
- Subscrição automática do device ao abrir o app (com VAPID)
- **6 notificações agendadas** (APScheduler, fuso America/Sao_Paulo):
  - 20h diário — sessão aberta não encerrada no dia
  - 8h diário — aniversário de paciente hoje 🎂
  - 9h diário — pacote esgotado há 7+ dias sem renovação
  - Segunda 8h — resumo semanal (sessões e pacientes da semana)
  - Segunda 9h — pacientes sem sessão há 30+ dias
- **Pacote quase acabando**: ao encerrar sessão com ≤ 2 sessões restantes, push imediato
- Endpoints: `GET /push/vapid-public-key`, `POST /push/subscribe`, `DELETE /push/unsubscribe`
- Script `generate_vapid_keys.py` para gerar chaves VAPID

### Como ativar no servidor
```
python generate_vapid_keys.py   # gera as chaves
# adicionar ao .env:
VAPID_PUBLIC_KEY=...
VAPID_PRIVATE_KEY=...
VAPID_EMAIL=mailto:seuemail@dominio.com
```

---

## Beta-0.19 — 2026-04-02

### Funcionalidades
- **Notificação de nova versão**: ao detectar atualização do PWA, banner com countdown "atualizando em 5s" + botão "Agora" e notificação nativa do sistema
- **Lembrete diário**: ao abrir o app (a partir das 7h), notificação nativa "Não esqueça de preencher as notas de hoje!" — exibida uma vez por dia por usuário
- **Permissão de notificação**: solicitada automaticamente no primeiro acesso ao app
- Service worker preparado para Web Push (backend) com handler de `push` e `notificationclick`

---

## Beta-0.18 — 2026-04-02

### Correções
- CPF único por fisioterapeuta: não é possível cadastrar o mesmo CPF duas vezes na mesma conta — erro claro "Paciente com este CPF já cadastrado na sua conta."
- Fisios diferentes podem atender o mesmo paciente (mesmo CPF) em contas separadas

---

## Beta-0.17 — 2026-04-02

### Funcionalidades
- **Sessão avulsa**: quando o paciente não tem pacote ativo, o encerramento da sessão registra automaticamente um `procedimento_extra` "Sessão avulsa" no faturamento
- **Configurações no drawer**: campo para definir o valor padrão da sessão avulsa (R$) — salvo por usuário
- Banner de confirmação ao encerrar sessão avulsa: "Sessão avulsa registrada no faturamento — R$ XX,XX"
- Endpoints `GET /configuracoes` e `PUT /configuracoes` para persistir preferências do usuário

---

## Beta-0.16 — 2026-04-02

### Funcionalidades
- **Anamnese desvinculada do cadastro**: criação de paciente captura apenas nome, CPF e endereço — anamnese é registrada separadamente no perfil do paciente
- **Conduta de Tratamento**: nova seção independente no perfil do paciente, com complementação via voz (IA integra com o que já existe) e edição manual
- Endpoint `/pacientes/{id}/complementar-conduta` para integração com IA

### Melhorias
- Modal de novo paciente (voz e manual) sem campos de anamnese — fluxo de cadastro simplificado
- Modal de edição de paciente sem campo de anamnese — foco em dados cadastrais

---

## Beta-0.15 — 2026-04-01

### Correções
- Faturamento (pacotes e procedimentos) agora isolado por usuário
- Notas fiscais agora isoladas por usuário
- Multi-tenancy completo: todos os dados separados por conta

---

## Beta-0.14 — 2026-04-01

### Funcionalidades
- **Multi-tenancy**: cada usuário vê apenas seus próprios pacientes — isolamento total de dados
- **Painel de administração** (`/admin.html`): admin aprova ou revoga acesso de usuários
- **Controle de acesso**: novos usuários ficam pendentes até aprovação do admin; mensagem clara na tela de login
- **Saudação neutra** no login: "Olá, [nome]!" em vez de "Bem-vinda"

### Melhorias
- Login simplificado: apenas Google SSO (biometria removida para evitar acesso não rastreado)
- Token JWT reduzido de 72h para 8h (melhor segurança para dados clínicos)
- Link "Gerenciar usuários" no drawer visível apenas para o admin
- `ADMIN_EMAIL` configurável via `.env`

### Correções
- Envio de áudio retornava 401: chamadas ao `/transcrever` não enviavam o token de autenticação
- Conflito de schema entre tabela `usuario` (WebAuthn) e `usuario_google` (SSO)
- Pacote `requests` adicionado ao `requirements.txt` (necessário para verificação do token Google)

---

## Beta-0.12 — 2026-04-01

### Melhorias
- Agente IA reclassificado como fisioterapeuta clínico experiente em todos os prompts — melhora a interpretação de jargões, abreviações (TENS, FNP, RPG, ADM, EVA) e a qualidade das notas de sessão, anamnese e respostas clínicas

---

## Beta-0.11 — 2026-04-01

### Melhorias
- Labels "Competência" e "Paciente" centralizados e em negrito (Faturamento + Notas Fiscais)
- Versão do app exibida no footer do drawer para controle do testador

---

## Beta-0.10 — 2026-04-01

### Funcionalidades
- CPF (com validação de dígitos verificadores) e endereço obrigatórios no cadastro de paciente
- CPF e endereço extraídos automaticamente por voz; feedback informa o que falta
- Busca de pacientes por nome, CPF, data de nascimento ou endereço (campo livre)
- Anamnese: card recolhido por padrão com preview de 80 chars
- Voz como ação primária na anamnese; edição manual como secundária
- Complemento de anamnese por voz — IA integra nova transcrição ao texto existente em linguagem clínica
- Anamnese renderizada com markdown (títulos, negrito, listas)
- Faturamento agrupado por paciente + mês com checkboxes para emissão de NFS-e individual
- Notas Fiscais: filtro por competência (grade de mês/ano) e por paciente
- Exclusão de paciente exige digitar "EXCLUIR" para confirmar
- Timer de auto-encerramento de sessão: 5 minutos
- Billing mobile: layout responsivo, cards centralizados, badge "Mês atual" corrigido
- Pacotes sem data_pagamento usam data de criação no filtro de competência
