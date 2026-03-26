from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from jose import jwt
import os

router = APIRouter()

JWT_SECRET = os.getenv("JWT_SECRET", "smartpass-secret")
JWT_ALG = "HS256"


@router.get("/wallet/samsung/{token}", response_class=HTMLResponse)
def samsung_wallet(token: str, request: Request):
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except Exception:
        return HTMLResponse("<h2>Token inválido</h2>", status_code=401)

    with open("static/samsung_wallet.html", encoding="utf-8") as f:
        html = f.read()

    html = (
        html.replace("{{NAME}}", payload.get("name", ""))
            .replace("{{EVENT}}", payload.get("event", ""))
            .replace("{{COMPANY}}", payload.get("company", ""))
            .replace("{{QR_URL}}", f"/qr/{token}")
    )

    return HTMLResponse(html)


from fastapi import APIRouter
from wallet.samsung_wallet import generate_samsung_cdata

router = APIRouter(prefix="/wallet")

@router.post("/samsung")
def issue_samsung_wallet(data: dict):
    cdata = generate_samsung_cdata({
        "name": data["name"],
        "company": data["company"],
        "event": data["event"],
        "qrValue": data["token"],
    })

    return {
        "add_to_wallet_url": (
            f"https://a.swallet.link/atw/v3/"
            f"{data['card_id']}#Clip?cdata={cdata}"
        )
    }
