import jwt
import time
from uuid import uuid4
from typing import Dict

# ============================================================
# CONFIGURAÇÕES DO SMARTPASS
# ============================================================

SMARTPASS_SECRET = "3irbs944vie00"  # depois vai para env
SMARTPASS_ISSUER = "4138017514550138240"
SMARTPASS_AUDIENCE = "1449673255902172160"

SMARTPASS_EXP_MINUTES = 30

# ============================================================
# GERA TOKEN SMARTPASS (OFICIAL DO SISTEMA)
# ============================================================

def generate_smartpass_token(
    *,
    name: str,
    company: str,
    event: str,
    pass_type: str = "visitor",
) -> str:
    """
    Gera o token oficial do SmartPass.
    Esse token é:
    - validado pelo backend (/token-info)
    - exibido no card web
    - usado como base para Samsung Wallet
    """

    now = int(time.time())
    exp = now + (SMARTPASS_EXP_MINUTES * 60)

    payload: Dict = {
        "id": str(uuid4()),
        "name": name,
        "company": company,
        "event": event,
        "type": pass_type,

        "iss": SMARTPASS_ISSUER,
        "aud": SMARTPASS_AUDIENCE,

        "iat": now,
        "nbf": now,
        "exp": exp,
    }

    token = jwt.encode(
        payload,
        SMARTPASS_SECRET,
        algorithm="HS256",
    )

    return token
