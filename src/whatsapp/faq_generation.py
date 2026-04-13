import os
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent  # go to src

DB_PATH = BASE_DIR / "Databases" / "petesinn.sqlite"


def update_faq(user_question: str, staff_reply: str):
    try:
        # Ensure directory exists
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

        #  Connect to DB (creates if not exists)
        conn = sqlite3.connect(DB_PATH)

        # Create table if not exists (like your JSON init)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS faq (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT,
                answer TEXT
            )
        """)

        #  Insert new FAQ
        conn.execute(
            "INSERT INTO faq (question, answer) VALUES (?, ?)",
            (user_question, staff_reply)
        )

        conn.commit()
        conn.close()

    except Exception as e:
        print(f"Error writing to FAQ database: {e}")