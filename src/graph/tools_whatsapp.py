from ast import List
import os
import time
import requests
import dateparser
from collections import defaultdict
from datetime import timezone

from dotenv import load_dotenv
from ics import Calendar
from pathlib import Path

from langchain_core.tools import tool
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS


from whatsapp.client import send_cta_button, send_image, send_location, send_text,send_document
from langchain_core.runnables import RunnableConfig


from typing import List

# ==================================================
# ENV
# ==================================================

load_dotenv()

# ==================================================
# ICS CALENDAR (AUTO REFRESH WITH TTL)
# ==================================================
EMBED_MODEL="text-embedding-3-small"
ICS_REFRESH_INTERVAL = 5 * 60  # 5 minutes

_calendar = None
_calendar_last_loaded = 0


def get_calendar() -> Calendar | None:
    """
    Returns a cached calendar.
    Automatically refreshes from ICS every
    ICS_REFRESH_INTERVAL seconds.
    """
    global _calendar, _calendar_last_loaded

    now = time.time()

    if _calendar is None or (now - _calendar_last_loaded) > ICS_REFRESH_INTERVAL:
        ics_url = os.getenv("ics_url")
        try:
            response = requests.get(ics_url, timeout=5)
            response.raise_for_status()

            _calendar = Calendar(response.text)
            _calendar_last_loaded = now
            print("📅 Calendar refreshed")

        except Exception as e:
            print(f"⚠️ Calendar refresh failed: {e}")
            # Keep old calendar if available

    return _calendar


# ==================================================
# RAG (FAISS) — LOAD ONCE
# ==================================================

def setup_rag():
    embeddings = OpenAIEmbeddings(model=EMBED_MODEL, api_key=os.getenv("OPENAI_API_KEY"))

    vectorstore = FAISS.load_local(
        str(Path(__file__).parent.parent / "index"),
        embeddings,
        allow_dangerous_deserialization=True,
    )

    return vectorstore.as_retriever(search_kwargs={"k": 3})


retriever = setup_rag()


def _format_rag_docs(docs: list) -> str:
    grouped = defaultdict(list)
    for doc in docs:
        source = doc.metadata.get("source", "unknown")
        grouped[source].append(doc.page_content)
    return "\n\n".join(
        f"{src}:\n" + "\n".join(chunks)
        for src, chunks in grouped.items()
    )


@tool
def rag_tool(query: str) -> str:
    """Retrieve information about the homestay."""
    try:
        docs = retriever.invoke(query)
        if not docs:
            return ""
        return _format_rag_docs(docs)
    except Exception as e:
        return f"RAG_ERROR::{e}"


# ==================================================
# DISTANCE & TRAVEL TIME
# ==================================================

def _get_coords(place: str):
    url = "https://nominatim.openstreetmap.org/search"
    params = {"q": place, "format": "json", "limit": 1}

    res = requests.get(
        url,
        params=params,
        headers={"User-Agent": "TherapyCenterBot/1.0"},
        timeout=5,
    ).json()

    if res:
        return float(res[0]["lat"]), float(res[0]["lon"])

    return None, None


def _get_distance_time(origin, dest, mode="driving"):
    url = (
        f"https://router.project-osrm.org/route/v1/"
        f"{mode}/{origin[1]},{origin[0]};{dest[1]},{dest[0]}"
        f"?overview=false"
    )

    res = requests.get(url, timeout=5).json()
    route = res["routes"][0]

    return route["distance"] / 1000, route["duration"] / 3600


@tool("get_distance_to_homestay")
def get_distance_to_homestay(origin: str, mode: str = "driving") -> str:
    """Get distance and travel time to the homestay."""
    try:
        destination = "Nedumbassery"

        origin_coords = _get_coords(origin)
        dest_coords = _get_coords(destination)

        if not all(origin_coords) or not all(dest_coords):
            return "Could not determine location coordinates."

        dist, time_hrs = _get_distance_time(origin_coords, dest_coords, mode)

        return (
            f"From {origin} to our homestay: "
            f"{dist:.1f} km ({time_hrs:.1f} hours by {mode})."
        )

    except Exception as e:
        return f"Distance error: {e}"


# ==================================================
# ROOM AVAILABILITY (ICS)
# ==================================================

@tool
def get_room_availability(start_time, end_time) -> str:
    """Check if the homestay is available between given dates."""
    try:
        if isinstance(start_time, str):
            start_time = dateparser.parse(start_time)
        if isinstance(end_time, str):
            end_time = dateparser.parse(end_time)

        if not start_time or not end_time:
            return "Invalid date format."

        start_time = start_time.replace(tzinfo=timezone.utc)
        end_time = end_time.replace(tzinfo=timezone.utc)

        calendar = get_calendar()
        if not calendar:
            return "Availability system temporarily unavailable."

        for event in calendar.events:
            if not (end_time <= event.begin or start_time >= event.end):
                return "No"

        return "Yes"

    except Exception as e:
        return f"Availability error: {e}"




# ==================================================
# PHOTOS
# ==================================================

@tool("get_room_photos")
def get_room_photos(rooms: List[str], config: RunnableConfig) -> dict:
    """Send room photos directly via WhatsApp."""

    sender_id = config["configurable"].get("thread_id")
    if not sender_id:
        return {"status": "error", "reason": "no_user_id"}

    photos = {
        "traditional room": [
            "https://a0.muscache.com/im/pictures/hosting/Hosting-32300689/original/e36390aa-9d2c-48fc-841f-dea45b058dd1.jpeg",
            "https://a0.muscache.com/im/pictures/defbc8a3-c61d-467d-b3e0-405b6fd9b6e6.jpg",
        ],
        "traditional room with kitchen": [
            "https://a0.muscache.com/im/pictures/hosting/Hosting-32300689/original/a61dc472-2f46-45b7-984f-234451e35039.jpeg",
            "https://a0.muscache.com/im/pictures/ac741fd1-9976-4316-9f86-fcc4f9ef36ac.jpg",
        ],
        "kitchen": [
            "https://a0.muscache.com/im/pictures/hosting/Hosting-32300689/original/98bfca78-5467-4266-9c8d-820fa61889bb.jpeg",
        ],
        "courtyard": [
            "https://a0.muscache.com/im/pictures/hosting/Hosting-U3RheVN1cHBseUxpc3Rpbmc6MzIyOTQwNjI=/original/35bc27f1-1d6d-41b6-b4aa-922fb6a7727b.jpeg",
        ],
        "livingroom": [
            "https://a0.muscache.com/im/pictures/hosting/Hosting-32300689/original/9c9bb4eb-9b73-4b26-93a4-efd3634a2ece.jpeg",
        ],
    }

    # Normalize input rooms
    keys = [r.lower().strip() for r in rooms]

    sent = 0
    invalid = []

    generic_keys = {"rooms", "room", "bedrooms", "bedroom"}

    if any(k in generic_keys for k in keys):
        keys = ["traditional room", "traditional room with kitchen"]

        


    for room_key in keys:
        if room_key not in photos:
            invalid.append(room_key)
            continue

        for idx, img in enumerate(photos[room_key]):
            caption = room_key.title() 
            send_image(sender_id, img, caption)
            sent += 1

    if sent == 0:
        return {"status": "error", "reason": "no_valid_rooms", "invalid_rooms": invalid}

    return {
        "status": "ok",
        "rooms": keys,
        "images_sent": sent,
        "invalid_rooms": invalid
    }


# ==================================================
# AIRBNB BOOKING LINK
# ==================================================

@tool("get_airbnb_booking_link")
def get_airbnb_booking_link(
    checkin: str,
    checkout: str,
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    pets: int = 0,
    currency: str = "INR",
    config: RunnableConfig = None,
) -> dict:
    """Generate Airbnb booking link and send it as a WhatsApp CTA button."""

    try:
        sender_id = config["configurable"].get("thread_id") if config else None
        if not sender_id:
            return {"status": "error", "reason": "no_user_id"}

        product_id = "32300689"
        base_url = "https://www.airbnb.co.in/book/stays/32300689"

        total_guests = adults + children + infants

        url = (
            f"{base_url}?checkin={checkin}"
            f"&checkout={checkout}"
            f"&numberOfGuests={total_guests}"
            f"&numberOfAdults={adults}"
            f"&numberOfChildren={children}"
            f"&numberOfInfants={infants}"
            f"&numberOfPets={pets}"
            f"&guestCurrency={currency}"
            f"&productId={product_id}"
            f"&isWorkTrip=false"
        )

        # 🔥 Send CTA button directly on WhatsApp
        send_cta_button(
            to=sender_id,
            text=f"Your stay from {checkin} to {checkout} is available. Tap below to book:",
            url=url,
            display_text="Book Now"
        )

        return {
            "status": "ok",
            "checkin": checkin,
            "checkout": checkout,
            "guests": total_guests,
        }

    except Exception as e:
        return {"status": "error", "reason": str(e)}
    
@tool("send_maps_link")
def send_maps_link(config: RunnableConfig) -> dict:
    """Send Pete's Inn Homestay as a native WhatsApp location card (precise pin)."""

    sender_id = config["configurable"].get("thread_id")
    if not sender_id:
        return {"status": "error", "reason": "no_user_id"}

    # Exact coordinates from your Google Maps link
    LAT = 10.1674924
    LON = 76.3804722

    NAME = "Pete's Inn Homestay"
    ADDRESS = "Akaparambu - Vappalassery Rd, Nedumbassery, Kerala 683572"

    send_location(
        to=sender_id,
        latitude=LAT,
        longitude=LON,
        name=NAME,
        address=ADDRESS
    )

    return {"status": "ok"}




@tool("send_invoice_pdf")
def send_invoice_pdf(config: RunnableConfig) -> dict:
    """Send the guest's invoice as a WhatsApp document (PDF)."""

    sender_id = config["configurable"].get("thread_id")
    if not sender_id:
        return {"status": "error", "reason": "no_user_id"}

    # Example: your generated or hosted invoice PDF
    FILE_URL = "https://www.dropbox.com/scl/fi/oddb4gjxjcdmhqv5ouc8t/OS-M4-Ktunotes.in.pdf?rlkey=44m7dhbn9qeyxhwc9ws0wnfbs&st=xj5yinwx&dl=1"
    FILENAME = "PetesInn_Invoice_1234.pdf"
    CAPTION = "Here’s your invoice from Pete’s Inn 📄"

    send_document(
        to=sender_id,
        file_url=FILE_URL,
        filename=FILENAME,
        caption=CAPTION
    )

    return {"status": "ok"}

@tool("send_review_link")
def send_review_link(config: RunnableConfig) -> dict:
    """Send a review link to the guest."""

    sender_id = config["configurable"].get("thread_id")
    if not sender_id:
        return {"status": "error", "reason": "no_user_id"}

    REVIEW_URL = "https://www.google.com/maps/place/Pete's+Inn+Homestay/@10.1674977,76.3778973,17z/data=!4m11!3m10!1s0x3b08079f023f1189:0x5db042313f5a17b!5m2!4m1!1i2!8m2!3d10.1674924!4d76.3804722!9m1!1b1!16s%2Fg%2F11h0mwylr1?entry=ttu&g_ep=EgoyMDI2MDIyMi4wIKXMDSoASAFQAw%3D%3D"

    send_cta_button(
        to=sender_id,
        text=(
            "Hi! 😊 We hope you enjoyed your stay at Pete’s Inn Homestay.\n"
            "Your review would mean a lot to us and helps other travelers find us.\n"
            "Please tap the button below to leave a review.\n\n"
            "If you have any feedback or concerns, feel free to reply here — "
            "I’ll personally make sure it’s addressed."
        ),
        url=REVIEW_URL,
        display_text="Leave Review"
    )

    return {"status": "ok"}


# ==================================================
# TOOL REGISTRY
# ==================================================

tools = [
    rag_tool,
    get_distance_to_homestay,
    get_room_availability,
    get_room_photos,
    get_airbnb_booking_link,
    send_maps_link,
    send_invoice_pdf,
    send_review_link
]
