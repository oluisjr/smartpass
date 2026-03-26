import time, json
from pathlib import Path
import jwt

# coloque seus valores aqui:
SAMSUNG_CARD_ID = "3irbs944vie00"
SAMSUNG_ISSUER_ID = "4138017514550138240"
SAMSUNG_AUDIENCE = "1449673255902172160"
SAMSUNG_CERTIFICATE_ID = "uvKU"
SAMSUNG_PRIVATE_KEY_PATH = "wallet/samsung_private_key.pem"

def main():
    now = int(time.time())
    smartpass_id = "76b5d873-ee23-412d-bdc2-d037f50a4271"  # teste

    private_key = Path(SAMSUNG_PRIVATE_KEY_PATH).read_text(encoding="utf-8").strip()

    card = {
        "type": "generic",
        "subType": "others",
        "data": [{
            "createdAt": now * 1000,
            "updatedAt": now * 1000,
            "language": "pt",
            "refId": smartpass_id,
            "attributes": {
                "title": "SmartPass CSN",
                "subtitle": "Teste",
                "providerName": "CSN",
                "text1": smartpass_id,
                "text2": "CSN",
                "text3": "LUIS",
                "text4": "Teste",
                "text5": "candidate",
                "text6": " ",
                "serial1.value": smartpass_id,
                "serial1.serialType": "QRCODE",
                "serial1.ptFormat": "QRCODE",
                "serial1.ptSubFormat": "QR_CODE",
                "locations": "[]",
                "noticeDesc": "{}",
                "csInfo": "{}",
            }
        }]
    }

    payload = {
        "card": card,
        "iss": SAMSUNG_ISSUER_ID,
        "aud": SAMSUNG_AUDIENCE,
        "iat": now,
        "exp": now + 60,
        "cardId": SAMSUNG_CARD_ID,
        "refId": smartpass_id,
    }

    headers = {"kid": SAMSUNG_CERTIFICATE_ID, "typ": "JWT", "alg": "RS256"}

    token = jwt.encode(payload, private_key, algorithm="RS256", headers=headers)

    print("TOKEN:", token[:80] + "...")
    with open("samsung_min_token.txt", "w", encoding="utf-8") as f:
        f.write(token)

    decoded = jwt.decode(token, options={"verify_signature": False})
    with open("samsung_min_payload.json", "w", encoding="utf-8") as f:
        json.dump(decoded, f, indent=2, ensure_ascii=False)

    print("✅ arquivos gerados: samsung_min_token.txt e samsung_min_payload.json")

if __name__ == "__main__":
    main()
