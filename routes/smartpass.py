from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse, StreamingResponse
from datetime import datetime, timezone, date
from io import BytesIO
import jwt
import qrcode
import pandas as pd

from sqlalchemy import Column, String, DateTime, Boolean
from sqlalchemy.orm import declarative_base

from models.database import SessionLocal, engine
from services.checkin_service import check_in
from services.smartpass_service import (
    generate_smartpass_token,
    SMARTPASS_SECRET,
    SMARTPASS_ISSUER,
    SMARTPASS_AUDIENCE,
)

ALGORITHM = "HS256"

router = APIRouter()

# ===============================
# MODEL (DB)
# ===============================
Base = declarative_base()

class SmartPass(Base):
    __tablename__ = "smartpasses"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, nullable=False)
    email = Column(String, nullable=True)

    company = Column(String, nullable=False)
    event = Column(String, nullable=False)
    pass_type = Column(String, nullable=True)

    valid_from = Column(DateTime, nullable=True)
    valid_to = Column(DateTime, nullable=True)

    checked_in = Column(Boolean, default=False)
    checked_in_at = Column(DateTime, nullable=True)


Base.metadata.create_all(bind=engine)

# ===============================
# ISSUE
# ===============================
@router.post("/issue")
def issue_smartpass(data: dict):
    token = generate_smartpass_token(
        name=data["name"],
        company=data["company"],
        event=data["event"],
        pass_type=data.get("type", "Visitante"),
    )

    payload = jwt.decode(
        token,
        SMARTPASS_SECRET,
        algorithms=[ALGORITHM],
        audience=SMARTPASS_AUDIENCE,
        issuer=SMARTPASS_ISSUER,
    )

    db = SessionLocal()
    try:
        vf = datetime.fromtimestamp(payload["iat"])
        vt = datetime.fromtimestamp(payload["exp"])

        sp = db.query(SmartPass).filter(SmartPass.id == payload["id"]).first()
        if not sp:
            sp = SmartPass(
                id=payload["id"],
                name=payload["name"],
                company=payload["company"],
                event=payload["event"],
                pass_type=data.get("type", "Visitante"),
                valid_from=vf,
                valid_to=vt,
                checked_in=False
            )
            db.add(sp)
        db.commit()
    finally:
        db.close()

    return {
        "valid": True,
        "smartpass_id": payload["id"],
        "token": token
    }

# ===============================
# VALIDATE
# ===============================
@router.post("/validate")
def validate_qr(payload: dict = Body(...)):
    raw = (payload.get("token") or "").strip()
    if not raw:
        return {"valid": False, "reason": "Token ausente"}

    smartpass_id = raw
    if raw.count(".") == 2:
        decoded = jwt.decode(
            raw,
            SMARTPASS_SECRET,
            algorithms=[ALGORITHM],
            audience=SMARTPASS_AUDIENCE,
            issuer=SMARTPASS_ISSUER,
        )
        smartpass_id = decoded.get("id")

    db = SessionLocal()
    try:
        sp = db.query(SmartPass).filter(SmartPass.id == smartpass_id).first()
        if not sp:
            return {"valid": False, "reason": "SmartPass não encontrado"}

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if sp.valid_from and now < sp.valid_from:
            return {"valid": False, "reason": "Ainda não válido"}
        if sp.valid_to and now > sp.valid_to:
            return {"valid": False, "reason": "Expirado"}

        result = check_in(db, smartpass_id)
        if not result.get("valid", False):
            return result

        return {
            "valid": True,
            "name": sp.name,
            "company": sp.company,
            "event": sp.event,
            "checked_in_at": result.get("checked_in_at")
        }
    finally:
        db.close()

# ===============================
# EXPORT
# ===============================
@router.get("/export/today.xlsx")
def export_today():
    today = date.today()
    start = datetime(today.year, today.month, today.day, 0, 0, 0)
    end = datetime(today.year, today.month, today.day, 23, 59, 59)

    db = SessionLocal()
    try:
        rows = db.query(SmartPass).filter(
            SmartPass.checked_in == True,
            SmartPass.checked_in_at >= start,
            SmartPass.checked_in_at <= end
        ).all()

        data = [{
            "checked_in_at": sp.checked_in_at,
            "smartpass_id": sp.id,
            "name": sp.name,
            "company": sp.company,
            "event": sp.event
        } for sp in rows]
    finally:
        db.close()

    out = BytesIO()
    pd.DataFrame(data).to_excel(out, index=False)
    out.seek(0)

    return StreamingResponse(
        out,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="leituras_hoje.xlsx"'}
    )
