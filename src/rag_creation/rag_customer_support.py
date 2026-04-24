import os
from pathlib import Path
from typing import List
from dotenv import load_dotenv
import sqlite3

from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import FAISS


# -----------------------------
# CONFIG
# -----------------------------
load_dotenv()

DOCS_DIR = "docs_customer_support"
INDEX_PATH = str(Path(__file__).parent.parent / "customer_support_index")

EMBED_MODEL = "text-embedding-3-small"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")


# -----------------------------
# CUSTOM SPLITTER (IMPORTANT)
# -----------------------------
def split_documents(raw_text: str) -> List[str]:
    docs = raw_text.split("### DOCUMENT START")
    clean_docs = []

    for doc in docs:
        if "### DOCUMENT END" in doc:
            content = doc.split("### DOCUMENT END")[0].strip()
            if content:
                clean_docs.append(content)

    return clean_docs


# -----------------------------
# METADATA EXTRACTION
# -----------------------------
def extract_metadata(doc: str):
    title = ""
    doc_type = ""

    for line in doc.split("\n"):
        if line.startswith("Title:"):
            title = line.replace("Title:", "").strip()
        elif line.startswith("Type:"):
            doc_type = line.replace("Type:", "").strip()

    return title, doc_type


# -----------------------------
# LOAD TXT RAG FILES
# -----------------------------
def load_structured_txt(folder_path: str) -> List[Document]:
    documents = []
    folder = Path(folder_path)

    for file in folder.iterdir():
        if not file.is_file() or file.suffix.lower() != ".txt":
            continue

        with open(file, "r", encoding="utf-8") as f:
            raw_text = f.read()

        chunks = split_documents(raw_text)

        for chunk in chunks:
            title, doc_type = extract_metadata(chunk)

            documents.append(
                Document(
                    page_content=chunk,
                    metadata={
                        "source": file.name,
                        "title": title,
                        "type": doc_type
                    }
                )
            )

    return documents


# -----------------------------
# LOAD FAQ FROM DB
# -----------------------------
def load_faq_from_db() -> List[Document]:
    BASE_DIR = Path(__file__).resolve().parent.parent
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
                page_content=f"Question: {q}\nAnswer: {a}",
                metadata={
                    "source": "faq",
                    "type": "faq"
                }
            )
        )

    conn.close()
    return faq_docs


# -----------------------------
# BUILD VECTORSTORE
# -----------------------------
def build_vectorstore() -> FAISS:
    embeddings = OpenAIEmbeddings(
        model=EMBED_MODEL,
        api_key=OPENAI_API_KEY
    )

    # ✅ Load structured RAG docs
    docs = load_structured_txt(DOCS_DIR)

    # ✅ Load FAQ
    faq_docs = load_faq_from_db()

    all_docs = docs + faq_docs

    print(f"Indexed {len(all_docs)} documents")

    vectorstore = FAISS.from_documents(all_docs, embeddings)
    vectorstore.save_local(INDEX_PATH)

    return vectorstore


# -----------------------------
# RUN TEST
# -----------------------------
if __name__ == "__main__":
    vectorstore = build_vectorstore()

    results = vectorstore.similarity_search(
        "my headphones are not connecting",
        k=3
    )

    for i, doc in enumerate(results, 1):
        print(f"\n--- Result {i} ---")
        print(f"Source: {doc.metadata.get('source')}")
        print(f"Type: {doc.metadata.get('type')}")
        print(f"Title: {doc.metadata.get('title')}")
        print(doc.page_content)