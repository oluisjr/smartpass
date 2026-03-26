from sqlalchemy import Column, String, DateTime, Float, Integer, Boolean, Text
from datetime import datetime
import uuid
import secrets
import base64

from .database import Base


def _gen_secret_b32() -> str:
    # base32 sem '=' (fica bonito e compacto)
    raw = secrets.token_bytes(20)
    return base64.b32encode(raw).decode("utf-8").rstrip("=")


class Meeting(Base):
    __tablename__ = "meetings"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    title = Column(String, nullable=False)
    location_name = Column(String, nullable=True)

    lat = Column(Float, nullable=False)
    lng = Column(Float, nullable=False)
    radius_m = Column(Integer, nullable=False, default=150)

    # Armazenamos em UTC (datetime naive ou aware, mas a app trata como UTC)
    starts_at = Column(DateTime, nullable=False)
    ends_at = Column(DateTime, nullable=False)

    # Segurança extra (software-only): código rotativo por reunião
    code_secret = Column(String, nullable=False, default=_gen_secret_b32)
    require_code = Column(Boolean, nullable=False, default=True)

    # Template editável de e-mail por reunião
    email_subject = Column(String, nullable=True)
    email_body = Column(Text, nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
