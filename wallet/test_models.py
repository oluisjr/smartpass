from wallet.models import WalletPassData
from wallet.google_wallet import generate_google_wallet_link


def test_wallet_pass():
    data = WalletPassData(
        smartpass_id="teste149",
        name="LUIS IGNACIO JUNIOR",
        company="CSN - Companhia Siderurgica Nacional",
        event="Processo Seletivo | ESTÁGIO",
        qr_token="TOKEN-DE-TESTE-123",
    )

    link = generate_google_wallet_link(data)
    print("LINK GERADO COM SUCESSO ✅")
    print(link)


if __name__ == "__main__":
    test_wallet_pass()
