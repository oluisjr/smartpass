from sqlalchemy import Column, Integer, String, Boolean, DateTime
from datetime import datetime, timezone
from .database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)

    # identidade
    email = Column(String, unique=True, index=True, nullable=False)
    display_name = Column(String, nullable=True)
    provider = Column(String, nullable=False)  # "local" | "azure"

    # permissões
    is_active = Column(Boolean, default=True)
    is_staff = Column(Boolean, default=False)
    is_admin = Column(Boolean, default=False)

    # auditoria
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def role(self) -> str:
        """
        Papel efetivo do usuário.
        Mantém compatibilidade com o front (/api/me).
        """
        if self.is_admin:
            return "admin"
        if self.is_staff:
            return "staff"
        return "user"

    def to_dict(self):
        """
        Serialização padrão para API.
        """
        return {
            "id": self.id,
            "email": self.email,
            "display_name": self.display_name,
            "provider": self.provider,
            "is_active": self.is_active,
            "is_staff": self.is_staff,
            "is_admin": self.is_admin,
            "role": self.role(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
