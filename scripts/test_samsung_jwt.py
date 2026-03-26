import sys
sys.path.append('.')  # Para importar seus módulos

from wallet.samsung_wallet import generate_samsung_cdata

# Teste com dados de exemplo
test_data = {
    "id": "test-123",
    "name": "LUIS IGNACIO JUNIOR",
    "company": "CSN", 
    "event": "Processo Seletivo | ESTÁGIO",
    "type": "candidate"
}

print("🧪 Testando geração de JWT Samsung...")
try:
    jwt_token = generate_samsung_cdata(test_data)
    print(f"✅ Token gerado: {jwt_token[:50]}...")
    
    # Salva para inspecionar
    with open("debug_jwt.txt", "w") as f:
        f.write(jwt_token)
    print("📁 Token salvo em debug_jwt.txt")
    
    # Pode inspecionar em jwt.io
    print("🔗 Inspecione em: https://jwt.io/")
    
except Exception as e:
    print(f"❌ Erro: {e}")
    import traceback
    traceback.print_exc()