from pathlib import Path
import json

BASE_DIR = Path(__file__).resolve().parent

GOOGLE_WALLET_ISSUER_ID = "3388000000023068955"
GOOGLE_WALLET_CLASS_ID = "smartpass_class"

SERVICE_ACCOUNT_FILE = BASE_DIR / "service-account.json"

WALLET_AUDIENCE = "google"
WALLET_TYPE = "generic"

with open(SERVICE_ACCOUNT_FILE, "r", encoding="utf-8") as f:
    SERVICE_ACCOUNT_INFO = json.load(f)

# Samsung Wallet
SAMSUNG_CARD_ID = "3irbs944vie00"
SAMSUNG_ISSUER_ID = "4138017514550138240"
SAMSUNG_PRIVATE_KEY_PATH = BASE_DIR / "samsung_private_key.pem"

SAMSUNG_CERTIFICATE_ID = "uvKU"
SAMSUNG_AUDIENCE = "1449673255902172160"
SAMSUNG_TOKEN_TTL_SECONDS = 60
