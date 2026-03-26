from sqlalchemy import Column, String, DateTime, Boolean, Float, ForeignKey, Text
from datetime import datetime
import uuid

from .database import Base

def _uuid():
    return str(uuid.uuid4())

class Invitee(Base):
    __tablename__ = "invitees"

    id = Column(String, primary_key=True, default=_uuid, index=True)
    meeting_id = Column(String, ForeignKey("meetings.id"), nullable=False, index=True)

    name = Column(String, nullable=False)
    email = Column(String, nullable=True, index=True)
    company = Column(String, nullable=True)

    # Segmentação (ex.: Mecânica, Elétrica, Tecnologia, ...)
    area = Column(String, nullable=True)

    # Janela de validade do convite (UTC recomendado)
    valid_from = Column(DateTime, nullable=True)
    valid_to = Column(DateTime, nullable=True)

    checked_in = Column(Boolean, default=False)
    checked_in_at = Column(DateTime, nullable=True)

    checkin_lat = Column(Float, nullable=True)
    checkin_lng = Column(Float, nullable=True)
    checkin_accuracy_m = Column(Float, nullable=True)

    checkin_device_hash = Column(String, nullable=True)
    checkin_user_agent = Column(Text, nullable=True)
    last_denied_reason = Column(String, nullable=True)

    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
