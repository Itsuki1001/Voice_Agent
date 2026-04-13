import json
import os
from pathlib import Path
from typing import List
from dotenv import load_dotenv

import sqlite3

from langchain_community.document_loaders import (
    PyPDFLoader,
    Docx2txtLoader,
    TextLoader,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_openai import OpenAIEmbeddings          # ← changed
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document


# -----------------------------
# CONFIG
# -----------------------------


load_dotenv()


DOCS_DIR = "docs_saless"

INDEX_PATH = str(Path(__file__).parent.parent / "sales_index")

EMBED_MODEL = "text-embedding-3-small"   # ← or "text-embedding-3-large" / "text-embedding-ada-002"
CHUNK_SIZE = 300
CHUNK_OVERLAP = 50

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


# -----------------------------
# LOAD FILES
# -----------------------------
def load_files(folder_path: str) -> List[Document]:
    docs = []
    folder = Path(folder_path)

    loaders = {
        ".pdf": PyPDFLoader,
        ".txt": TextLoader,
        ".docx": Docx2txtLoader,
    }

    for file in folder.iterdir():
        if not file.is_file():
            continue
        if file.suffix.lower() == ".json":
            continue

        loader_cls = loaders.get(file.suffix.lower())
        if not loader_cls:
            continue

        loader = loader_cls(str(file))
        loaded = loader.load()

        for d in loaded:
            d.metadata = {"source": file.name}
            docs.append(d)

    return docs


# -----------------------------
# LOAD FAQ JSON (NO CHUNKING)
# -----------------------------
def load_faq_from_db() -> List[Document]:
    BASE_DIR = Path(__file__).resolve().parent.parent  # goes to src/
    
    db_path = BASE_DIR / "Databases" / "petesinn.sqlite"

    conn = sqlite3.connect(db_path)

    cursor = conn.execute("SELECT question, answer FROM faq")

    faq_docs = []

    for q, a in cursor.fetchall():
        q = q.strip()
        a = a.strip()

        if not q or not a:
            continue

        faq_docs.append(
            Document(
                page_content=f"{q}\n{a}",
                metadata={"source": "faq"},
            )
        )

    conn.close()

    return faq_docs


# -----------------------------
# BUILD VECTORSTORE
# -----------------------------
def build_vectorstore() -> FAISS:
    embeddings = OpenAIEmbeddings(model=EMBED_MODEL,api_key=OPENAI_API_KEY)   # ← changed

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )

    docs = load_files(DOCS_DIR)
    docs = splitter.split_documents(docs)

    for doc in docs:
        filename = doc.metadata.get("source", "unknown")
        doc.page_content = f"Source: {filename}\n\n{doc.page_content}"

    #faq_docs = load_faq_from_db()

    all_docs = docs 

    print(f"Indexed {len(all_docs)} documents")

    vs = FAISS.from_documents(all_docs, embeddings)
    vs.save_local(INDEX_PATH)

    return vs


# -----------------------------
# RUN
# -----------------------------
if __name__ == "__main__":
    vectorstore = build_vectorstore()

    results = vectorstore.similarity_search("whats the wifi password", k=3)

    for i, doc in enumerate(results, 1):
        print(f"\n--- Result {i} ---")
        print(f"Source: {doc.metadata['source']}")
        print(doc.page_content)