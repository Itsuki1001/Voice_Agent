import os
import time
import uuid
import requests
import dateparser
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv
from ics import Calendar
from langchain_core.tools import tool
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings

load_dotenv()

# ─────────────────────────────────────────────
# CALENDAR
# ─────────────────────────────────────────────
EMBED_MODEL = "text-embedding-3-small"
ICS_REFRESH_INTERVAL = 5 * 60

_calendar = None
_calendar_last_loaded = 0


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
        except Exception as e:
            print(f"⚠️ Calendar refresh failed: {e}")
    return _calendar


# ─────────────────────────────────────────────
# ROOMS
# ─────────────────────────────────────────────
class RoomType(Enum):
    DELUXE = "Deluxe Room"
    HONEYMOON = "Honeymoon Suite"
    FAMILY = "Family Villa"


@dataclass
class Room:
    room_id: str
    room_type: RoomType
    capacity: int
    base_rate: float
    description: str
    amenities: List[str]


ROOMS = [
    Room("DLX-01", RoomType.DELUXE,    2, 4500, "Cozy deluxe room with garden view",
         ["King Bed", "AC", "WiFi", "Smart TV", "Mini Bar", "Balcony"]),
    Room("HMS-01", RoomType.HONEYMOON, 2, 7500, "Romantic suite with private jacuzzi and ocean view",
         ["King Bed", "AC", "WiFi", "Smart TV", "Jacuzzi", "Ocean View", "Champagne on Arrival", "Rose Petals"]),
    Room("FVM-01", RoomType.FAMILY,    4, 9500, "Spacious villa perfect for families",
         ["2 Bedrooms", "AC", "WiFi", "Smart TV", "Kitchen", "Living Room", "Garden", "BBQ Area"]),
]

# room_id → {expires_at, guest_phone, booking_ref}
_temporary_holds: Dict[str, Dict] = {}


# ─────────────────────────────────────────────
# AVAILABILITY HELPERS
# ─────────────────────────────────────────────
def _is_available(room_id: str, start: datetime, end: datetime) -> bool:
    """Return True if room has no hold and no calendar conflict."""
    hold = _temporary_holds.get(room_id)
    if hold and hold["expires_at"] > datetime.now(timezone.utc):
        return False
    elif hold:
        del _temporary_holds[room_id]

    cal = get_calendar()
    if cal:
        for event in cal.events:
            b = event.begin.datetime
            e = event.end.datetime
            if b.tzinfo is None:
                b = b.replace(tzinfo=timezone.utc)
            if e.tzinfo is None:
                e = e.replace(tzinfo=timezone.utc)
            if not (end <= b or start >= e):
                return False
    return True


def _parse_dates(check_in: str, check_out: str):
    """Return (start_dt, end_dt) as UTC datetimes, or raise ValueError."""
    s = dateparser.parse(check_in,  settings={"PREFER_DATES_FROM": "future"})
    e = dateparser.parse(check_out, settings={"PREFER_DATES_FROM": "future"})
    if not s or not e:
        raise ValueError("Could not parse dates")
    s = s.replace(tzinfo=timezone.utc)
    e = e.replace(tzinfo=timezone.utc)
    if s >= e:
        raise ValueError("check_out must be after check_in")
    if s.date() < datetime.now(timezone.utc).date():
        raise ValueError("check_in cannot be in the past")
    return s, e


# ─────────────────────────────────────────────
# PRICING HELPERS
# ─────────────────────────────────────────────
def _discounts(start: datetime, end: datetime, base_total: float) -> Tuple[float, List[dict]]:
    nights = (end - start).days
    advance = (start - datetime.now(timezone.utc)).days
    items = []

    if start.weekday() < 4:
        items.append({"label": "Weekday Special", "pct": 15})
    if nights >= 7:
        items.append({"label": "Week-Long Stay",  "pct": 20})
    elif nights >= 3:
        items.append({"label": "Multi-Night Stay", "pct": 10})
    if advance >= 30:
        items.append({"label": "Early Bird",       "pct": 10})
    if start.month in [6, 7, 8, 9]:
        items.append({"label": "Monsoon Special",  "pct": 25})

    total_pct = sum(i["pct"] for i in items)
    amount = base_total * total_pct / 100
    return amount, items


def _pricing(room: Room, start: datetime, end: datetime) -> dict:
    nights = (end - start).days
    base_total = room.base_rate * nights
    disc_amt, disc_items = _discounts(start, end, base_total)
    subtotal = base_total - disc_amt
    gst = subtotal * 0.12
    total = subtotal + gst
    return {
        "nights": nights,
        "base_rate": room.base_rate,
        "base_total": round(base_total),
        "discount_amount": round(disc_amt),
        "discounts": disc_items,           # [{label, pct}, ...]
        "gst": round(gst),
        "total": round(total),
        "advance_50": round(total * 0.5),
    }


# ─────────────────────────────────────────────
# RAG
# ─────────────────────────────────────────────
def _setup_rag():
    embeddings = OpenAIEmbeddings(model=EMBED_MODEL, api_key=os.getenv("OPENAI_API_KEY"))
    vectorstore = FAISS.load_local(
        str(Path(__file__).parent.parent / "index"),
        embeddings,
        allow_dangerous_deserialization=True,
    )
    return vectorstore.as_retriever(search_kwargs={"k": 3})


retriever = _setup_rag()


@tool
def rag_tool(query: str) -> str:
    """Retrieve factual information about the homestay (location, policies, facilities, etc.)."""
    try:
        docs = retriever.invoke(query)
        if not docs:
            return ""
        grouped = defaultdict(list)
        for doc in docs:
            grouped[doc.metadata.get("source", "info")].append(doc.page_content)
        return "\n\n".join(
            f"[{src}]\n" + "\n".join(chunks) for src, chunks in grouped.items()
        )
    except Exception as e:
        return f"RAG_ERROR::{e}"


# ─────────────────────────────────────────────
# DISTANCE
# ─────────────────────────────────────────────
def _coords(place: str):
    res = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params={"q": place, "format": "json", "limit": 1},
        headers={"User-Agent": "HomestayBot/1.0"},
        timeout=5,
    ).json()
    return (float(res[0]["lat"]), float(res[0]["lon"])) if res else (None, None)


@tool
def get_distance_to_homestay(origin: str, mode: str = "driving") -> dict:
    """
    Return driving/walking distance and estimated travel time from a given origin.

    Returns a dict: {origin, destination, distance_km, duration_hours, mode}
    or {error: "..."} on failure.
    """
    try:
        dest_name = "Nedumbassery"
        oc = _coords(origin)
        dc = _coords(dest_name)
        if not all(oc) or not all(dc):
            return {"error": "Could not resolve coordinates"}

        res = requests.get(
            f"https://router.project-osrm.org/route/v1/{mode}/{oc[1]},{oc[0]};{dc[1]},{dc[0]}?overview=false",
            timeout=5,
        ).json()
        route = res["routes"][0]
        return {
            "origin": origin,
            "destination": dest_name,
            "distance_km": round(route["distance"] / 1000, 1),
            "duration_hours": round(route["duration"] / 3600, 1),
            "mode": mode,
        }
    except Exception as e:
        return {"error": str(e)}


# ─────────────────────────────────────────────
# AVAILABILITY CHECK
# ─────────────────────────────────────────────
@tool
def check_availability_and_prices(check_in: str, check_out: str, guests: int = 2) -> dict:
    """
    Check which rooms are available for the given dates and return pricing.

    Returns:
      On success:  {check_in, check_out, nights, available: [{room_id, room_type,
                    capacity, amenities, pricing: {nights, base_rate, base_total,
                    discount_amount, discounts, gst, total, advance_50}}]}
      On no rooms: {check_in, check_out, nights, available: [],
                    next_available: [{check_in, check_out}]}  ← up to 3 alternatives
      On error:    {error: "..."}
    """
    try:
        start, end = _parse_dates(check_in, check_out)
    except ValueError as e:
        return {"error": str(e)}

    nights = (end - start).days
    ci = start.strftime("%Y-%m-%d")
    co = end.strftime("%Y-%m-%d")

    available = []
    for room in ROOMS:
        if room.capacity >= guests and _is_available(room.room_id, start, end):
            available.append({
                "room_id":   room.room_id,
                "room_type": room.room_type.value,
                "capacity":  room.capacity,
                "amenities": room.amenities,
                "pricing":   _pricing(room, start, end),
            })

    result = {"check_in": ci, "check_out": co, "nights": nights, "available": available}

    if not available:
        # Suggest up to 3 nearest alternative date ranges
        alts = []
        for offset in range(1, 61):
            alt_start = start + timedelta(days=offset)
            alt_end = alt_start + timedelta(days=nights)
            for room in ROOMS:
                if room.capacity >= guests and _is_available(room.room_id, alt_start, alt_end):
                    alts.append({
                        "check_in":  alt_start.strftime("%Y-%m-%d"),
                        "check_out": alt_end.strftime("%Y-%m-%d"),
                    })
                    break
            if len(alts) >= 3:
                break
        result["next_available"] = alts

    return result


# ─────────────────────────────────────────────
# NEXT AVAILABLE DATES
# ─────────────────────────────────────────────
@tool
def find_next_available_dates(duration_nights: int = 1, guests: int = 2, results: int = 5) -> dict:
    """
    Find the next available date windows for a stay of given length.

    Returns:
      {duration_nights, guests, slots: [{check_in, check_out, room_ids: [...]}]}
      or {error: "..."}
    """
    today = datetime.now(timezone.utc)
    slots = []

    for offset in range(90):
        start = today + timedelta(days=offset)
        end = start + timedelta(days=duration_nights)
        room_ids = [
            r.room_id for r in ROOMS
            if r.capacity >= guests and _is_available(r.room_id, start, end)
        ]
        if room_ids:
            slots.append({
                "check_in":  start.strftime("%Y-%m-%d"),
                "check_out": end.strftime("%Y-%m-%d"),
                "room_ids":  room_ids,
            })
        if len(slots) >= results:
            break

    if not slots:
        return {"error": f"No availability found in next 90 days for {duration_nights} nights"}

    return {"duration_nights": duration_nights, "guests": guests, "slots": slots}


# ─────────────────────────────────────────────
# HOLD + PAYMENT LINK
# ─────────────────────────────────────────────
@tool
def hold_room_and_generate_payment(
    room_type: str,
    check_in: str,
    check_out: str,
    guest_name: str,
    guest_phone: str,
    guests: int = 2,
) -> dict:
    """
    Temporarily hold a room (10 min) and generate a payment link.

    Returns:
      {booking_ref, room_type, room_id, check_in, check_out, guests,
       pricing: {...}, hold_expires_at, payment_link, whatsapp_sent}
      or {error: "..."}
    """
    # Find room
    room = next((r for r in ROOMS if r.room_type.value.lower() == room_type.lower()), None)
    if not room:
        valid = [r.room_type.value for r in ROOMS]
        return {"error": f"Unknown room type. Valid options: {valid}"}

    try:
        start, end = _parse_dates(check_in, check_out)
    except ValueError as e:
        return {"error": str(e)}

    if not _is_available(room.room_id, start, end):
        return {"error": f"{room_type} is not available for those dates"}

    pricing = _pricing(room, start, end)
    booking_ref = f"BK{uuid.uuid4().hex[:8].upper()}"
    hold_expires = datetime.now(timezone.utc) + timedelta(minutes=10)

    _temporary_holds[room.room_id] = {
        "expires_at":  hold_expires,
        "guest_phone": guest_phone,
        "booking_ref": booking_ref,
    }

    payment_link = f"https://pay.resort-demo.com/{booking_ref}"

    # ── WhatsApp notification (mock — replace with Twilio in prod) ──────────
    wa_body = (
        f"Hi {guest_name}! Booking ref *{booking_ref}*. "
        f"{room.room_type.value} | {start.strftime('%b %d')}–{end.strftime('%b %d, %Y')} | "
        f"₹{pricing['advance_50']:,} due now. Pay: {payment_link} "
        f"(link valid 10 min)"
    )
    print(f"📱 [MOCK WhatsApp → {guest_phone}] {wa_body}")
    # ────────────────────────────────────────────────────────────────────────

    return {
        "booking_ref":     booking_ref,
        "room_type":       room.room_type.value,
        "room_id":         room.room_id,
        "check_in":        start.strftime("%Y-%m-%d"),
        "check_out":       end.strftime("%Y-%m-%d"),
        "guests":          guests,
        "pricing":         pricing,
        "hold_expires_at": hold_expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "payment_link":    payment_link,
        "whatsapp_sent":   True,
    }


# ─────────────────────────────────────────────
# ROOM DETAILS
# ─────────────────────────────────────────────
@tool
def get_room_details(room_type: str) -> dict:
    """
    Return static details for a specific room type.

    Returns:
      {room_id, room_type, capacity, base_rate, description, amenities}
      or {error: "..."}
    """
    room = next((r for r in ROOMS if r.room_type.value.lower() == room_type.lower()), None)
    if not room:
        return {"error": f"Unknown room type. Options: {[r.room_type.value for r in ROOMS]}"}
    return {
        "room_id":     room.room_id,
        "room_type":   room.room_type.value,
        "capacity":    room.capacity,
        "base_rate":   room.base_rate,
        "description": room.description,
        "amenities":   room.amenities,
    }


# ─────────────────────────────────────────────
# MAINTENANCE
# ─────────────────────────────────────────────
def cleanup_expired_holds():
    now = datetime.now(timezone.utc)
    expired = [rid for rid, h in _temporary_holds.items() if h["expires_at"] <= now]
    for rid in expired:
        del _temporary_holds[rid]


# ─────────────────────────────────────────────
# EXPORTS
# ─────────────────────────────────────────────
tools = [
    rag_tool,
    get_distance_to_homestay,
    check_availability_and_prices,
    find_next_available_dates,
    hold_room_and_generate_payment,
    get_room_details,
]