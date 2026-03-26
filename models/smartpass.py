from sqlalchemy import Column, String, DateTime, Boolean
from .base import Base

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
