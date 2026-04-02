# Changelog — Physio Notes

Todas as mudanças relevantes por versão. Usado como corpo do commit/tag de release.

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
