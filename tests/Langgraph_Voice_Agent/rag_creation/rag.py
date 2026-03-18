import json
from pathlib import Path
from typing import List

from langchain_community.document_loaders import (
    PyPDFLoader,
    Docx2txtLoader,
    TextLoader,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document


# -----------------------------
# CONFIG
# -----------------------------
DOCS_DIR = "docs"
FAQ_FILE = "docs/faq_document.json"
INDEX_PATH = "C:/Users/Administrator/Documents/Python/Whoosh_Final/Production/peetsinn_index"

EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
CHUNK_SIZE = 350
CHUNK_OVERLAP = 100


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
def load_faq_json(path: str) -> List[Document]:
    faq_docs = []

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for item in data:
        q = item.get("question", "").strip()
        a = item.get("answer", "").strip()

        if not q or not a:
            continue

        text = f"{q}\n{a}"

        faq_docs.append(
            Document(
                page_content=text,
                metadata={"source": "faq"},
            )
        )

    return faq_docs


# -----------------------------
# BUILD VECTORSTORE
# -----------------------------
# -----------------------------
# BUILD VECTORSTORE
# -----------------------------
def build_vectorstore() -> FAISS:
    embeddings = HuggingFaceEmbeddings(model_name=EMBED_MODEL)

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
    )

    docs = load_files(DOCS_DIR)
    docs = splitter.split_documents(docs)  # Chunks created here
    
    # NOW add filename to each chunk
    for doc in docs:
        original_content = doc.page_content
        filename = doc.metadata.get("source", "unknown")
        doc.page_content = f"Source: {filename}\n\n{original_content}"

    faq_docs = load_faq_json(FAQ_FILE)



    all_docs = docs + faq_docs

    print(f"Indexed {len(all_docs)} documents")

    vs = FAISS.from_documents(all_docs, embeddings)
    vs.save_local(INDEX_PATH)

    return vs


# -----------------------------
# RUN
# -----------------------------
if __name__ == "__main__":
    vectorstore = build_vectorstore()

    results = vectorstore.similarity_search(
        "is there a helipad",
        k=3,
    )

    for i, doc in enumerate(results, 1):
        print(f"\n--- Result {i} ---")
        print(f"Source: {doc.metadata['source']}")
        print(doc.page_content)
