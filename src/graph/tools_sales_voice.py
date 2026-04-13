import os
from pathlib import Path
from collections import defaultdict
from langchain_core.tools import tool
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings
from dotenv import load_dotenv
load_dotenv()

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
EMBED_MODEL = "text-embedding-3-small"
BASE_DIR = Path(__file__).parent.parent
RAG_PATH = BASE_DIR / "sales_index"   # your FAISS index folder


# ─────────────────────────────────────────────
# SETUP RAG
# ─────────────────────────────────────────────
def _setup_rag():
    embeddings = OpenAIEmbeddings(
        model=EMBED_MODEL,
        api_key=os.getenv("OPENAI_API_KEY")
    )

    vectorstore = FAISS.load_local(
        str(RAG_PATH),
        embeddings,
        allow_dangerous_deserialization=True
    )

    return vectorstore.as_retriever(search_kwargs={"k": 3})


retriever = _setup_rag()


# ─────────────────────────────────────────────
# SALES RAG TOOL
# ─────────────────────────────────────────────
@tool
def sales_rag_tool(query: str) -> str:
    """
    Retrieve sales knowledge:
    - product value
    - pain points
    - objection handling
    - closing strategies
    """

    try:
        docs = retriever.invoke(query)

        if not docs:
            return ""

        grouped = defaultdict(list)

        for doc in docs:
            source = doc.metadata.get("source", "sales")
            grouped[source].append(doc.page_content)

        return "\n\n".join(
            f"[{src}]\n" + "\n".join(chunks)
            for src, chunks in grouped.items()
        )

    except Exception as e:
        return f"RAG_ERROR::{str(e)}"


# ─────────────────────────────────────────────
# OPTIONAL: SIMPLE LOGGER (for later use)
# ─────────────────────────────────────────────
@tool
def log_conversation(data: str) -> str:
    """
    Log conversation data (optional for analytics).
    """
    try:
        log_file = BASE_DIR / "logs.txt"
        with open(log_file, "a") as f:
            f.write(data + "\n")
        return "logged"
    except Exception as e:
        return f"LOG_ERROR::{str(e)}"


# ─────────────────────────────────────────────
# EXPORT TOOLS
# ─────────────────────────────────────────────
tools = [
    sales_rag_tool,
    # log_conversation   # enable later if needed
]