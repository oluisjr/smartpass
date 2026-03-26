from fastapi import FastAPI, Body, UploadFile, File, Request, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse, Response
from fastapi.staticfiles import StaticFiles
from io import BytesIO
from datetime import datetime, timezone, date
from types import SimpleNamespace
import os
import time
import jwt
import qrcode

from pathlib import Path
from fastapi import HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse

import pandas as pd

from dotenv import load_dotenv
load_dotenv()
# ===============================
# DB
# ===============================
from models.database import SessionLocal, engine, Base
from sqlalchemy import text
from models import SmartPass, Meeting, Invitee, User, AccessGrant


SMARTPASS_ADMIN_USER = os.getenv("SMARTPASS_ADMIN_USER","").strip()
SMARTPASS_ADMIN_PASSWORD = os.getenv("SMARTPASS_ADMIN_PASSWORD","").strip()
SMARTPASS_STAFF_USER = os.getenv("SMARTPASS_STAFF_USER","").strip()
SMARTPASS_STAFF_PASSWORD = os.getenv("SMARTPASS_STAFF_PASSWORD","").strip()

def _ensure_schema():
    """Migração bem simples (SQLite) para esta demo.
    - Adiciona colunas novas quando não existirem.

    Obs.: isso evita o "quebrou o banco" só porque evoluímos o modelo.
    """
    with engine.begin() as conn:
        cols = conn.execute(text("PRAGMA table_info(invitees)"))
        existing = {row[1] for row in cols.fetchall()}  # row[1] = name
        if "area" not in existing:
            conn.execute(text("ALTER TABLE invitees ADD COLUMN area VARCHAR"))
        if "valid_from" not in existing:
            conn.execute(text("ALTER TABLE invitees ADD COLUMN valid_from DATETIME"))
        if "valid_to" not in existing:
            conn.execute(text("ALTER TABLE invitees ADD COLUMN valid_to DATETIME"))
        if "checkin_device_hash" not in existing:
            conn.execute(text("ALTER TABLE invitees ADD COLUMN checkin_device_hash VARCHAR"))
        if "checkin_user_agent" not in existing:
            conn.execute(text("ALTER TABLE invitees ADD COLUMN checkin_user_agent TEXT"))
        if "last_denied_reason" not in existing:
            conn.execute(text("ALTER TABLE invitees ADD COLUMN last_denied_reason VARCHAR"))

        # meetings upgrades
        cols_m = conn.execute(text("PRAGMA table_info(meetings)"))
        existing_m = {row[1] for row in cols_m.fetchall()}
        if "code_secret" not in existing_m:
            conn.execute(text("ALTER TABLE meetings ADD COLUMN code_secret VARCHAR"))
        if "require_code" not in existing_m:
            conn.execute(text("ALTER TABLE meetings ADD COLUMN require_code BOOLEAN"))
        if "email_subject" not in existing_m:
            conn.execute(text("ALTER TABLE meetings ADD COLUMN email_subject VARCHAR"))
        if "email_body" not in existing_m:
            conn.execute(text("ALTER TABLE meetings ADD COLUMN email_body TEXT"))


# ===============================
# SMARTPASS SERVICE
# ===============================
from services.smartpass_service import (
    generate_smartpass_token,
    SMARTPASS_SECRET,
    SMARTPASS_ISSUER,
    SMARTPASS_AUDIENCE,
)

# ===============================
# CHECK-IN SERVICE
# ===============================
from services.checkin_service import check_in

# ===============================
# SAMSUNG WALLET
# ===============================
from wallet.samsung_wallet import generate_samsung_cdata

# ===============================
# GOOGLE WALLET
# ===============================
from wallet.google_wallet import generate_google_wallet_link
from wallet.models import WalletPassData

# ===============================
# SMTP (Email)
# ===============================
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


ALGORITHM = "HS256"
QR_FOLDER = "qrcodes"
os.makedirs(QR_FOLDER, exist_ok=True)

app = FastAPI(title="SmartPass API")

Base.metadata.create_all(bind=engine)
_ensure_schema()

# ===============================
# STATIC + PAGES + HEALTH
# ===============================
ROOT_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "static"

if not STATIC_DIR.exists():
    # você prefere falhar cedo e alto do que ficar caçando 404 fantasma
    raise RuntimeError(f"Pasta static não encontrada em: {STATIC_DIR}")

# serve /static/*
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _read_static_html(filename: str) -> str:
    p = STATIC_DIR / filename
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Arquivo não encontrado: static/{filename}")
    return p.read_text(encoding="utf-8")


@app.get("/", response_class=RedirectResponse)
def root():
    # você pode trocar para /login se preferir
    return RedirectResponse(url="/portal", status_code=302)

@app.get("/login", response_class=HTMLResponse)
def login_page():
    # garanta que seu HTML esteja em static/login.html
    return _read_static_html("login.html")


@app.get("/portal", response_class=HTMLResponse)
def portal_page():
    return _read_static_html("portal.html")


@app.get("/checkin", response_class=HTMLResponse)
def checkin_page():
    return _read_static_html("checkin.html")


@app.get("/reception", response_class=HTMLResponse)
def reception_page():
    return _read_static_html("reception.html")


@app.get("/reader", response_class=HTMLResponse)
def reader_page():
    return _read_static_html("reader.html")

# ===============================
# HELPERS
# ===============================
def as_utc_aware(dt: datetime | None) -> datetime | None:
    """Normaliza datetime para UTC-aware."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_dt_utc(value) -> datetime | None:
    """
    Aceita datetime/string/vazio e devolve datetime UTC-aware ou None.
    """
    if value is None:
        return None
    try:
        if isinstance(value, float) and pd.isna(value):
            return None
    except Exception:
        pass

    if isinstance(value, datetime):
        dt = value
    else:
        dt = pd.to_datetime(value, errors="coerce")
        if pd.isna(dt):
            return None
        dt = dt.to_pydatetime()

    # transforma em UTC-aware
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def send_email(to_email: str, subject: str, html: str):
    """
    Envio SMTP simples.
    Configure por env vars:
      SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM (opcional)
    """
    smtp_host = os.getenv("SMTP_HOST", "")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    smtp_from = os.getenv("SMTP_FROM", smtp_user)

    if not (smtp_host and smtp_user and smtp_pass and smtp_from):
        raise RuntimeError("SMTP não configurado (SMTP_HOST/SMTP_USER/SMTP_PASS/SMTP_FROM).")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.sendmail(smtp_from, [to_email], msg.as_string())

def _fmt_utc(dt: datetime | None) -> str:
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")

def render_invite_email(
    meeting_name: str,
    invitee_name: str,
    area: str,
    checkin_link: str,
    valid_from: datetime | None,
    valid_to: datetime | None,
    require_code: bool = True,
) -> tuple[str, str]:
    """
    Retorna (assunto, html) do e-mail.
    """
    subject = f"Convite – {meeting_name}"
    vf = _fmt_utc(valid_from)
    vt = _fmt_utc(valid_to)

    code_txt = ""
    if require_code:
        code_txt = """
        <li><b>Código de confirmação:</b> no dia, peça ao organizador o código de 6 dígitos (ele muda a cada 60s).</li>
        """

    html = f"""
    <div style="font-family: Arial, sans-serif; line-height: 1.4; color: #111;">
      <h2 style="margin:0 0 8px 0;">{meeting_name}</h2>
      <p style="margin:0 0 12px 0;">Olá <b>{invitee_name}</b>,</p>

      <p style="margin:0 0 12px 0;">
        Você está convidado(a). Área: <b>{area or "—"}</b>.
      </p>

      <div style="padding:12px; border:1px solid #ddd; border-radius:10px; background:#fafafa; margin: 12px 0;">
        <p style="margin:0 0 10px 0;"><b>Como confirmar presença (no local e horário do evento):</b></p>
        <ol style="margin:0; padding-left:18px;">
          <li>Abra o link abaixo no dia do evento.</li>
          <li>Permita o acesso à localização.</li>
          {code_txt}
          <li>Clique em <b>Marcar presença</b>.</li>
        </ol>
        <p style="margin:10px 0 0 0; font-size: 13px; color:#333;">
          Janela de validade: <b>{vf}</b> até <b>{vt}</b>.
        </p>
      </div>

      <p style="margin:0 0 12px 0;">
        <a href="{checkin_link}" style="display:inline-block; padding:10px 14px; background:#0b5fff; color:#fff; text-decoration:none; border-radius:10px;">
          Abrir link de check-in
        </a>
      </p>

      <p style="margin:0; font-size:12px; color:#666;">
        Se você não conseguir abrir o link, copie e cole no navegador:<br/>
        <span style="word-break:break-all;">{checkin_link}</span>
      </p>
    </div>
    """
    return subject, html

def _require_admin(request: Request):
    user = getattr(request.state, "user", None)

    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")

    role = (user.get("role") or "").lower()
    email = user.get("email") or user.get("sub")

    if role != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito ao administrador")

    # objeto simples para uso nas rotas
    return SimpleNamespace(email=email)

# ========================
# CHECKIN LINK BUILDER
# ========================
def build_checkin_link(request, token: str) -> str:
    """
    Gera um link absoluto para a página de check-in.
    """
    base = str(request.base_url).rstrip("/")
    return f"{base}/checkin?token={token}"

# ===============================
# MEETINGS / INVITES (NEW)
# ===============================
INVITE_SECRET = os.getenv("INVITE_SECRET", SMARTPASS_SECRET)
INVITE_ISSUER = os.getenv("INVITE_ISSUER", "smartpass-portal")
INVITE_AUDIENCE = os.getenv("INVITE_AUDIENCE", "smartpass-checkin")

def utcnow():
    return datetime.now(timezone.utc)

def haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Distância em metros entre dois pontos (WGS84) usando Haversine."""
    import math
    R = 6371000.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi/2)**2 + math.cos(phi1)*math.cos(phi2)*math.sin(dl/2)**2
    c = 2*math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R*c

def issue_invite_token(invitee_id: str, meeting_id: str, valid_from: datetime, valid_to: datetime) -> str:
    """Emite JWT para um convidado.

    Regras:
    - valid_from = quando o botão começa a funcionar
    - valid_to = expiração do convite
    """
    if valid_from is None or valid_to is None:
        raise ValueError("valid_from/valid_to obrigatórios")

    nbf = int(as_utc_aware(valid_from).timestamp())
    exp = int(as_utc_aware(valid_to).timestamp())
    payload = {
        "sub": "invitee",
        "invitee_id": invitee_id,
        "meeting_id": meeting_id,
        "iat": int(utcnow().timestamp()),
        "nbf": nbf,
        "exp": exp,
        "iss": INVITE_ISSUER,
        "aud": INVITE_AUDIENCE,
    }
    return jwt.encode(payload, INVITE_SECRET, algorithm=ALGORITHM)


def _normalize_b32(secret_b32: str) -> bytes:
    # base32 precisa de padding; removemos '=' no storage
    import base64
    s = (secret_b32 or "").strip().upper()
    pad = "=" * ((8 - (len(s) % 8)) % 8)
    return base64.b32decode(s + pad)


def meeting_code(secret_b32: str, *, step_seconds: int = 60, t: float | None = None) -> tuple[str, int]:
    """Gera um código rotativo de 6 dígitos (HOTP/TOTP simplificado).

    Retorna (codigo, segundos_restantes).
    """
    import hmac, hashlib, struct
    now = time.time() if t is None else float(t)
    counter = int(now // step_seconds)
    key = _normalize_b32(secret_b32)
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack(">I", digest[offset:offset+4])[0] & 0x7fffffff
    code = str(code_int % 1_000_000).zfill(6)
    remaining = step_seconds - (int(now) % step_seconds)
    return code, remaining


def is_valid_meeting_code(secret_b32: str, provided: str, *, step_seconds: int = 60) -> bool:
    p = str(provided or "").strip()
    if not p or len(p) != 6 or not p.isdigit():
        return False
    now = time.time()
    # Aceita janela anterior/atual/próxima para tolerância de relógio do celular
    for delta in (-step_seconds, 0, step_seconds):
        code, _ = meeting_code(secret_b32, step_seconds=step_seconds, t=now + delta)
        if p == code:
            return True
    return False

# ===============================
# HEALTH
# ===============================
@app.get("/health")
def health():
    return {"ok": True, "status": "SmartPass API rodando"}

@app.get("/api/health")
def api_health():
    # alias para o front (portal) que chama /api/health
    return {"ok": True, "status": "SmartPass API rodando"}

# ===============================
# ISSUE SMARTPASS (OFICIAL)
# - Gera JWT
# - Grava SmartPass no DB (id + datas)
# ===============================
@app.post("/issue")
def issue_smartpass(data: dict = Body(...)):
    try:
        for field in ("name", "company", "event"):
            if not data.get(field):
                return JSONResponse(status_code=400, content={"error": f"Campo obrigatório ausente: {field}"})

        token = generate_smartpass_token(
            name=data["name"],
            company=data["company"],
            event=data["event"],
            pass_type=data.get("type", "visitor"),
        )

        payload = jwt.decode(
            token,
            SMARTPASS_SECRET,
            algorithms=[ALGORITHM],
            audience=SMARTPASS_AUDIENCE,
            issuer=SMARTPASS_ISSUER,
        )

        smartpass_id = payload["id"]

        # Datas UTC-aware
        vf = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
        vt = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)

        db = SessionLocal()
        try:
            sp = db.query(SmartPass).filter(SmartPass.id == smartpass_id).first()
            if not sp:
                sp = SmartPass(id=smartpass_id)
                db.add(sp)

            sp.name = payload.get("name", data["name"])
            sp.company = payload.get("company", data["company"])
            sp.event = payload.get("event", data["event"])
            sp.pass_type = payload.get("type", data.get("type", "visitor"))

            # Se vierem datas custom do client, usa; senão usa iat/exp do token
            sp.valid_from = as_utc_aware(parse_dt_utc(data.get("valid_from")) or vf)
            sp.valid_to = as_utc_aware(parse_dt_utc(data.get("valid_to")) or vt)

            # email opcional
            if data.get("email"):
                sp.email = str(data["email"]).strip()

            sp.checked_in = False
            sp.checked_in_at = None

            db.commit()
        finally:
            db.close()

        return {
            "valid": True,
            "smartpass_id": smartpass_id,
            "expires_at": payload["exp"],
            "token": token,
            "qrcode_url": f"/qrcode/{smartpass_id}",
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ===============================
# SERVE QR CODE
# - QR carrega um JWT "curto" com id
# ===============================
@app.get("/qrcode/{smartpass_id}")
def get_qrcode(smartpass_id: str):
    token = jwt.encode(
        {
            "id": smartpass_id,
            "iss": SMARTPASS_ISSUER,
            "aud": SMARTPASS_AUDIENCE,
            "iat": int(time.time()),
        },
        SMARTPASS_SECRET,
        algorithm=ALGORITHM,
    )

    img = qrcode.make(token)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return StreamingResponse(buf, media_type="image/png")


# ===============================
# TOKEN INFO (USADO PELO HTML)
# ===============================
@app.get("/token-info")
def token_info(token: str):
    try:
        payload = jwt.decode(
            token,
            SMARTPASS_SECRET,
            algorithms=[ALGORITHM],
            audience=SMARTPASS_AUDIENCE,
            issuer=SMARTPASS_ISSUER,
        )
        return {"valid": True, "data": payload}

    except jwt.ExpiredSignatureError:
        return {"valid": False, "reason": "Token expirado"}
    except jwt.InvalidAudienceError:
        return {"valid": False, "reason": "Audience inválida"}
    except jwt.InvalidIssuerError:
        return {"valid": False, "reason": "Issuer inválido"}
    except jwt.InvalidTokenError:
        return {"valid": False, "reason": "Token inválido"}


# ===============================
# SAMSUNG WALLET ENDPOINT
# ===============================
@app.post("/wallet/samsung")
def create_samsung_wallet(data: dict = Body(...)):
    try:
        for field in ("id", "name", "company", "event"):
            if field not in data:
                raise ValueError(f"Campo obrigatório ausente: {field}")

        if "type" not in data:
            data["type"] = "Visitante"

        cdata_token = generate_samsung_cdata(data)
        return {"cdata": cdata_token}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ===============================
# GOOGLE WALLET ENDPOINT
# ===============================
@app.post("/wallet/google")
def wallet_google(payload: dict = Body(...)):
    try:
        required = ("id", "name", "company", "event")
        missing = [k for k in required if not payload.get(k)]
        if missing:
            return JSONResponse(
                status_code=400,
                content={"error": f"Campos obrigatórios ausentes: {', '.join(missing)}"}
            )

        smartpass_id = payload["id"]

        data = WalletPassData(
            id=smartpass_id,
            smartpass_id=smartpass_id,
            name=payload["name"],
            company=payload["company"],
            event=payload["event"],
            qr_token=smartpass_id  # compatibilidade
        )

        save_url = generate_google_wallet_link(data)
        return {"saveUrl": save_url}

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ===============================
# VALIDATE ENDPOINT
# ===============================
@app.post("/validate")
def validate_qr(payload: dict = Body(...)):
    """
    Recebe {"token": "<string lida do QR>"}.
    - Se for JWT (tem 2 pontos), decodifica e extrai o smartpass_id.
    - Se não, assume que já é o smartpass_id.
    Consulta DB, valida janelas, faz check-in e retorna dados ao reader.
    """
    try:
        raw = (payload.get("token") or "").strip()
        if not raw:
            return JSONResponse(status_code=400, content={"valid": False, "reason": "Token ausente"})

        smartpass_id = raw

        # Detecta JWT (a.b.c)
        if raw.count(".") == 2:
            try:
                decoded = jwt.decode(
                    raw,
                    SMARTPASS_SECRET,
                    algorithms=[ALGORITHM],
                    audience=SMARTPASS_AUDIENCE,
                    issuer=SMARTPASS_ISSUER,
                )
            except jwt.ExpiredSignatureError:
                return JSONResponse(status_code=400, content={"valid": False, "reason": "Token JWT expirado"})
            except jwt.InvalidTokenError:
                return JSONResponse(status_code=400, content={"valid": False, "reason": "Token JWT inválido"})

            smartpass_id = decoded.get("id")
            if not smartpass_id:
                return JSONResponse(status_code=400, content={"valid": False, "reason": "JWT sem id"})

        db = SessionLocal()
        try:
            sp = db.query(SmartPass).filter(SmartPass.id == smartpass_id).first()
            if not sp:
                return JSONResponse(status_code=404, content={"valid": False, "reason": "SmartPass não encontrado"})

            now = datetime.now(timezone.utc)

            vf = as_utc_aware(getattr(sp, "valid_from", None))
            vt = as_utc_aware(getattr(sp, "valid_to", None))

            if vf and now < vf:
                return JSONResponse(status_code=400, content={"valid": False, "reason": "Ainda não válido"})
            if vt and now > vt:
                return JSONResponse(status_code=400, content={"valid": False, "reason": "Expirado"})

            # Check-in (evita duplicado) - sua função já está funcionando
            result = check_in(db, smartpass_id)
            if not result.get("valid", False):
                return JSONResponse(status_code=400, content=result)

            return {
                "valid": True,
                "smartpass_id": sp.id,
                "name": sp.name,
                "company": sp.company,
                "event": sp.event,
                "checked_in_at": result.get("checked_in_at"),
            }
        finally:
            db.close()

    except Exception as e:
        return JSONResponse(status_code=500, content={"valid": False, "reason": str(e)})

SESSION_COOKIE = os.getenv("SESSION_COOKIE", "sp_session")

def _clear_session_cookie(resp: Response):
    resp.delete_cookie(key=SESSION_COOKIE, path="/")


def _create_session_jwt(email: str, role: str) -> str:
    # JWT simples (já que você já usa jwt no projeto)
    secret = os.getenv("APP_SESSION_SECRET", SMARTPASS_SECRET)
    now = int(time.time())
    payload = {
        "sub": email,
        "role": role,          # "admin" | "staff"
        "iat": now,
        "exp": now + 60 * 60 * 8,  # 8h
        "v": 1
    }
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def _set_session_cookie(resp: Response, token: str):
    secure = os.getenv("COOKIE_SECURE", "0") == "1"
    resp.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
        max_age=60 * 60 * 8
    )


@app.post("/api/auth/login")
def api_login(data: dict = Body(...), request: Request = None):
    """
    Login local (env). Emite sessão via cookie JWT (SESSION_COOKIE).
    Suporta payloads {username,password} ou {email,password}.

    Regras:
    - Se bater com ADMIN do .env -> role admin (is_admin=True, is_staff=True)
    - Se bater com STAFF do .env -> role staff (is_staff=True)
    - Depois aplica AccessGrant (se existir e ativo) para promover/demover staff/admin conforme tabela
      (exceto: admin do .env continua admin sempre)
    """

    # 1) Normaliza credenciais vindas do front
    username_in = ((data.get("username") or data.get("email") or "")).strip().lower()
    password_in = (data.get("password") or "").strip()

    if not username_in or not password_in:
        return JSONResponse(status_code=400, content={"error": "Informe email e senha"})

    # 2) Carrega credenciais do env
    admin_user = (SMARTPASS_ADMIN_USER or "").strip().lower()
    admin_pass = (SMARTPASS_ADMIN_PASSWORD or "").strip()

    staff_user = (SMARTPASS_STAFF_USER or "").strip().lower()
    staff_pass = (SMARTPASS_STAFF_PASSWORD or "").strip()

    # 3) Valida contra env (local)
    ok_admin = (admin_user and username_in == admin_user and password_in == admin_pass)
    ok_staff = (staff_user and username_in == staff_user and password_in == staff_pass)

    if not (ok_admin or ok_staff):
        return JSONResponse(status_code=401, content={"error": "Usuário ou senha inválidos"})

    db = SessionLocal()
    try:
        # 4) Garante usuário no DB
        u = db.query(User).filter(User.email == username_in).first()
        if not u:
            u = User(
                email=username_in,
                display_name=None,
                provider="local",
                is_active=True,
                is_staff=False,
                is_admin=False,
            )
            db.add(u)
            db.commit()
            db.refresh(u)
        else:
            # mantém provider como "local" (sem mexer em display_name)
            if (u.provider or "") != "local":
                u.provider = "local"
                db.commit()
                db.refresh(u)

        # 5) Bootstrap mínimo conforme login local
        #    (admin do env SEMPRE admin; staff do env SEMPRE staff)
        if ok_admin:
            u.is_admin = True
            u.is_staff = True
        elif ok_staff:
            u.is_staff = True
            # não força is_admin aqui
            if u.is_admin:
                u.is_admin = False

        db.commit()
        db.refresh(u)

        # 6) Aplica AccessGrant (se existir e ativo)
        #    Observação: admin do env não perde admin, mesmo se grant tentar rebaixar.
        g = (
            db.query(AccessGrant)
            .filter(
                AccessGrant.email == username_in,
                AccessGrant.is_active == True,
            )
            .first()
        )
        if g:
            if not ok_admin:
                # para não-admin do env, grant manda
                u.is_staff = bool(g.grant_staff)
                u.is_admin = bool(g.grant_admin)
            else:
                # admin do env mantém admin; pode manter staff também
                u.is_staff = True
                u.is_admin = True

            db.commit()
            db.refresh(u)

        # 7) Resposta + cookie de sessão
        role = "admin" if u.is_admin else ("staff" if u.is_staff else "user")
        resp = JSONResponse(
            {
                "ok": True,
                "role": role,
                "email": u.email,
                "is_admin": bool(u.is_admin),
                "is_staff": bool(u.is_staff),
            }
        )
        token = _create_session_jwt(u.email, role)
        _set_session_cookie(resp, token)  # seu helper existente
        return resp

    finally:
        db.close()


@app.post("/api/auth/logout")
def api_auth_logout():
    resp = JSONResponse({"ok": True})
    _clear_session_cookie(resp)
    return resp

# ===============================
# ROTAS PÚBLICAS (não exigem sessão)
# ===============================
PUBLIC_PREFIXES = (
    "/static/",
    "/login",
    "/health",
    "/api/health",
    "/api/auth/login",
    "/api/auth/logout",
    "/auth/azure/login",
    "/auth/azure/callback",
    "/checkin",         # se o checkin for público por design
    "/reception",     # info do convite (token)
)

def _is_public_path(path: str) -> bool:
    if path == "/":
        return True
    return any(path.startswith(p) for p in PUBLIC_PREFIXES)

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    path = request.url.path

    if _is_public_path(path):
        return await call_next(request)

    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        # Se o browser está pedindo uma página (HTML), redireciona.
        accept = (request.headers.get("accept") or "").lower()
        if "text/html" in accept:
            return RedirectResponse(url="/login", status_code=302)
        return JSONResponse(status_code=401, content={"error": "Não autenticado"})

    try:
        payload = jwt.decode(
            token,
            os.getenv("APP_SESSION_SECRET", SMARTPASS_SECRET),
            algorithms=[ALGORITHM],
        )
        request.state.user = payload
    except Exception:
        accept = (request.headers.get("accept") or "").lower()
        if "text/html" in accept:
            return RedirectResponse(url="/login", status_code=302)
        return JSONResponse(status_code=401, content={"error": "Sessão inválida"})

    return await call_next(request)

# ===============================
# EXPORT DO DIA (para o botão do reader)
# ===============================
@app.get("/export/today.xlsx")
def export_today():
    """
    Exporta as leituras do dia (UTC) até o momento.
    """
    today = date.today()
    start = datetime(today.year, today.month, today.day, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=timezone.utc)

    db = SessionLocal()
    try:
        rows = db.query(SmartPass).filter(
            SmartPass.checked_in == True,
            SmartPass.checked_in_at != None,
            SmartPass.checked_in_at >= start,
            SmartPass.checked_in_at <= end,
        ).all()

        data = []
        for sp in rows:
            data.append({
                "checked_in_at": sp.checked_in_at.isoformat() if sp.checked_in_at else None,
                "smartpass_id": sp.id,
                "name": sp.name,
                "email": getattr(sp, "email", None),
                "company": sp.company,
                "event": sp.event,
                "type": getattr(sp, "pass_type", None),
            })

        out = BytesIO()
        pd.DataFrame(data).to_excel(out, index=False)
        out.seek(0)

        return StreamingResponse(
            out,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="leituras_hoje.xlsx"'}
        )
    finally:
        db.close()

def _ensure_user(db, email: str, provider: str, display_name: str | None = None) -> User:
    email = (email or "").strip().lower()
    u = db.query(User).filter(User.email == email).first()
    if not u:
        u = User(email=email, provider=provider, display_name=display_name, is_active=True)
        db.add(u)
        db.commit()
        db.refresh(u)
    else:
        # mantém atualizado sem mexer em permissões aqui
        changed = False
        if display_name and (u.display_name or "") != display_name:
            u.display_name = display_name
            changed = True
        if provider and (u.provider or "") != provider:
            u.provider = provider
            changed = True
        if changed:
            db.commit()
            db.refresh(u)
    return u


def _apply_access_grant(db, u: User):
    g = (
        db.query(AccessGrant)
        .filter(AccessGrant.email == u.email, AccessGrant.is_active == True)
        .first()
    )
    if not g:
        return

    u.is_staff = bool(g.grant_staff)
    u.is_admin = bool(g.grant_admin)


# ===============================
# ADMIN UPLOAD (Excel -> Issue + Email)
# ===============================
@app.post("/admin/upload")
async def admin_upload(request: Request, file: UploadFile = File(...)):
    """
    Recebe Excel com colunas:
      nome, email, empresa, evento, tipo, valid_from, valid_to

    Para cada linha:
      - gera token oficial
      - grava no DB (SmartPass)
      - envia e-mail com link do card web
    """
    try:
        content = await file.read()
        df = pd.read_excel(BytesIO(content))

        # normaliza colunas
        cols = {str(c).strip().lower(): c for c in df.columns}
        required = ["nome", "email", "empresa", "evento"]
        missing = [c for c in required if c not in cols]
        if missing:
            return JSONResponse(status_code=400, content={"error": f"Colunas obrigatórias ausentes: {', '.join(missing)}"})

        base_url = str(request.base_url).rstrip("/")  # http://127.0.0.1:8000

        created = 0
        emailed = 0
        failed = []

        db = SessionLocal()
        try:
            for idx, row in df.iterrows():
                try:
                    nome = str(row[cols["nome"]]).strip()
                    email = str(row[cols["email"]]).strip()
                    empresa = str(row[cols["empresa"]]).strip()
                    evento = str(row[cols["evento"]]).strip()

                    if not (nome and email and empresa and evento):
                        raise ValueError("Campos vazios (nome/email/empresa/evento)")

                    tipo = "visitor"
                    if "tipo" in cols and row[cols["tipo"]] is not None and not pd.isna(row[cols["tipo"]]):
                        tipo = str(row[cols["tipo"]]).strip() or "visitor"

                    vf = parse_dt_utc(row[cols["valid_from"]]) if "valid_from" in cols else None
                    vt = parse_dt_utc(row[cols["valid_to"]]) if "valid_to" in cols else None

                    # 1) gera token
                    token = generate_smartpass_token(
                        name=nome,
                        company=empresa,
                        event=evento,
                        pass_type=tipo,
                    )

                    # 2) decodifica para pegar id/iat/exp e salvar
                    payload = jwt.decode(
                        token,
                        SMARTPASS_SECRET,
                        algorithms=[ALGORITHM],
                        audience=SMARTPASS_AUDIENCE,
                        issuer=SMARTPASS_ISSUER,
                    )

                    smartpass_id = payload["id"]

                    # fallback datas
                    if vf is None:
                        vf = datetime.fromtimestamp(payload["iat"], tz=timezone.utc)
                    if vt is None:
                        vt = datetime.fromtimestamp(payload["exp"], tz=timezone.utc)

                    sp = db.query(SmartPass).filter(SmartPass.id == smartpass_id).first()
                    if not sp:
                        sp = SmartPass(id=smartpass_id)
                        db.add(sp)

                    sp.name = nome
                    sp.email = email
                    sp.company = empresa
                    sp.event = evento
                    sp.pass_type = tipo
                    sp.valid_from = as_utc_aware(vf)
                    sp.valid_to = as_utc_aware(vt)
                    sp.checked_in = False
                    sp.checked_in_at = None

                    db.commit()
                    created += 1

                    link = f"{base_url}/static/smartpass.html?token={token}"

                    # 3) envia e-mail
                    send_email(
                        to_email=email,
                        subject="Seu SmartPass (CSN)",
                        html=f"""
                          <p>Olá, <b>{nome}</b>!</p>
                          <p>Seu SmartPass está pronto:</p>
                          <p><a href="{link}">Abrir SmartPass</a></p>
                          <p>Você pode adicionar ao Google Wallet ou Samsung Wallet pelo próprio card.</p>
                        """
                    )
                    time.sleep(0.3)  # evita sobrecarga SMTP
                    emailed += 1

                except Exception as e:
                    failed.append({"row": int(idx) + 2, "error": str(e)})  # +2: header + index
                    # continua o lote

        finally:
            db.close()

        return {
            "ok": True,
            "created": created,
            "emailed": emailed,
            "failed": failed,
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# ===============================
# STATIC FILES
# ===============================
app.mount("/static", StaticFiles(directory="static"), name="static")

# ===============================
# PORTAL PAGES (STATIC HTML)
# ===============================
from fastapi.responses import HTMLResponse

# ===============================
# EXPORTS (PDF / ICS) + RESEND
# ===============================
try:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas as pdf_canvas
except Exception:
    A4 = None
    pdf_canvas = None


def _dt_ics(dt: datetime) -> str:
    # UTC in basic format: YYYYMMDDTHHMMSSZ
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y%m%dT%H%M%SZ")

@app.get("/api/auth/me")
def api_auth_me(request: Request):
    """
    Retorna o usuário logado.
    IMPORTANTE: o portal usa is_admin/is_staff para liberar telas e label.
    """
    user = getattr(request.state, "user", None)
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")

    role = (user.get("role") or "").strip().lower()
    email = (user.get("sub") or user.get("email") or "").strip()

    return {
        "ok": True,
        "email": email,
        "role": role,
        "is_admin": role == "admin",
        "is_staff": role in ("admin", "staff"),
    }

@app.get("/api/meetings/{meeting_id}/export/ics")
def export_ics(meeting_id: str):
    """Baixa um arquivo .ics (Adicionar ao calendário)."""
    db = SessionLocal()
    try:
        m = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not m:
            return JSONResponse(status_code=404, content={"error": "Reunião não encontrada."})

        uid = f"{m.id}@smartpass"
        dtstamp = _dt_ics(datetime.now(timezone.utc))
        ics = "\r\n".join([
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//CSN//SmartPass//PT-BR",
            "CALSCALE:GREGORIAN",
            "METHOD:PUBLISH",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"DTSTAMP:{dtstamp}",
            f"DTSTART:{_dt_ics(m.starts_at)}",
            f"DTEND:{_dt_ics(m.ends_at)}",
            f"SUMMARY:{m.title}",
            f"LOCATION:{m.location_name or 'Local do evento'}",
            "END:VEVENT",
            "END:VCALENDAR",
            "",
        ])
        return Response(content=ics, media_type="text/calendar", headers={
            "Content-Disposition": f'attachment; filename="reuniao_{m.id}.ics"'
        })
    finally:
        db.close()


@app.get("/api/meetings/{meeting_id}/export/pdf")
def export_pdf(meeting_id: str):
    """Relatório PDF (executivo) de presença — estilo 'enterprise'.

    Mantém tudo simples (reportlab puro), mas com:
    - Header/brand
    - KPIs (presentes, pendentes, taxa)
    - Mini gráfico de barras
    - Tabela com linhas alternadas
    """
    if pdf_canvas is None:
        return JSONResponse(status_code=500, content={"error": "reportlab não disponível."})

    from reportlab.lib import colors
    from reportlab.lib.units import mm as _mm

    db = SessionLocal()
    try:
        m = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not m:
            return JSONResponse(status_code=404, content={"error": "Reunião não encontrada."})

        invitees = db.query(Invitee).filter(Invitee.meeting_id == meeting_id).order_by(Invitee.name.asc()).all()
        total = len(invitees)
        checked = sum(1 for i in invitees if i.checked_in)
        denied = sum(1 for i in invitees if (not i.checked_in) and (i.last_denied_reason is not None))
        pending = max(total - checked, 0)
        pct = (checked / total * 100.0) if total else 0.0

        buff = BytesIO()
        c = pdf_canvas.Canvas(buff, pagesize=A4)
        w, h = A4

        def draw_header(page_title: str):
            c.setFillColorRGB(0.05, 0.08, 0.12)
            c.rect(0, h-32*_mm, w, 32*_mm, stroke=0, fill=1)
            c.setFillColor(colors.white)
            c.setFont('Helvetica-Bold', 14)
            c.drawString(18*_mm, h-18*_mm, 'CSN • SmartPass')
            c.setFont('Helvetica', 9)
            c.setFillColor(colors.Color(0.75,0.85,0.95))
            c.drawString(18*_mm, h-25*_mm, page_title)
            c.setFillColor(colors.Color(0.65,0.70,0.78))
            c.setFont('Helvetica', 8)
            c.drawRightString(w-18*_mm, h-18*_mm, datetime.now(timezone.utc).strftime('%d/%m/%Y %H:%M UTC'))

        def kpi_box(x, y, title, value, sub=''):
            c.setFillColor(colors.Color(0.08,0.11,0.16))
            c.roundRect(x, y, 56*_mm, 22*_mm, 4*_mm, stroke=0, fill=1)
            c.setFillColor(colors.Color(0.75,0.85,0.95))
            c.setFont('Helvetica', 8)
            c.drawString(x+6*_mm, y+15*_mm, title)
            c.setFillColor(colors.white)
            c.setFont('Helvetica-Bold', 14)
            c.drawString(x+6*_mm, y+6*_mm, str(value))
            if sub:
                c.setFillColor(colors.Color(0.65,0.70,0.78))
                c.setFont('Helvetica', 7)
                c.drawRightString(x+54*_mm, y+6*_mm, sub)

        draw_header('Relatório executivo de presença')

        y = h - 48*_mm
        c.setFillColor(colors.black)
        c.setFont('Helvetica-Bold', 12)
        c.drawString(18*_mm, y, m.title)
        y -= 6*_mm
        c.setFont('Helvetica', 9)
        c.setFillColor(colors.Color(0.2,0.24,0.3))
        c.drawString(18*_mm, y, f"Local: {m.location_name or '—'}  |  Raio: {m.radius_m} m")
        y -= 5*_mm
        c.drawString(18*_mm, y, f"Início: {_fmt_utc(m.starts_at)}   |   Fim: {_fmt_utc(m.ends_at)}")

        # KPIs
        y -= 18*_mm
        kpi_box(18*_mm, y, 'Presentes', checked, f"{pct:.1f}%")
        kpi_box(78*_mm, y, 'Pendentes', pending)
        kpi_box(138*_mm, y, 'Bloqueios', denied)

        # Mini gráfico
        gy = y - 20*_mm
        c.setFont('Helvetica-Bold', 9)
        c.setFillColor(colors.Color(0.05,0.08,0.12))
        c.drawString(18*_mm, gy+16*_mm, 'Distribuição')
        # axis area
        x0 = 18*_mm
        y0 = gy
        bw = 60*_mm
        maxv = max(checked, pending, denied, 1)
        bars = [('Presentes', checked, colors.Color(0.2,0.75,0.85)), ('Pendentes', pending, colors.Color(0.98,0.78,0.18)), ('Bloqueios', denied, colors.Color(0.95,0.35,0.45))]
        bx = x0
        for label, val, col in bars:
            bh = (val/maxv) * (12*_mm)
            c.setFillColor(col)
            c.roundRect(bx, y0, 16*_mm, bh, 2*_mm, stroke=0, fill=1)
            c.setFillColor(colors.Color(0.35,0.4,0.5))
            c.setFont('Helvetica', 7)
            c.drawCentredString(bx+8*_mm, y0-3*_mm, label[:3])
            c.setFillColor(colors.Color(0.05,0.08,0.12))
            c.drawCentredString(bx+8*_mm, y0+bh+2*_mm, str(val))
            bx += 20*_mm

        # Table title
        y = gy - 10*_mm
        c.setFillColor(colors.black)
        c.setFont('Helvetica-Bold', 10)
        c.drawString(18*_mm, y, 'Lista de convidados')
        y -= 4*_mm

        # Table header
        col_x = [18*_mm, 92*_mm, 120*_mm, 148*_mm]
        c.setFillColor(colors.Color(0.92,0.94,0.96))
        c.rect(18*_mm, y-6*_mm, w-36*_mm, 8*_mm, stroke=0, fill=1)
        c.setFillColor(colors.Color(0.05,0.08,0.12))
        c.setFont('Helvetica-Bold', 8)
        c.drawString(col_x[0], y-3*_mm, 'Nome')
        c.drawString(col_x[1], y-3*_mm, 'Área')
        c.drawString(col_x[2], y-3*_mm, 'Status')
        c.drawString(col_x[3], y-3*_mm, 'Check-in (UTC)')
        y -= 10*_mm

        c.setFont('Helvetica', 8)
        row_h = 6.5*_mm
        alt = False
        for inv in invitees:
            if y < 24*_mm:
                c.showPage()
                draw_header('Relatório executivo de presença (continuação)')
                y = h - 48*_mm
                # redraw header row
                c.setFillColor(colors.Color(0.92,0.94,0.96))
                c.rect(18*_mm, y-6*_mm, w-36*_mm, 8*_mm, stroke=0, fill=1)
                c.setFillColor(colors.Color(0.05,0.08,0.12))
                c.setFont('Helvetica-Bold', 8)
                c.drawString(col_x[0], y-3*_mm, 'Nome')
                c.drawString(col_x[1], y-3*_mm, 'Área')
                c.drawString(col_x[2], y-3*_mm, 'Status')
                c.drawString(col_x[3], y-3*_mm, 'Check-in (UTC)')
                y -= 10*_mm
                c.setFont('Helvetica', 8)

            if alt:
                c.setFillColor(colors.Color(0.98,0.99,1.0))
                c.rect(18*_mm, y-row_h+1*_mm, w-36*_mm, row_h, stroke=0, fill=1)
            alt = not alt

            name = (inv.name or '')[:48]
            area = (inv.area or '—')[:16]
            status = 'OK' if inv.checked_in else (inv.last_denied_reason or 'Pendente')
            chk = inv.checked_in_at.astimezone(timezone.utc).strftime('%d/%m/%Y %H:%M') if inv.checked_in_at else '—'

            c.setFillColor(colors.Color(0.05,0.08,0.12))
            c.drawString(col_x[0], y-3*_mm, name)
            c.drawString(col_x[1], y-3*_mm, area)
            c.drawString(col_x[2], y-3*_mm, status[:18])
            c.drawString(col_x[3], y-3*_mm, chk)
            y -= row_h

        c.showPage()
        c.save()
        pdf = buff.getvalue()
        buff.close()

        return Response(content=pdf, media_type='application/pdf', headers={
            'Content-Disposition': f'attachment; filename="presenca_{m.id}.pdf"'
        })
    finally:
        db.close()

@app.post("/api/meetings/{meeting_id}/invitees/{invitee_id}/resend")
def resend_invite(meeting_id: str, invitee_id: str, request: Request):
    """Reenviar convite por e-mail (1 clique)."""
    db = SessionLocal()
    try:
        m = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        inv = db.query(Invitee).filter(Invitee.id == invitee_id, Invitee.meeting_id == meeting_id).first()
        if not m or not inv:
            return JSONResponse(status_code=404, content={"error": "Reunião ou convidado não encontrado."})
        if not inv.email:
            return JSONResponse(status_code=400, content={"error": "Convidado sem e-mail."})

        # token do convidado (mesma regra do check-in)
        vf = as_utc_aware(inv.valid_from) or as_utc_aware(m.starts_at)
        vt = as_utc_aware(inv.valid_to) or as_utc_aware(m.ends_at)
        token = issue_invite_token(inv.id, m.id, vf, vt)
        link = build_checkin_link(request, token)

        subj, html = render_invite_email(
            meeting_name=m.title,
            invitee_name=inv.name,
            area=inv.area or '',
            checkin_link=link,
            valid_from=vf,
            valid_to=vt,
            require_code=bool(getattr(m, 'require_code', True)),
        )
        send_email(inv.email, subj, html)
        return {"ok": True}
    finally:
        db.close()

@app.get("/portal", response_class=HTMLResponse)
def portal_page():
    with open("static/portal.html", encoding="utf-8") as f:
        return f.read()

@app.get("/checkin", response_class=HTMLResponse)
def checkin_page():
    with open("static/checkin.html", encoding="utf-8") as f:
        return f.read()

@app.get("/reader", response_class=HTMLResponse)
def reader_page():
    with open("static/reader.html", encoding="utf-8") as f:
        return f.read()



@app.get("/reception", response_class=HTMLResponse)
def reception_page():
    with open("static/reception.html", encoding="utf-8") as f:
        return f.read()
# ===============================
# PORTAL API
# ===============================

# Jobs in-memory (bom o bastante para MVP local). Em produção, troque por Redis/DB.
JOBS: dict[str, dict] = {}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = JOBS.get(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": "Job não encontrado."})
    return job
@app.get("/api/meetings")
def list_meetings():
    db = SessionLocal()
    try:
        meetings = db.query(Meeting).order_by(Meeting.starts_at.desc()).all()
        return [{
            "id": m.id,
            "title": m.title,
            "location_name": m.location_name,
            "lat": m.lat,
            "lng": m.lng,
            "radius_m": m.radius_m,
            "starts_at": m.starts_at.isoformat(),
            "ends_at": m.ends_at.isoformat(),
        } for m in meetings]
    finally:
        db.close()


@app.get("/api/meetings/{meeting_id}")
def get_meeting(meeting_id: str):
    db = SessionLocal()
    try:
        m = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not m:
            return JSONResponse(status_code=404, content={"error": "Reunião não encontrada."})
        return {
            "id": m.id,
            "title": m.title,
            "location_name": m.location_name,
            "lat": m.lat,
            "lng": m.lng,
            "radius_m": m.radius_m,
            "starts_at": m.starts_at.isoformat(),
            "ends_at": m.ends_at.isoformat(),
            "require_code": bool(getattr(m, "require_code", True)),
            "email_subject": getattr(m, "email_subject", None),
            "email_body": getattr(m, "email_body", None),
        }
    finally:
        db.close()

@app.post("/api/meetings")
def create_meeting(data: dict = Body(...)):
    # Espera: title, location_name, lat, lng, radius_m, starts_at, ends_at (ISO)
    db = SessionLocal()
    try:
        m = Meeting(
            title=str(data.get("title","")).strip(),
            location_name=str(data.get("location_name","")).strip() or None,
            lat=float(data["lat"]),
            lng=float(data["lng"]),
            radius_m=int(data.get("radius_m", 150)),
            starts_at=parse_dt_utc(data["starts_at"]),
            ends_at=parse_dt_utc(data["ends_at"]),
        )
        if not m.title:
            return JSONResponse(status_code=400, content={"error": "Título é obrigatório."})
        if m.starts_at is None or m.ends_at is None:
            return JSONResponse(status_code=400, content={"error": "starts_at/ends_at inválidos."})
        db.add(m)
        db.commit()
        db.refresh(m)
        return {"id": m.id}
    finally:
        db.close()


@app.put("/api/meetings/{meeting_id}")
def update_meeting(meeting_id: str, data: dict = Body(...)):
    """Editar reunião (título/local/geo/horários)."""
    db = SessionLocal()
    try:
        m = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not m:
            return JSONResponse(status_code=404, content={"error": "Reunião não encontrada."})

        if "title" in data:
            m.title = str(data.get("title") or "").strip()
        if "location_name" in data:
            m.location_name = str(data.get("location_name") or "").strip() or None
        if "lat" in data:
            m.lat = float(data["lat"])
        if "lng" in data:
            m.lng = float(data["lng"])
        if "radius_m" in data:
            m.radius_m = int(data.get("radius_m") or 150)
        if "starts_at" in data and data.get("starts_at"):
            m.starts_at = parse_dt_utc(data["starts_at"]) or m.starts_at
        if "ends_at" in data and data.get("ends_at"):
            m.ends_at = parse_dt_utc(data["ends_at"]) or m.ends_at

        # Email template por reunião (editável no portal)
        if "email_subject" in data:
            m.email_subject = str(data.get("email_subject") or "").strip() or None
        if "email_body" in data:
            m.email_body = str(data.get("email_body") or "").strip() or None

        # Segurança extra: exigir código rotativo
        if "require_code" in data:
            m.require_code = bool(data.get("require_code"))

        if not m.title:
            return JSONResponse(status_code=400, content={"error": "Título é obrigatório."})

        db.commit()
        return {"ok": True}
    finally:
        db.close()


@app.delete("/api/meetings/{meeting_id}")
def delete_meeting(meeting_id: str):
    """Excluir reunião e convidados vinculados."""
    db = SessionLocal()
    try:
        m = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not m:
            return JSONResponse(status_code=404, content={"error": "Reunião não encontrada."})
        # remove convidados primeiro
        db.query(Invitee).filter(Invitee.meeting_id == meeting_id).delete()
        db.query(Meeting).filter(Meeting.id == meeting_id).delete()
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@app.get("/api/meetings/{meeting_id}/code")
def get_meeting_code(meeting_id: str):
    """Código rotativo da reunião (muda a cada 60s)."""
    db = SessionLocal()
    try:
        m = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not m:
            return JSONResponse(status_code=404, content={"error": "Reunião não encontrada."})
        code, remaining = meeting_code(getattr(m, "code_secret", ""), step_seconds=60)
        return {"code": code, "remaining_s": remaining}
    finally:
        db.close()


@app.put("/api/meetings/{meeting_id}/template")
def update_email_template(meeting_id: str, data: dict = Body(...)):
    """Salva template de e-mail (assunto/corpo) por reunião."""
    db = SessionLocal()
    try:
        m = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not m:
            return JSONResponse(status_code=404, content={"error": "Reunião não encontrada."})
        m.email_subject = str(data.get("subject") or "").strip() or None
        m.email_body = str(data.get("body") or "").strip() or None
        # permite toggles futuros
        if "require_code" in data:
            m.require_code = bool(data.get("require_code"))
        db.commit()
        return {"ok": True}
    finally:
        db.close()

@app.post("/api/meetings/{meeting_id}/invitees")
def add_invitee(meeting_id: str, data: dict = Body(...), request: Request = None):
    db = SessionLocal()
    try:
        m = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not m:
            return JSONResponse(status_code=404, content={"error":"Reunião não encontrada."})
        # valid_from/valid_to: se não vier, usamos a janela da reunião
        vf = parse_dt_utc(data.get("valid_from")) or as_utc_aware(m.starts_at)
        vt = parse_dt_utc(data.get("valid_to")) or as_utc_aware(m.ends_at)

        inv = Invitee(
            meeting_id=meeting_id,
            name=str(data.get("name", "")).strip(),
            email=str(data.get("email", "")).strip() or None,
            company=str(data.get("company", "")).strip() or None,
            area=str(data.get("area", "")).strip() or None,
            valid_from=vf,
            valid_to=vt,
            notes=str(data.get("notes", "")).strip() or None,
        )
        if not inv.name:
            return JSONResponse(status_code=400, content={"error":"Nome é obrigatório."})
        db.add(inv)
        db.commit()
        db.refresh(inv)

        token = issue_invite_token(inv.id, meeting_id, inv.valid_from, inv.valid_to)
        base_url = str(request.base_url).rstrip("/") if request else ""
        link = f"{base_url}/checkin?token={token}"
        return {"invitee_id": inv.id, "link": link}
    finally:
        db.close()

        link = build_checkin_link(request, token)

@app.post("/api/admin/grants")
def create_grant(payload: dict = Body(...), request: Request = None):
    admin = _require_admin(request)

    email = (payload.get("email") or "").strip().lower()
    grant_staff = bool(payload.get("grant_staff", False))
    grant_admin = bool(payload.get("grant_admin", False))
    is_active = bool(payload.get("is_active", True))

    if not email:
        raise HTTPException(status_code=400, detail="Email obrigatório")

    db = SessionLocal()
    try:
        grant = db.query(AccessGrant).filter(AccessGrant.email == email).first()

        if not grant:
            grant = AccessGrant(email=email)
            db.add(grant)

        grant.grant_staff = grant_staff
        grant.grant_admin = grant_admin
        grant.is_active = is_active

        db.commit()
        db.refresh(grant)

        return {
            "ok": True,
            "grant": {
                "email": grant.email,
                "grant_staff": grant.grant_staff,
                "grant_admin": grant.grant_admin,
                "is_active": grant.is_active,
            }
        }
    finally:
        db.close()


@app.get("/api/admin/grants")
def list_grants(request: Request):
    _require_admin(request)
    db = SessionLocal()
    try:
        grants = db.query(AccessGrant).order_by(AccessGrant.created_at.desc()).all()
        return [{
            "id": g.id,
            "email": g.email,
            "grant_staff": bool(g.grant_staff),
            "grant_admin": bool(g.grant_admin),
            "is_active": bool(g.is_active),
            "created_by": g.created_by,
            "created_at": (g.created_at.isoformat() if g.created_at else None),
        } for g in grants]
    finally:
        db.close()

@app.put("/api/admin/grants/{grant_id}")
def update_grant(grant_id: int, payload: dict = Body(...), request: Request = None):
    admin = _require_admin(request)

    grant_staff = bool(payload.get("grant_staff", False))
    grant_admin = bool(payload.get("grant_admin", False))
    is_active = bool(payload.get("is_active", True))

    db = SessionLocal()
    try:
        g = db.query(AccessGrant).filter(AccessGrant.id == grant_id).first()
        if not g:
            raise HTTPException(status_code=404, detail="Grant não encontrado")

        g.grant_staff = grant_staff
        g.grant_admin = grant_admin
        g.is_active = is_active

        # opcional: manter rastreio de quem mexeu por último
        if hasattr(g, "created_by") and not g.created_by:
            g.created_by = admin["email"]

        db.commit()
        db.refresh(g)
        return {"ok": True}
    finally:
        db.close()

@app.delete("/api/admin/grants/{grant_id}")
def delete_grant(grant_id: int, request: Request):
    _require_admin(request)

    db = SessionLocal()
    try:
        g = db.query(AccessGrant).filter(AccessGrant.id == grant_id).first()
        if not g:
            raise HTTPException(status_code=404, detail="Grant não encontrado")

        g.is_active = False
        db.commit()
        return {"ok": True}
    finally:
        db.close()

        
@app.post("/api/meetings/{meeting_id}/upload")
async def upload_invitees_excel(
    meeting_id: str,
    request: Request,
    background: BackgroundTasks,
    file: UploadFile = File(...),
):
    """Excel -> cria convidados e dispara e-mails em background.

    Colunas esperadas (case-insensitive):
    - nome
    - email
    - empresa (opcional)
    - área
    - valid_from (início)
    - valid_to (expiração)

    Retorna rápido com job_id para o Portal exibir progresso.
    """
    content = await file.read()
    df = pd.read_excel(BytesIO(content))

    # Normaliza nomes de colunas
    norm = {str(c).strip().lower(): c for c in df.columns}

    def col(*names):
        for n in names:
            if n in norm:
                return norm[n]
        return None

    c_nome = col("nome", "name")
    c_email = col("email", "e-mail", "e_mail")
    c_empresa = col("empresa", "company")
    c_area = col("área", "area")
    c_vf = col("valid_from", "validfrom", "inicio", "início")
    c_vt = col("valid_to", "validto", "fim", "expira", "expiracao", "expiração")

    if not c_nome:
        return JSONResponse(status_code=400, content={"error": "Coluna 'nome' obrigatória."})
    if not c_email:
        return JSONResponse(status_code=400, content={"error": "Coluna 'email' obrigatória."})
    if not c_area:
        return JSONResponse(status_code=400, content={"error": "Coluna 'Área' obrigatória."})
    if not c_vf or not c_vt:
        return JSONResponse(status_code=400, content={"error": "Colunas 'valid_from' e 'valid_to' obrigatórias."})

    db = SessionLocal()
    try:
        m = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not m:
            return JSONResponse(status_code=404, content={"error": "Reunião não encontrada."})

        base_url = str(request.base_url).rstrip("/")

        # Template de e-mail por reunião (editável no portal)
        default_subject = f"Confirmação de presença — {m.title}"
        subject_tpl = (getattr(m, "email_subject", None) or "").strip() or default_subject
        body_tpl = (getattr(m, "email_body", None) or "").strip() or None

        created = 0
        to_email: list[dict] = []
        failed = []

        for idx, row in df.iterrows():
            try:
                nome = str(row[c_nome]).strip() if not pd.isna(row[c_nome]) else ""
                email = str(row[c_email]).strip() if not pd.isna(row[c_email]) else ""
                empresa = (
                    str(row[c_empresa]).strip()
                    if (c_empresa and not pd.isna(row[c_empresa]))
                    else None
                )
                area = str(row[c_area]).strip() if not pd.isna(row[c_area]) else None
                vf = parse_dt_utc(row[c_vf])
                vt = parse_dt_utc(row[c_vt])

                if not nome:
                    raise ValueError("Nome vazio")
                if not vf or not vt:
                    raise ValueError("valid_from/valid_to inválidos")

                inv = Invitee(
                    meeting_id=meeting_id,
                    name=nome,
                    email=email or None,
                    company=empresa,
                    area=area,
                    valid_from=vf,
                    valid_to=vt,
                )
                db.add(inv)
                db.commit()
                db.refresh(inv)
                created += 1

                token = issue_invite_token(inv.id, meeting_id, inv.valid_from, inv.valid_to)
                link = f"{base_url}/checkin?token={token}"

                if inv.email:
                    to_email.append({
                        "to": inv.email,
                        "name": inv.name,
                        "meeting": m.title,
                        "area": inv.area,
                        "link": link,
                        "expires": inv.valid_to.isoformat() if inv.valid_to else None,
                        "row": int(idx) + 2,
                    })

            except Exception as e:
                failed.append({"row": int(idx) + 2, "error": str(e)})

        import uuid
        job_id = str(uuid.uuid4())
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "created": created,
            "emailed": 0,
            "total_to_email": len(to_email),
            "failed": failed,
        }

        def _run_email_job(job_id_: str, items: list[dict]):
            JOBS[job_id_]["status"] = "sending"
            emailed = 0
            for item in items:
                try:
                    subject = subject_tpl
                    # Variáveis disponíveis:
                    # {NOME}, {AREA}, {REUNIAO}, {VALID_TO}, {LINK}, {LOCAL}
                    vars_ = {
                        "{NOME}": item.get("name") or "",
                        "{AREA}": item.get("area") or "—",
                        "{REUNIAO}": item.get("meeting") or "",
                        "{VALID_TO}": item.get("expires") or "—",
                        "{LINK}": item.get("link") or "",
                        "{LOCAL}": m.location_name or "—",
                    }

                    if body_tpl:
                        html = body_tpl
                        for k, v in vars_.items():
                            html = html.replace(k, str(v))
                    else:
                        html = f"""
                        <div style='font-family:Segoe UI,Arial;line-height:1.5'>
                          <h2>Olá, {item['name']}!</h2>
                          <p>Seu link de presença para <b>{item['meeting']}</b> está pronto.</p>
                          <p><b>Área:</b> {item.get('area') or '—'}</p>
                          <p><a href='{item['link']}' style='display:inline-block;padding:12px 16px;background:#0ea5e9;color:#001018;border-radius:10px;text-decoration:none;font-weight:700'>Abrir e marcar presença</a></p>
                          <p style='opacity:.7'>Expira em: {item.get('expires') or '—'}</p>
                          <p style='opacity:.7'>No local, você precisará digitar um <b>código de confirmação</b> que muda a cada 60 segundos.</p>
                        </div>
                        """
                    send_email(
                        to_email=item["to"],
                        subject=subject,
                        html=html,
                    )
                    emailed += 1
                    JOBS[job_id_]["emailed"] = emailed
                except Exception as e:
                    JOBS[job_id_]["failed"].append({
                        "row": item.get("row"),
                        "error": f"Falha ao enviar e-mail: {e}",
                        "to": item.get("to"),
                    })

            JOBS[job_id_]["status"] = "done"

        background.add_task(_run_email_job, job_id, to_email)

        return {
            "created": created,
            "job_id": job_id,
            "total_to_email": len(to_email),
            "failed": failed,
        }

    finally:
        db.close()

@app.get("/api/meetings/{meeting_id}/attendance")
def meeting_attendance(meeting_id: str):
    db = SessionLocal()
    try:
        m = db.query(Meeting).filter(Meeting.id == meeting_id).first()
        if not m:
            return JSONResponse(status_code=404, content={"error":"Reunião não encontrada."})
        rows = db.query(Invitee).filter(Invitee.meeting_id == meeting_id).order_by(Invitee.checked_in.desc(), Invitee.name.asc()).all()
        return {
            "meeting": {"id": m.id, "title": m.title, "location_name": m.location_name, "starts_at": m.starts_at.isoformat(), "ends_at": m.ends_at.isoformat()},
            "invitees": [{
                "id": r.id,
                "name": r.name,
                "email": r.email,
                "company": r.company,
                "area": r.area,
                "checked_in": r.checked_in,
                "checked_in_at": r.checked_in_at.isoformat() if r.checked_in_at else None,
            } for r in rows]
        }
    finally:
        db.close()


@app.get("/api/meetings/{meeting_id}/stats")
def meeting_stats(meeting_id: str):
    db = SessionLocal()
    try:
        total = db.query(Invitee).filter(Invitee.meeting_id == meeting_id).count()
        checked = db.query(Invitee).filter(Invitee.meeting_id == meeting_id, Invitee.checked_in == True).count()
        denied = db.query(Invitee).filter(Invitee.meeting_id == meeting_id, Invitee.last_denied_reason != None).count()
        return {
            "total": total,
            "checked_in": checked,
            "pending": max(total - checked, 0),
            "rate": (checked / total) if total else 0.0,
            "denied_recent": denied,
        }
    finally:
        db.close()


@app.get("/api/meetings/{meeting_id}/export/csv")
def export_csv(meeting_id: str):
    db = SessionLocal()
    try:
        rows = db.query(Invitee).filter(Invitee.meeting_id == meeting_id).order_by(Invitee.name.asc()).all()
        data = []
        for r in rows:
            data.append({
                "nome": r.name,
                "email": r.email,
                "empresa": r.company,
                "Área": r.area,
                "valid_from": r.valid_from.isoformat() if r.valid_from else None,
                "valid_to": r.valid_to.isoformat() if r.valid_to else None,
                "checked_in": bool(r.checked_in),
                "checked_in_at": r.checked_in_at.isoformat() if r.checked_in_at else None,
                "denied_reason": r.last_denied_reason,
            })
        df = pd.DataFrame(data)
        out = df.to_csv(index=False).encode("utf-8")
        return StreamingResponse(BytesIO(out), media_type="text/csv", headers={"Content-Disposition": f"attachment; filename=presenca_{meeting_id}.csv"})
    finally:
        db.close()

@app.get("/api/meetings/{meeting_id}/export/xlsx")
def export_xlsx(meeting_id: str):
    db = SessionLocal()
    try:
        rows = db.query(Invitee).filter(Invitee.meeting_id == meeting_id).order_by(Invitee.name.asc()).all()
        data = []
        for r in rows:
            data.append({
                "Nome": r.name,
                "Email": r.email,
                "Empresa": r.company,
                "Área": r.area,
                "Validade início": r.valid_from.isoformat() if r.valid_from else None,
                "Validade fim": r.valid_to.isoformat() if r.valid_to else None,
                "Confirmado": "SIM" if r.checked_in else "NÃO",
                "Confirmado em": r.checked_in_at.isoformat() if r.checked_in_at else None,
                "Motivo bloqueio": r.last_denied_reason,
            })
        df = pd.DataFrame(data)
        bio = BytesIO()
        with pd.ExcelWriter(bio, engine="openpyxl") as writer:
            df.to_excel(writer, sheet_name="Presença", index=False)
        bio.seek(0)
        return StreamingResponse(bio, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": f"attachment; filename=presenca_{meeting_id}.xlsx"})
    finally:
        db.close()

# ===============================
# GUEST API
# ===============================
@app.get("/api/checkin/info")
def checkin_info(token: str):
    """Retorna dados mínimos para montar a tela do convidado (sem expor lista toda)."""
    try:
        payload = jwt.decode(token, INVITE_SECRET, algorithms=[ALGORITHM], audience=INVITE_AUDIENCE, issuer=INVITE_ISSUER)
    except Exception:
        return JSONResponse(status_code=401, content={"error":"Token inválido/expirado."})

    db = SessionLocal()
    try:
        inv = db.query(Invitee).filter(Invitee.id == payload["invitee_id"], Invitee.meeting_id == payload["meeting_id"]).first()
        m = db.query(Meeting).filter(Meeting.id == payload["meeting_id"]).first()
        if not inv or not m:
            return JSONResponse(status_code=404, content={"error":"Convite não encontrado."})
        return {
            "invitee": {
                "name": inv.name,
                "company": inv.company,
                "area": inv.area,
                "checked_in": inv.checked_in,
                "valid_from": inv.valid_from.isoformat() if inv.valid_from else None,
                "valid_to": inv.valid_to.isoformat() if inv.valid_to else None,
            },
            "meeting": {
                "title": m.title,
                "location_name": m.location_name,
                "starts_at": m.starts_at.isoformat(),
                "ends_at": m.ends_at.isoformat(),
                "lat": m.lat,
                "lng": m.lng,
                "radius_m": m.radius_m,
            },
            "policy": {
                "requires_geolocation": True,
                "requires_code": bool(getattr(m, "require_code", True)),
                "code_step_s": 60,
            }
        }
    finally:
        db.close()

@app.post("/api/checkin")
def do_checkin(data: dict = Body(...), request: Request = None):
    """Check-in com geofence + janela de horário + 1x."""
    token = data.get("token","")
    lat = data.get("lat")
    lng = data.get("lng")
    accuracy = data.get("accuracy_m")
    code = data.get("code")

    if lat is None or lng is None:
        return JSONResponse(status_code=400, content={"error":"Localização não informada."})

    try:
        payload = jwt.decode(token, INVITE_SECRET, algorithms=[ALGORITHM], audience=INVITE_AUDIENCE, issuer=INVITE_ISSUER)
    except Exception:
        return JSONResponse(status_code=401, content={"error":"Token inválido/expirado."})

    db = SessionLocal()
    try:
        inv = db.query(Invitee).filter(Invitee.id == payload["invitee_id"], Invitee.meeting_id == payload["meeting_id"]).first()
        m = db.query(Meeting).filter(Meeting.id == payload["meeting_id"]).first()
        if not inv or not m:
            return JSONResponse(status_code=404, content={"error":"Convite não encontrado."})
        if inv.checked_in:
            return {"ok": True, "message": "Presença já confirmada.", "checked_in_at": inv.checked_in_at.isoformat() if inv.checked_in_at else None}

        # Código rotativo por reunião (TOTP simplificado)
        if bool(getattr(m, "require_code", True)):
            if not is_valid_meeting_code(getattr(m, "code_secret", ""), str(code or ""), step_seconds=60):
                inv.last_denied_reason = "bad_code"
                db.commit()
                return JSONResponse(status_code=403, content={"error": "Código de confirmação inválido.", "reason": "bad_code"})

        now = utcnow()

        # Regra: quem manda é o convite (valid_from/valid_to).
        vf = as_utc_aware(inv.valid_from) or as_utc_aware(m.starts_at)
        vt = as_utc_aware(inv.valid_to) or as_utc_aware(m.ends_at)

        if vf and now < vf:
            inv.last_denied_reason = "too_early"
            db.commit()
            return JSONResponse(status_code=403, content={"error": "Ainda não está no horário permitido.", "reason": "too_early"})
        if vt and now > vt:
            inv.last_denied_reason = "expired"
            db.commit()
            return JSONResponse(status_code=403, content={"error": "Convite expirado.", "reason": "expired"})

        dist = haversine_m(float(lat), float(lng), float(m.lat), float(m.lng))
        acc = float(accuracy) if accuracy is not None else 0.0

        # tolerância = raio + acurácia + 25m (para GPS ruim em prédio)
        allowed = float(m.radius_m) + max(acc, 0.0) + 25.0
        if dist > allowed:
            inv.last_denied_reason = "out_of_range"
            db.commit()
            return JSONResponse(status_code=403, content={"error":"Você não parece estar no local do evento.", "distance_m": dist, "allowed_m": allowed, "reason": "out_of_range"})

        inv.checked_in = True
        inv.checked_in_at = now
        inv.checkin_lat = float(lat)
        inv.checkin_lng = float(lng)
        inv.checkin_accuracy_m = acc

        # Auditoria leve (software-only)
        ua = (request.headers.get("user-agent") if request else "") or ""
        inv.checkin_user_agent = ua[:2000]
        import hashlib
        fp_raw = (ua + "|" + (request.headers.get("accept-language") if request else "")).encode("utf-8", errors="ignore")
        inv.checkin_device_hash = hashlib.sha256(fp_raw).hexdigest()[:32]
        inv.last_denied_reason = None
        db.commit()

        return {"ok": True, "message": "Presença confirmada ✅", "checked_in_at": inv.checked_in_at.isoformat()}
    finally:
        db.close()
