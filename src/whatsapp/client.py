import os
import requests
from dotenv import load_dotenv

# --------------------------------------------------
# ENV & CONFIG
# --------------------------------------------------

load_dotenv()

ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERSION = os.getenv("VERSION", "v24.0")

WHATSAPP_API_URL = f"https://graph.facebook.com/{VERSION}/{PHONE_NUMBER_ID}/messages"

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json",
}

# --------------------------------------------------
# 🔥 CHANGED: REUSED HTTP SESSION (BIG LATENCY WIN)
# --------------------------------------------------

SESSION = requests.Session()                     # ✅ NEW
SESSION.headers.update(HEADERS)                  # ✅ NEW

# --------------------------------------------------
# LOW-LEVEL WHATSAPP API CALL
# --------------------------------------------------

def post_whatsapp(payload: dict, retries: int = 2):
    """
    Sends payload to WhatsApp API.
    Retries on failure.
    Blocking call (runs in background threads).
    """
    for attempt in range(retries):
        try:
            response = SESSION.post(             # 🔄 CHANGED (was requests.post)
                WHATSAPP_API_URL,
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            return response
        except requests.exceptions.RequestException:
            if attempt == retries - 1:
                raise

# --------------------------------------------------
# SEND HELPERS
# --------------------------------------------------

def send_text(to: str, body: str):
    return post_whatsapp({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    })

def send_image(to: str, image_url: str, caption: str = ""):
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "image",
        "image": {"link": image_url},
    }
    if caption:
        payload["image"]["caption"] = caption
    return post_whatsapp(payload)

def send_cta_button(to: str, text: str, url: str, display_text: str):
    return post_whatsapp({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "cta_url",
            "body": {"text": text},
            "action": {
                "name": "cta_url",
                "parameters": {
                    "display_text": display_text,
                    "url": url,
                },
            },
        },
    })


def send_location(to: str, latitude: float, longitude: float, name: str, address: str):
    return post_whatsapp({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "location",
        "location": {
            "latitude": latitude,
            "longitude": longitude,
            "name": name,
            "address": address
        }
    })

def send_document(to: str, file_url: str, filename: str, caption: str = ""):
    return post_whatsapp({
        "messaging_product": "whatsapp",
        "to": to,
        "type": "document",
        "document": {
            "link": file_url,
            "filename": filename,
            "caption": caption
        }
    })