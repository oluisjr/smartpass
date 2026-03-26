from services.smartpass_service import generate_smartpass_token

if __name__ == "__main__":
    token = generate_smartpass_token(
        name="LUIS IGNACIO JUNIOR",
        company="CSN",
        event="Processo Seletivo | ESTÁGIO",
        pass_type="candidate"
    )

    print(token)
