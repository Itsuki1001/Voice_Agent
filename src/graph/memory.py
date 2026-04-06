import sqlite3
import os
import base64
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from dotenv import load_dotenv

load_dotenv()

def setup_memory():
    # 1. Ensure the directory exists
    db_dir = "Databases"
    if not os.path.exists(db_dir):
        os.makedirs(db_dir)
        print(f"Directory created: {db_dir}")

    # 2. Setup Encryption
    key_b64 = os.getenv("ENCRYPTION_KEY")
    if not key_b64:
        raise ValueError("ENCRYPTION_KEY not found in environment variables")
    
    key = base64.b64decode(key_b64)
    # Using the pycryptodome AES serializer as per your snippet
    serde = EncryptedSerializer.from_pycryptodome_aes(key=key)

    # 3. Connect to SQLite
    # Tip: Using "/" is safer across all OS types (Windows/Mac/Linux)
    db_path = os.path.join(db_dir, "petesinn.sqlite")
    sqlite_conn = sqlite3.connect(db_path, check_same_thread=False)

    # 4. Performance Optimizations
    sqlite_conn.execute("PRAGMA journal_mode=WAL;")
    sqlite_conn.execute("PRAGMA synchronous=NORMAL;")

    return SqliteSaver(sqlite_conn, serde=serde)

memory = setup_memory()