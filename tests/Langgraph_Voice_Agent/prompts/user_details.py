from typing import Optional
from collections import OrderedDict
from google.oauth2 import service_account
from googleapiclient.discovery import build
import time


# -------------------------------
# CACHE CONFIG
# -------------------------------

MAX_CACHE_SIZE = 100
EVICT_COUNT = 10
NEGATIVE_CACHE_TTL = 10 * 60  # 10 minutes

# phone -> (timestamp, user_or_None)
_USER_CACHE: OrderedDict[str, tuple[float, Optional[dict]]] = OrderedDict()


# -------------------------------
# GOOGLE SHEETS CONFIG
# -------------------------------

SERVICE_ACCOUNT_FILE = "prompts\\gen-lang-client-0161999752-a028b5d0eea4.json"
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

SPREADSHEET_ID = "1Ehs9KQp9rNOkXPo1CHhUc-JbIj90pMC_XuFAiBzo3zk"
RANGE_NAME = "Sheet1!A:D"

_creds = service_account.Credentials.from_service_account_file(
    SERVICE_ACCOUNT_FILE, scopes=SCOPES
)
_sheets_service = build("sheets", "v4", credentials=_creds)


# -------------------------------
# SHEETS ACCESS
# -------------------------------

def _normalize_phone(p: str) -> str:
    return str(p).strip()


def _get_latest_user_from_sheets(phone: str) -> Optional[dict]:
    phone = _normalize_phone(phone)

    try:
        result = (
            _sheets_service.spreadsheets()
            .values()
            .get(spreadsheetId=SPREADSHEET_ID, range=RANGE_NAME)
            .execute()
        )

        rows = result.get("values", [])

        if not rows:
            return None

        latest_match = None

        for row in rows[1:]:  # skip header
            if len(row) < 4:
                continue

            checkin = row[1]
            checkout = row[2]
            row_phone = _normalize_phone(row[3])

            if row_phone == phone:
                latest_match = {
                    "phone": row_phone,
                    "checkin": checkin,
                    "checkout": checkout,
                }

        return latest_match

    except Exception as e:
        print(f"[SHEETS ERROR] fetching user {phone}: {e}")
        return None


# -------------------------------
# CACHE LOGIC
# -------------------------------

def _evict_if_needed():
    if len(_USER_CACHE) > MAX_CACHE_SIZE:
        for _ in range(EVICT_COUNT):
            if _USER_CACHE:
                _USER_CACHE.popitem(last=False)


def get_user_details(phone: str) -> Optional[dict]:
    """
    - Real users: cached indefinitely (until LRU eviction).
    - None users: cached for 10 minutes, then dropped and rechecked.
    """
    phone = _normalize_phone(phone)
    now = time.time()

    # Cache hit
    if phone in _USER_CACHE:
        print(f"[CACHE] Hit for {phone}")
        ts, user = _USER_CACHE.pop(phone)

        # If we have a real user -> return it (no TTL)
        if user is not None:
            _USER_CACHE[phone] = (ts, user)  # mark as recently used
            return user

        # If user is None -> apply TTL
        if now - ts < NEGATIVE_CACHE_TTL:
            _USER_CACHE[phone] = (ts, user)  # still valid negative cache
            return None
        # else: expired None -> refetch

    # Cache miss or expired None -> fetch from Sheets
    user = _get_latest_user_from_sheets(phone)
    print(f"[CACHE] Miss for {phone} | Found user: {user is not None}")

    # Cache result (None or dict)
    _USER_CACHE[phone] = (now, user)
    _evict_if_needed()

    return user
