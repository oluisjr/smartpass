import time
import json
from pathlib import Path

import jwt

from wallet.wallet_config import (
    SAMSUNG_CARD_ID,           # ex: "3irbs944vie00"
    SAMSUNG_ISSUER_ID,         # ex: "4138017514550138240" (Partner ID/Code)
    SAMSUNG_PRIVATE_KEY_PATH,  # ex: "key.pem"
    SAMSUNG_TOKEN_TTL_SECONDS, # ex: 60
    SAMSUNG_AUDIENCE,          # ex: "1449673255902172160"
    SAMSUNG_CERTIFICATE_ID,    # kid do Partner Wallet
)


def build_card_payload(smartpass: dict, ref_id: str) -> dict:
    """
    Formato 'generic/others' conforme exemplos do Partner Wallet.
    REGRAS IMPORTANTES:
    - refId consistente (top-level e dentro do card)
    - serial1.value deve ser algo que o seu /validate aceite
    """
    now_ms = int(time.time() * 1000)
    start_date = now_ms
    end_date = now_ms + (3 * 24 * 60 * 60 * 1000)

    smartpass_id = smartpass["id"]  # ID do seu software (uuid)

    return {
        "type": "generic",
        "subType": "others",
        "data": [
            {
                "createdAt": now_ms,
                "updatedAt": now_ms,
                "language": "pt",
                "refId": ref_id,  # ✅ igual ao smartpass_id
                "attributes": {
                    "title": "SmartPass CSN",
                    "subtitle": (smartpass.get("event") or "Identificação")[:30],
                    "providerName": smartpass.get("company") or "CSN",

                    "startDate": start_date,
                    "startDate.utcOffset": "UTC-3",
                    "endDate": end_date,
                    "endDate.utcOffset": "UTC-3",

                    "text1": smartpass_id,
                    "text2": smartpass.get("company", ""),
                    "text3": smartpass.get("name", ""),
                    "text4": smartpass.get("event", ""),
                    "text5": smartpass.get("type", "Visitante"),
                    "text6": " ",

                    # ✅ QR = smartpass_id (seu /validate aceita ID puro)
                    "serial1.value": smartpass_id,
                    "serial1.serialType": "QRCODE",
                    "serial1.ptFormat": "QRCODE",
                    "serial1.ptSubFormat": "QR_CODE",

                    "locations": "[]",
                    "noticeDesc": "{}",
                    "csInfo": "{}",
                }
            }
        ]
    }


def _read_private_key_text(path: str) -> str:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Chave privada não encontrada: {path}")

    key = p.read_text(encoding="utf-8").strip()

    # sanity checks úteis pra evitar “Invalid jwt” por key errada
    if "BEGIN" not in key or "PRIVATE KEY" not in key:
        raise ValueError(
            "Arquivo de chave não parece um PEM válido. "
            "Ele deve conter 'BEGIN ... PRIVATE KEY'."
        )

    return key


def generate_samsung_cdata(smartpass: dict) -> str:
    """
    Gera o cdata (JWT) para o script samsungWallet.addButton / <samsung:wallet>.
    Esse é o formato do guia: cardId + partnerCode + cdata(JWT).
    """
    now = int(time.time())

    if not smartpass.get("id"):
        raise ValueError("smartpass precisa conter 'id' (smartpass_id do seu sistema).")

    # ✅ refId = ID do seu software
    ref_id = smartpass["id"]

    private_key = _read_private_key_text(SAMSUNG_PRIVATE_KEY_PATH)

    card = build_card_payload(smartpass, ref_id)

    payload = {
        "card": card,

        # JWT padrão (obrigatório)
        "iss": SAMSUNG_ISSUER_ID,     # partnerId / partnerCode
        "aud": SAMSUNG_AUDIENCE,      # "1449673255902172160"
        "iat": now,
        "exp": now + int(SAMSUNG_TOKEN_TTL_SECONDS),

        # Samsung (obrigatório no guia)
        "cardId": SAMSUNG_CARD_ID,    # "3irbs944vie00"
        "refId": ref_id,
    }

    headers = {
        "kid": SAMSUNG_CERTIFICATE_ID,  # certificate id do Partner Wallet
        "typ": "JWT",
        "alg": "RS256",
    }

    token = jwt.encode(
        payload=payload,
        key=private_key,
        algorithm="RS256",
        headers=headers,
    )

    return token


# ===========================
# TESTE LOCAL (opcional)
# ===========================
def test_generate_token():
    test_data = {
        "id": "76b5d873-ee23-412d-bdc2-d037f50a4271",
        "name": "LUIS IGNACIO JUNIOR",
        "company": "CSN",
        "event": "Processo Seletivo | ESTÁGIO",
        "type": "candidate",
    }

    token = generate_samsung_cdata(test_data)

    with open("samsung_jwt_debug.txt", "w", encoding="utf-8") as f:
        f.write(token)

    decoded = jwt.decode(token, options={"verify_signature": False})
    with open("samsung_payload_debug.json", "w", encoding="utf-8") as f:
        json.dump(decoded, f, indent=2, ensure_ascii=False)

    print("✅ Token salvo: samsung_jwt_debug.txt")
    print("✅ Payload salvo: samsung_payload_debug.json")


if __name__ == "__main__":
    test_generate_token()
