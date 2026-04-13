import sqlite3
import os
import base64
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from dotenv import load_dotenv
from pathlib import Path
load_dotenv()

#  Absolute base path (fixes your path issues)
BASE_DIR = Path(__file__).resolve().parent.parent   # goes to src/

def setup_memory():
    # 1. Ensure DB directory exists (absolute, not relative)
    db_dir = os.path.join(BASE_DIR, "Databases")
    os.makedirs(db_dir, exist_ok=True)

    # 2. Setup Encryption
    key_b64 = os.getenv("ENCRYPTION_KEY")
    if not key_b64:
        raise ValueError("ENCRYPTION_KEY not found in environment variables")
    
    key = base64.b64decode(key_b64)
    serde = EncryptedSerializer.from_pycryptodome_aes(key=key)

    # 3. SQLite path (stable now)
    db_path = os.path.join(db_dir, "petesinn.sqlite")

    # 4. Connect
    sqlite_conn = sqlite3.connect(db_path, check_same_thread=False)

    # 5. Performance tweaks
    sqlite_conn.execute("PRAGMA journal_mode=WAL;")
    sqlite_conn.execute("PRAGMA synchronous=NORMAL;")

    # 6.  Create FAQ table (so you can reuse same DB)
    sqlite_conn.execute("""
        CREATE TABLE IF NOT EXISTS faq (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT,
            answer TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    sqlite_conn.execute("""
        CREATE TABLE IF NOT EXISTS handoffs (
            id TEXT PRIMARY KEY,
            phone TEXT,
            message TEXT
        )
                        
    """)
    sqlite_conn.execute("""
    CREATE TABLE IF NOT EXISTS processed(
        id TEXT PRIMARY KEY
    )
    """)

    sqlite_conn.commit()

    # 7. LangGraph memory
    memory = SqliteSaver(sqlite_conn, serde=serde)

    return memory, sqlite_conn


#  Initialize once
memory, conn = setup_memory()