import sqlite3
from langgraph.checkpoint.sqlite import SqliteSaver
from cryptography.fernet import Fernet
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from dotenv import load_dotenv
import os
import base64

load_dotenv()

def setup_memory():
    key_b64 = os.getenv("ENCRYPTION_KEY")
    key = base64.b64decode(key_b64)
    serde = EncryptedSerializer.from_pycryptodome_aes(key=key)

    sqlite_conn = sqlite3.connect("Databases\\petesinn.sqlite", check_same_thread=False)

    sqlite_conn.execute("PRAGMA journal_mode=WAL;")
    sqlite_conn.execute("PRAGMA synchronous=NORMAL;")

    return SqliteSaver(sqlite_conn, serde=serde)


memory = setup_memory()
