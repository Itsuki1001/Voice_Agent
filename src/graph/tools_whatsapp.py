from typing import List
import os
import time
import uuid
import requests
import dateparser
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

from dotenv import load_dotenv
from ics import Calendar

from langchain_core.tools import tool
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS

from whatsapp.client import send_cta_button, send_image, send_location, send_text, send_document
from langchain_core.runnables import RunnableConfig

from typing import List, Dict, Tuple

load_dotenv()

# ==================================================
# ROOM CATALOGUE
# ==================================================

ROOMS: Dict[str, Dict] = {
    "deluxe_garden": {
        "name":        "Deluxe Garden Room",
        "base_rate":   4500,
        "description": "AC, king bed, balcony, garden view, mini bar, WiFi, smart TV",
    },
    "honeymoon_suite": {
        "name":        "Honeymoon Ocean Suite",
        "base_rate":   8500,
        "description": "AC, king bed, private jacuzzi, ocean view balcony, WiFi, smart TV, champagne on arrival",
    },
    "family_villa": {
        "name":        "Family Villa",
        "base_rate":   12000,
        "description": "AC, 2 bedrooms, living room, kitchen, private garden, BBQ area, WiFi, smart TV",
    },
}


# ==================================================
# ICS CALENDAR  (auto-refresh every 5 min)
# ==================================================

EMBED_MODEL          = "text-embedding-3-small"
ICS_REFRESH_INTERVAL = 5 * 60

_calendar: Calendar | None = None
_calendar_last_loaded: float = 0
from datetime import datetime, timezone

_bookings: Dict[str, List[Tuple[datetime, datetime]]] = {
    "deluxe_garden": [
        # Short stay
        (
            datetime(2026, 4, 18, tzinfo=timezone.utc),
            datetime(2026, 4, 20, tzinfo=timezone.utc),
        ),
        # Weekend block
        (
            datetime(2026, 4, 25, tzinfo=timezone.utc),
            datetime(2026, 4, 27, tzinfo=timezone.utc),
        ),
    ],

    "honeymoon_suite": [
        # Longer romantic stay
        (
            datetime(2026, 4, 19, tzinfo=timezone.utc),
            datetime(2026, 4, 23, tzinfo=timezone.utc),
        ),
        # Future booking
        (
            datetime(2026, 5, 1, tzinfo=timezone.utc),
            datetime(2026, 5, 5, tzinfo=timezone.utc),
        ),
    ],

    "family_villa": [
        # Family vacation block
        (
            datetime(2026, 4, 17, tzinfo=timezone.utc),
            datetime(2026, 4, 21, tzinfo=timezone.utc),
        ),
        # Another stay
        (
            datetime(2026, 4, 28, tzinfo=timezone.utc),
            datetime(2026, 5, 2, tzinfo=timezone.utc),
        ),
    ],
}


def get_calendar() -> Calendar | None:
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
    return _calendar


# ==================================================
# TEMPORARY HOLDS  (10-min room lock)
# ==================================================

_temporary_holds: Dict[str, Dict] = {}


def _clear_expired_hold() -> None:
    hold = _temporary_holds.get("room")
    if hold and hold["expires_at"] <= datetime.now(timezone.utc):
        del _temporary_holds["room"]


def _is_room_free(room_key: str, start: datetime, end: datetime) -> bool:
    bookings = _bookings.get(room_key, [])

    for b_start, b_end in bookings:
        if not (end <= b_start or start >= b_end):
            return False
    return True


def _is_available(room_key: str, start: datetime, end: datetime, sender_id: str | None = None) -> bool:
    _clear_expired_hold()

    hold = _temporary_holds.get("room")
    if hold:
        if sender_id and hold.get("guest_phone") == sender_id:
            return True
        return False

    return _is_room_free(room_key, start, end)


# ==================================================
# PRICING
# ==================================================

@tool
def get_available_rooms(checkin: str, checkout: str) -> dict:
    """
    Return all available rooms for a date range with pricing.
    Use this when the user asks general availability without specifying a room.
    """
    start = dateparser.parse(checkin, settings={"PREFER_DATES_FROM": "future"})
    end   = dateparser.parse(checkout, settings={"PREFER_DATES_FROM": "future"})

    if not start or not end:
        return {"error": "Invalid date format"}
    if start >= end:
        return {"error": "Check-out must be after check-in"}

    start = start.replace(tzinfo=timezone.utc)
    end   = end.replace(tzinfo=timezone.utc)

    results = []

    for room_key, room in ROOMS.items():
        if _is_room_free(room_key, start, end):
            pricing = _build_pricing(start, end, room_key)
            results.append({
                "room_key": room_key,
                "name": room["name"],
                "price": pricing["total"],
                "nights": pricing["nights"],
            })

    return {
        "check_in": start.strftime("%Y-%m-%d"),
        "check_out": end.strftime("%Y-%m-%d"),
        "available_rooms": results
    }

@tool("get_room_rate")
def get_room_rate(room_key: str, checkin: str, checkout: str) -> dict:
    """
    Return the nightly rate and full pricing breakdown for a specific room and date range.
    room_key must be one of: deluxe_garden, honeymoon_suite, family_villa.
    Call this whenever a guest asks about the cost of a specific room.
    """
    if room_key not in ROOMS:
        return {"error": f"Unknown room '{room_key}'. Choose from: {list(ROOMS)}"}

    start = dateparser.parse(checkin,  settings={"PREFER_DATES_FROM": "future"})
    end   = dateparser.parse(checkout, settings={"PREFER_DATES_FROM": "future"})

    if not start or not end:
        return {"error": "Invalid date format"}
    if start >= end:
        return {"error": "Check-out must be after check-in"}

    start = start.replace(tzinfo=timezone.utc)
    end   = end.replace(tzinfo=timezone.utc)

    return {
        "room":        ROOMS[room_key]["name"],
        "description": ROOMS[room_key]["description"],
        **_build_pricing(start, end, room_key),
    }


def _calculate_discounts(start: datetime, end: datetime, base_total: float) -> Tuple[float, List[dict]]:
    nights       = (end - start).days
    advance_days = (start - datetime.now(timezone.utc)).days
    items        = []

    if start.weekday() < 4:
        items.append({"label": "Weekday Special",  "pct": 15})
    if nights >= 7:
        items.append({"label": "Week-Long Stay",   "pct": 20})
    elif nights >= 3:
        items.append({"label": "Multi-Night Stay", "pct": 10})
    if advance_days >= 30:
        items.append({"label": "Early Bird",       "pct": 10})
    if start.month in [6, 7, 8, 9]:
        items.append({"label": "Monsoon Special",  "pct": 25})

    total_pct       = sum(i["pct"] for i in items)
    discount_amount = base_total * total_pct / 100
    return discount_amount, items


def _build_pricing(start: datetime, end: datetime, room_key: str) -> dict:
    """Internal pricing calc — always reads the base rate via get_room_rate's logic."""
    rate       = ROOMS[room_key]["base_rate"]   # single source of truth; swap for DB/API call here later
    nights     = (end - start).days
    base_total = rate * nights
    disc_amount, disc_items = _calculate_discounts(start, end, base_total)
    subtotal   = base_total - disc_amount
    gst        = subtotal * 0.12
    total      = subtotal + gst

    return {
        "nights":          nights,
        "base_rate":       rate,
        "base_total":      round(base_total),
        "discount_amount": round(disc_amount),
        "discounts":       disc_items,
        "gst":             round(gst),
        "total":           round(total),
        "advance_50":      round(total * 0.5),
    }


def _format_pricing_block(pricing: dict, checkin: str, checkout: str) -> str:
    lines = [
        f"📅 *{checkin}  →  {checkout}*  ({pricing['nights']} night{'s' if pricing['nights'] > 1 else ''})",
        "",
        f"  Base rate:  ₹{pricing['base_rate']:,} × {pricing['nights']} = ₹{pricing['base_total']:,}",
    ]
    if pricing["discounts"]:
        labels = ", ".join(f"{d['label']} ({d['pct']}%)" for d in pricing["discounts"])
        lines += [
            f"  🏷️ Discounts:  {labels}",
            f"  Discount total:  -₹{pricing['discount_amount']:,}",
        ]
    lines += [
        f"  GST (12%):  ₹{pricing['gst']:,}",
        f"  ─────────────────────",
        f"  💰 *Total:  ₹{pricing['total']:,}*",
        f"  50% advance due:  ₹{pricing['advance_50']:,}",
    ]
    return "\n".join(lines)


# ==================================================
# RAG (FAISS)
# ==================================================

def setup_rag():
    embeddings  = OpenAIEmbeddings(model=EMBED_MODEL, api_key=os.getenv("OPENAI_API_KEY"))
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
        grouped[doc.metadata.get("source", "unknown")].append(doc.page_content)
    return "\n\n".join(f"{src}:\n" + "\n".join(chunks) for src, chunks in grouped.items())


@tool
def rag_tool(query: str) -> str:
    """Retrieve general information about the resort."""
    try:
        docs = retriever.invoke(query)
        return _format_rag_docs(docs) if docs else ""
    except Exception as e:
        return f"RAG_ERROR::{e}"


# ==================================================
# TOOL: ROOM AVAILABILITY
# ==================================================

@tool
def get_room_availability(
    start_time,
    end_time,
    room_key: str,
    config: RunnableConfig,
) -> dict:
    """
    Check if a specific room is available for the given dates and return pricing.
    room_key must be one of: deluxe_garden, honeymoon_suite, family_villa.
    Does NOT send any WhatsApp message on success — call send_payment_link next.
    Sends a 'fully booked' message automatically when unavailable.
    """
    try:
        if isinstance(start_time, str):
            start_time = dateparser.parse(start_time, settings={"PREFER_DATES_FROM": "future"})
        if isinstance(end_time, str):
            end_time   = dateparser.parse(end_time,   settings={"PREFER_DATES_FROM": "future"})

        if not start_time or not end_time:
            return {"available": False, "error": "Invalid date format"}
        if start_time >= end_time:
            return {"available": False, "error": "Check-out must be after check-in"}
        if room_key not in ROOMS:
            return {"available": False, "error": f"Unknown room '{room_key}'. Choose from: {list(ROOMS)}"}

        start_time = start_time.replace(tzinfo=timezone.utc)
        end_time   = end_time.replace(tzinfo=timezone.utc)
        sender_id  = config["configurable"].get("thread_id")

        if not _is_available(start_time, end_time, sender_id):
            if sender_id:
                send_text(
                    sender_id,
                    f"❌ Sorry, *{ROOMS[room_key]['name']}* is fully booked "
                    f"from *{start_time.strftime('%Y-%m-%d')}* to *{end_time.strftime('%Y-%m-%d')}*.\n"
                    "Please try different dates — I'm happy to check! 😊"
                )
            return {"available": False}

        return {
            "available": True,
            "room":      ROOMS[room_key]["name"],
            "check_in":  start_time.strftime("%Y-%m-%d"),
            "check_out": end_time.strftime("%Y-%m-%d"),
            "pricing":   _build_pricing(start_time, end_time, room_key),
        }

    except Exception as e:
        return {"available": False, "error": str(e)}


# ==================================================
# TOOL: ROOM PHOTOS
# ==================================================

ROOM_PHOTOS: Dict[str, list] = {
    "deluxe_garden": [
        "https://res.cloudinary.com/dieyzbtql/image/upload/v1776418990/1_vdiyae.png",
        "https://res.cloudinary.com/dieyzbtql/image/upload/v1776419087/2_jzuibs.png",
        "https://res.cloudinary.com/dieyzbtql/image/upload/v1776419088/3_yn3pdo.png",
    ],
    "honeymoon_suite": [
        "https://res.cloudinary.com/dieyzbtql/image/upload/v1776419133/1_wruawk.png",
        "https://res.cloudinary.com/dieyzbtql/image/upload/v1776419133/2_arp0rl.png",
        "https://res.cloudinary.com/dieyzbtql/image/upload/v1776419135/3_bqer51.png",
    ],
    "family_villa": [
        "https://res.cloudinary.com/dieyzbtql/image/upload/v1776419216/1_q6zhhx.jpg",
    ],
}


@tool("get_room_photos")
def get_room_photos(rooms: List[str], config: RunnableConfig) -> dict:
    """Send room photos via WhatsApp. Pass room keys or 'all' to send everything."""
    sender_id = config["configurable"].get("thread_id")
    if not sender_id:
        return {"status": "error", "reason": "no_user_id"}

    # Normalise: "all" or generic words → every room
    keys = [r.lower().strip() for r in rooms]
    if any(k in {"all", "rooms", "room", "everything"} for k in keys):
        keys = list(ROOM_PHOTOS)

    sent, invalid = 0, []
    for key in keys:
        if key not in ROOM_PHOTOS:
            invalid.append(key)
            continue
        for url in ROOM_PHOTOS[key]:
            send_image(sender_id, url, ROOMS[key]["name"])
            sent += 1

    return {"status": "ok" if sent else "error", "images_sent": sent, "invalid_rooms": invalid}


# ==================================================
# TOOL: SEND PAYMENT LINK
# ==================================================

@tool("send_payment_link")
def send_payment_link(
    checkin:     str,
    checkout:    str,
    room_key:    str,
    guest_name:  str,
    guest_phone: str,
    adults:      int = 2,
    config:      RunnableConfig = None,
) -> dict:
    """
    Place a 10-minute hold and send the complete booking summary + Pay Now button.
    room_key must be one of: deluxe_garden, honeymoon_suite, family_villa.
    Handles its own availability check — do NOT call get_room_availability first.
    """
    try:
        sender_id = config["configurable"].get("thread_id") if config else None
        if not sender_id:
            return {"status": "error", "reason": "no_user_id"}
        if room_key not in ROOMS:
            return {"status": "error", "reason": f"Unknown room '{room_key}'"}

        start = dateparser.parse(checkin,  settings={"PREFER_DATES_FROM": "future"})
        end   = dateparser.parse(checkout, settings={"PREFER_DATES_FROM": "future"})
        if not start or not end:
            return {"status": "error", "reason": "Invalid date format"}

        start = start.replace(tzinfo=timezone.utc)
        end   = end.replace(tzinfo=timezone.utc)

        if not _is_available(start, end, sender_id):
            send_text(
                sender_id,
                f"❌ Sorry, *{ROOMS[room_key]['name']}* is no longer available "
                f"for those dates.\nPlease try different dates — I'm happy to help! 😊"
            )
            return {"status": "error", "reason": "dates_unavailable"}

        pricing     = _build_pricing(start, end, room_key)
        booking_ref = f"BK{uuid.uuid4().hex[:8].upper()}"
        expires_at  = datetime.now(timezone.utc) + timedelta(minutes=10)

        _temporary_holds["room"] = {
            "expires_at":  expires_at,
            "guest_phone": guest_phone,
            "booking_ref": booking_ref,
        }

        checkin_fmt  = start.strftime("%d %b %Y")
        checkout_fmt = end.strftime("%d %b %Y")
        payment_url  = f"https://pay.paradisebeachcherai.com/{booking_ref}"

        summary = (
            f"🏖️ *Paradise Beach Resort Cherai*\n"
            f"🛏️ {ROOMS[room_key]['name']}\n"
            f"👤 {guest_name}\n\n"
            + _format_pricing_block(pricing, checkin_fmt, checkout_fmt)
            + f"\n\n🔖 Ref: *{booking_ref}*\n⏳ _Hold expires in 10 mins_"
        )
        send_text(sender_id, summary)
        send_cta_button(
            to=sender_id,
            text=f"Pay ₹{pricing['advance_50']:,} (50% advance) to confirm your stay 🔒",
            url=payment_url,
            display_text="Pay Now",
        )

        return {
            "status":          "ok",
            "booking_ref":     booking_ref,
            "room":            ROOMS[room_key]["name"],
            "check_in":        start.strftime("%Y-%m-%d"),
            "check_out":       end.strftime("%Y-%m-%d"),
            "guests":          adults,
            "pricing":         pricing,
            "hold_expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "payment_url":     payment_url,
        }

    except Exception as e:
        return {"status": "error", "reason": str(e)}


# ==================================================
# TOOL: DISTANCE
# ==================================================

def _get_coords(place: str):
    res = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": place, "format": "json", "limit": 1},
        headers={"User-Agent": "ParadiseBeachResortBot/1.0"},
        timeout=5,
    ).json()
    return (float(res[0]["lat"]), float(res[0]["lon"])) if res else (None, None)


def _get_distance_time(origin, dest, mode="driving"):
    res = requests.get(
        f"https://router.project-osrm.org/route/v1/{mode}/"
        f"{origin[1]},{origin[0]};{dest[1]},{dest[0]}?overview=false",
        timeout=5,
    ).json()
    r = res["routes"][0]
    return r["distance"] / 1000, r["duration"] / 3600


@tool("get_distance_to_homestay")
def get_distance_to_homestay(origin: str, mode: str = "driving") -> str:
    """Get distance and travel time from any location to the resort."""
    try:
        o = _get_coords(origin)
        d = _get_coords("Cherai Beach, Kerala")
        if not all(o) or not all(d):
            return "Could not determine coordinates."
        dist, hrs = _get_distance_time(o, d, mode)
        return f"From {origin} to Paradise Beach Resort Cherai: {dist:.1f} km ({hrs:.1f} hours by {mode})."
    except Exception as e:
        return f"Distance error: {e}"


# ==================================================
# TOOLS: MAPS / INVOICE / REVIEW
# ==================================================

@tool("send_maps_link")
def send_maps_link(config: RunnableConfig) -> dict:
    """Send the resort as a native WhatsApp location card."""
    sender_id = config["configurable"].get("thread_id")
    if not sender_id:
        return {"status": "error", "reason": "no_user_id"}
    send_location(
        to=sender_id,
        latitude=10.1674924, longitude=76.3804722,
        name="Paradise Beach Resort Cherai",
        address="Cherai Beach, Cherai, Kerala 683514",
    )
    return {"status": "ok"}


@tool("send_invoice_pdf")
def send_invoice_pdf(config: RunnableConfig) -> dict:
    """Send the guest's invoice as a WhatsApp PDF document."""
    sender_id = config["configurable"].get("thread_id")
    if not sender_id:
        return {"status": "error", "reason": "no_user_id"}
    send_document(
        to=sender_id,
        file_url="https://www.dropbox.com/scl/fi/oddb4gjxjcdmhqv5ouc8t/OS-M4-Ktunotes.in.pdf?rlkey=44m7dhbn9qeyxhwc9ws0wnfbs&st=xj5yinwx&dl=1",
        filename="ParadiseBeachResort_Invoice.pdf",
        caption="Here's your invoice from Paradise Beach Resort Cherai 📄",
    )
    return {"status": "ok"}


@tool("send_review_link")
def send_review_link(config: RunnableConfig) -> dict:
    """Send a Google review link to the guest."""
    sender_id = config["configurable"].get("thread_id")
    if not sender_id:
        return {"status": "error", "reason": "no_user_id"}
    send_cta_button(
        to=sender_id,
        text=(
            "Hi! 😊 We hope you had a wonderful stay at Paradise Beach Resort Cherai.\n"
            "Your review means the world to us and helps other travellers find us.\n"
            "Tap below to leave a quick review — it only takes a minute!\n\n"
            "Have any feedback? Just reply here and we'll make it right. 🙏"
        ),
        url=(
            "https://www.google.com/maps/place/Pete's+Inn+Homestay/@10.1674977,76.3778973,17z/"
            "data=!4m11!3m10!1s0x3b08079f023f1189:0x5db042313f5a17b!5m2!4m1!1i2!8m2!3d10.1674924!"
            "4d76.3804722!9m1!1b1!16s%2Fg%2F11h0mwylr1?entry=ttu&g_ep=EgoyMDI2MDIyMi4wIKXMDSoASAFQAw%3D%3D"
        ),
        display_text="Leave a Review",
    )
    return {"status": "ok"}


# ==================================================
# TOOL REGISTRY
# ==================================================

tools = [
    rag_tool,
    get_room_rate,
    get_distance_to_homestay,
    get_room_availability,
    get_room_photos,
    send_payment_link,
    send_maps_link,
    send_invoice_pdf,
    send_review_link,
    get_available_rooms
]