# Changelog — Physio Notes

Todas as mudanças relevantes por versão. Usado como corpo do commit/tag de release.

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
