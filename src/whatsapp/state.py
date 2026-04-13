from pathlib import Path
import sqlite3

BASE_DIR = Path(__file__).resolve().parent.parent
DB = str(BASE_DIR / "Databases" / "petesinn.sqlite")


# --------------------------------------------------
# HANDOFF FUNCTIONS
# --------------------------------------------------

def save_handoff(hid, phone, msg):
    conn = sqlite3.connect(DB, check_same_thread=False)
    cur = conn.cursor()

    cur.execute(
        "INSERT OR REPLACE INTO handoffs (id, phone, message) VALUES (?,?,?)",
        (hid, phone, msg)
    )

    conn.commit()
    conn.close()


def get_handoff(hid):
    conn = sqlite3.connect(DB, check_same_thread=False)
    cur = conn.cursor()

    cur.execute(
        "SELECT phone, message FROM handoffs WHERE id=?",
        (hid,)
    )

    r = cur.fetchone()
    conn.close()

    if not r:
        return None

    return {
        "user_phone": r[0],
        "user_message": r[1]
    }


def delete_handoff(hid):
    conn = sqlite3.connect(DB, check_same_thread=False)
    cur = conn.cursor()

    cur.execute("DELETE FROM handoffs WHERE id=?", (hid,))
    conn.commit()
    conn.close()


# --------------------------------------------------
# MESSAGE DEDUPE
# --------------------------------------------------

def seen(mid):
    conn = sqlite3.connect(DB, check_same_thread=False)
    cur = conn.cursor()

    cur.execute("SELECT 1 FROM processed WHERE id=?", (mid,))
    r = cur.fetchone()

    conn.close()
    return r is not None


def mark(mid):
    conn = sqlite3.connect(DB, check_same_thread=False)
    cur = conn.cursor()

    cur.execute(
        "INSERT OR IGNORE INTO processed (id) VALUES (?)",
        (mid,)
    )

    conn.commit()
    conn.close()