import time
import jwt
from wallet.wallet_config import (
    GOOGLE_WALLET_ISSUER_ID,
    GOOGLE_WALLET_CLASS_ID,
    SERVICE_ACCOUNT_INFO,
)
from wallet.models import WalletPassData


def create_wallet_jwt(data: WalletPassData) -> str:
    now = int(time.time())

    payload = {
        "iss": SERVICE_ACCOUNT_INFO["client_email"],
        "aud": "google",
        "typ": "savetowallet",
        "iat": now,
        "payload": {
            "genericObjects": [
                {
                    "id": f"{GOOGLE_WALLET_ISSUER_ID}.{data.smartpass_id}",
                    "classId": f"{GOOGLE_WALLET_ISSUER_ID}.{GOOGLE_WALLET_CLASS_ID}",

                    "backgroundColor": {
                        "rgb": {
                            "red": 0,
                            "green": 63,
                            "blue": 135
                        }
                    },

                    "heroImage": {
                        "sourceUri": {
                            "uri": "https://raw.githubusercontent.com/oluisjr/Assets/edf86b0f99d600e0a19e19bc6907aedc596492ca/csn_wallet_hero.png"
                        },
                        "contentDescription": {
                            "defaultValue": {
                                "language": "pt-BR",
                                "value": "Identidade visual CSN"
                            }
                        }
                    },

                    "logo": {
                        "sourceUri": {
                            "uri": "https://raw.githubusercontent.com/oluisjr/Assets/853ac41683b98dfd0c27e66e091567e71af8e24b/logoAzul.png"
                        },
                        "contentDescription": {
                            "defaultValue": {
                                "language": "pt-BR",
                                "value": "CSN"
                            }
                        }
                    },

                    "cardTitle": {
                        "defaultValue": {
                            "language": "pt-BR",
                            "value": "SmartPass"
                        }
                    },

                    "subheader": {
                        "defaultValue": {
                            "language": "pt-BR",
                            "value": "Processo Seletivo | ESTÁGIO"
                        }
                    },

                    "header": {
                        "defaultValue": {
                            "language": "pt-BR",
                            "value": data.name.upper()
                        }
                    },

                    "barcode": {
                        "type": "QR_CODE",
                        "value": data.smartpass_id
                    },

                    "textModulesData": [
                        {
                            "header": "Empresa",
                            "body": data.company
                        }
                    ],
                }
            ]
        },
    }

    signed_jwt = jwt.encode(
        payload,
        SERVICE_ACCOUNT_INFO["private_key"],
        algorithm="RS256",
    )

    return signed_jwt
