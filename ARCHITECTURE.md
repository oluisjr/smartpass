# SmartPass (CSN) — Documento de Arquitetura (v5)

## Visão geral
O SmartPass é um sistema web (software-only) para **controle de presença em reuniões/eventos** com foco em:
- **Escalabilidade** (200+ convidados sem filas por QR individual)
- **Autonomia do cliente** (portal de gestão extremamente simples)
- **Anti “presença fantasma”** (validação de horário + geolocalização + código rotativo)
- **Auditoria e relatórios** (export Excel/CSV/PDF e métricas)

O sistema é composto por 3 experiências:
1) **Portal (Admin)**: criação/edição de reuniões, upload de convidados, disparo de convites, ajustes de template.
2) **Painel da reunião (Reader)**: acompanhamento ao vivo, exportações e código rotativo.
3) **Tela do convidado (Check-in)**: confirmação de presença com UX minimalista + validações.

---

## Objetivos funcionais
- Criar reuniões com: título, local (mapa), raio, início/fim (UTC), exigir código.
- Importar convidados via Excel com colunas:
  - `nome`, `email`, `empresa` (opcional), `Área`, `valid_from`, `valid_to`
- Enviar convites com link único por convidado (token assinado).
- No check-in, validar:
  1) Token válido
  2) Janela de tempo (valid_from → valid_to)
  3) Geofence (distância ao ponto do mapa <= raio + tolerância)
  4) Código rotativo por reunião (TOTP simplificado, 60s)
  5) Check-in 1x
- Permitir “esqueci um convidado” (criação imediata + link).
- Exportar relatórios em CSV/Excel/PDF e arquivo .ics (calendário).

---

## Stack e justificativa
### Backend
- **FastAPI** (Python): leve, rápido, ótimo para APIs e evoluir para serviços maiores.
- **SQLAlchemy + SQLite**: começa simples e gratuito; evolução natural para PostgreSQL sem reescrever camadas.
- **BackgroundTasks**: envio de e-mail assíncrono (UX não “congela”).

### Frontend
- **HTML + Tailwind CDN + JavaScript**:
  - Zero build, manutenção fácil, rápido de iterar
  - UX/UI “enterprise” com custo baixíssimo
- **Leaflet + OpenStreetMap**:
  - Mapa gratuito e robusto para seleção de coordenadas no portal

---

## Componentes
### 1) Portal (Admin) — `/portal`
Responsável por:
- CRUD de reuniões
- Seleção de ponto no mapa e raio
- Upload Excel (job de import + envio)
- Criação “na hora” de convidado
- Ajuste de template de e-mail por reunião
- Links para painel da reunião, exportações e modo recepção

### 2) Painel (Reader) — `/reader?meeting_id=...`
Responsável por:
- Acompanhamento ao vivo (polling leve)
- Exibição do **código rotativo** + contador
- Export CSV/Excel/PDF e .ics
- Reenvio de convite por convidado (1 clique)
- Métricas de presença

### 3) Modo Recepção — `/reception?meeting_id=...`
Responsável por:
- Exibir as últimas confirmações em tela “limpa”
- Feedback visual + beep (opcional) quando entra alguém
- Código rotativo em destaque

### 4) Check-in (Convidado) — `/checkin?token=...`
Responsável por:
- Mostrar reunião + expiração
- Pedir localização do navegador
- Solicitar o código rotativo
- Confirmar presença e registrar auditoria
- Feedback visual por estado (fora do local/fora do tempo/expirado/ok)

---

## Modelo de dados
### Meeting
- `id` (UUID)
- `title`
- `location_name`
- `lat`, `lng`, `radius_m`
- `starts_at`, `ends_at` (UTC)
- `code_secret` (base32)
- `require_code` (bool)
- `email_subject`, `email_body`

### Invitee
- `id` (UUID)
- `meeting_id` (FK)
- `name`, `email`, `company`, `area`
- `valid_from`, `valid_to` (UTC)
- `checked_in`, `checked_in_at`
- Auditoria: `checkin_device_hash`, `checkin_user_agent`, `last_denied_reason`

### Jobs (envio)
- Job em memória (para demo/MVP): progresso de import/envio.
- Evolução recomendada: persistir em tabela `jobs` para robustez.

---

## Fluxos principais
### A) Criar reunião
1) Admin define título, local (mapa), raio, horários (UTC)
2) Backend salva Meeting (gera `code_secret` automaticamente)

### B) Enviar convites (Excel)
1) Portal envia Excel → backend valida colunas e cria job
2) Backend cria Invitees e dispara e-mails em background
3) Portal acompanha job via polling e mostra progresso

### C) Check-in
1) Convidado abre link (token)
2) Front pede geolocalização e solicita código rotativo
3) Backend valida regras e registra check-in
4) Reader/refresco exibe atualização em tempo real (polling)

---

## Segurança e mitigação de fraudes
- **Token assinado (JWT)** por convidado → evita alteração manual de dados.
- **Janela de tempo** por convidado (valid_from/valid_to).
- **Geofence**: Haversine + tolerância (inclui `accuracy` quando disponível).
- **Código rotativo 60s** por reunião:
  - reduz reutilização de links e compartilhamento
  - validação com tolerância de tempo (-60/0/+60s)
- **Check-in 1x**: impede spam e duplicidade.
- Auditoria básica para investigação (user-agent + hash do device).

Limitações assumidas (web):
- GPS pode ser “spoofado” em dispositivos avançados.
  - Mitigação: exigir código rotativo e janela curta.

---

## APIs principais (resumo)
- Meetings:
  - `POST /api/meetings`
  - `PUT /api/meetings/{id}`
  - `DELETE /api/meetings/{id}`
  - `GET /api/meetings`
  - `GET /api/meetings/{id}/stats`
  - `GET /api/meetings/{id}/code`
  - `GET /api/meetings/{id}/attendance`
- Upload/convites:
  - `POST /api/meetings/{id}/upload`
  - `POST /api/meetings/{id}/invitees` (esquecido na hora)
  - `POST /api/meetings/{id}/invitees/{invitee_id}/resend`
- Check-in:
  - `GET /api/checkin/info?token=...`
  - `POST /api/checkin`
- Export:
  - `GET /api/meetings/{id}/export/csv`
  - `GET /api/meetings/{id}/export/xlsx`
  - `GET /api/meetings/{id}/export/pdf`
  - `GET /api/meetings/{id}/export/ics`

---

## Operação e deploy
- MVP: Uvicorn local / VM / container.
- Produção recomendada:
  - Docker + reverse proxy (Nginx/Caddy)
  - HTTPS obrigatório (geolocalização exige contexto seguro)
  - Postgres como DB
  - Worker de e-mail (Celery/RQ) se volume crescer

---

## Roadmap de evolução (sem reescrever)
1) Persistir Jobs no DB
2) Multi-tenant (clientes/workspaces) + login
3) RBAC (admin/recepção/leitor)
4) Webhooks/integração (Teams, SharePoint, Power Automate)
5) Analytics avançado (picos, tempos médios, dashboards)

---

## Decisões de UX/UI
- “Uma ação por tela”: reduzir erros.
- Feedback imediato (loading/progress/toasts).
- Copy com linguagem humana (sem mensagens técnicas).
- Tema claro/escuro persistente.
- Minimalista com identidade visual “cyberpunk corporativo”.
