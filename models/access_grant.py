from sqlalchemy import Column, Integer, String, Boolean, DateTime
from datetime import datetime, timezone
from .database import Base

class AccessGrant(Base):
    __tablename__ = "access_grants"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)

    grant_staff = Column(Boolean, default=False)
    grant_admin = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)

    created_by = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
