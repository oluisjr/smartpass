# SmartPass Portal (v2)

## Rodar local
```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --reload
```

Abra:
- Portal: http://127.0.0.1:8000/portal
- Painel (leitor): http://127.0.0.1:8000/reader

## Regras de check-in
- Janela de horário: libera 15 min antes e até 30 min depois (ajuste no código `issue_invite_token` e validação do `/api/checkin`).
- Geofence: centro (lat/lng) + raio (meters) definido ao criar reunião no mapa.
- Tolerância extra: raio + (acurácia GPS) + 25m.

## Excel
Colunas:
- nome (obrigatório)
- email (opcional, mas recomendado pra disparo automático)
- empresa (opcional)
