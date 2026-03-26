import qrcode
from io import BytesIO

def generate_qr(smartpass_id: str) -> BytesIO:
    qr = qrcode.make(smartpass_id)
    buffer = BytesIO()
    qr.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
