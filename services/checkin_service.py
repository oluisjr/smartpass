from datetime import datetime, timezone
from sqlalchemy.orm import Session
from models import SmartPass


def check_in(db: Session, smartpass_id: str) -> dict:
    sp = db.query(SmartPass).filter(SmartPass.id == smartpass_id).first()

    if not sp:
        return {"valid": False, "reason": "SmartPass inexistente"}

    # Evita duplicado
    if getattr(sp, "checked_in", False):
        return {"valid": False, "reason": "SmartPass já utilizado"}

    now = datetime.now(timezone.utc)  # ✅ UTC timezone-aware

    sp.checked_in = True
    sp.checked_in_at = now
    db.commit()
    db.refresh(sp)

    return {
        "valid": True,
        "checked_in_at": sp.checked_in_at.isoformat() if sp.checked_in_at else None
    }
