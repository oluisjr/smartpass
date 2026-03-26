from wallet.wallet_jwt import create_wallet_jwt
from wallet.models import WalletPassData


def generate_google_wallet_link(data: WalletPassData) -> str:
    jwt_token = create_wallet_jwt(data)
    return f"https://pay.google.com/gp/v/save/{jwt_token}"
