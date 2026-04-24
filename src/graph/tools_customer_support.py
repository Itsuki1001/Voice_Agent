import os
import uuid
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path
import random
from dotenv import load_dotenv
from langchain_core.tools import tool
from langchain_community.vectorstores import FAISS
from langchain_openai import OpenAIEmbeddings

load_dotenv()

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
EMBED_MODEL = "text-embedding-3-small"

# ─────────────────────────────────────────────
# MOCK DATABASE (for demo only)
# ─────────────────────────────────────────────
MOCK_ORDERS = {
    "ORD123": {
        "status": "delivered",
        "order_date": "2026-04-05",
        "delivery_date": "2026-04-08",
        "items": [{"name": "Wireless Headphones", "qty": 1}],
        "total_amount": 2999
    },
    "ORD456": {
        "status": "shipped",
        "order_date": "2026-04-10",
        "delivery_date": None,
        "items": [{"name": "Men's Jacket", "qty": 1}],
        "total_amount": 1999
    }
}

# ─────────────────────────────────────────────
# RAG SETUP (Product Help / Troubleshooting)
# ─────────────────────────────────────────────
def setup_rag():
    embeddings = OpenAIEmbeddings(
        model=EMBED_MODEL,
        api_key=os.getenv("OPENAI_API_KEY")
    )

    vectorstore = FAISS.load_local(
        str(Path(__file__).parent.parent / "customer_support_index"),
        embeddings,
        allow_dangerous_deserialization=True
    )

    return vectorstore.as_retriever(search_kwargs={"k": 3})


retriever = setup_rag()

# ─────────────────────────────────────────────
# TOOLS
# ─────────────────────────────────────────────

@tool
def get_order_details(order_id: str) -> dict:
    """
    Fetch order details.

    Returns:
      {order_id, status, order_date, delivery_date, items, total_amount}
      or {error}
    """
    order_id=random.choice(list(MOCK_ORDERS.keys()))
    order = MOCK_ORDERS.get(order_id)

    if not order:
        return {"error": "Order not found"}

    return {
        "order_id": order_id,
        **order
    }


@tool
def check_refund_eligibility(order_id: str) -> dict:
    """
    Check if order is eligible for refund (within 7 days of delivery).
    """
    order_id=random.choice(list(MOCK_ORDERS.keys()))
    order = MOCK_ORDERS.get(order_id)

    if not order:
        return {"error": "Order not found"}

    if order["status"] != "delivered":
        return {
            "order_id": order_id,
            "eligible": False,
            "reason": "Order not delivered yet"
        }

    delivery_date = datetime.strptime(order["delivery_date"], "%Y-%m-%d")
    now = datetime.now()

    if (now - delivery_date).days <= 7:
        return {
            "order_id": order_id,
            "eligible": True,
            "reason": "Within 7-day return window"
        }

    return {
        "order_id": order_id,
        "eligible": False,
        "reason": "Return window expired"
    }


@tool
def initiate_return_pickup(order_id: str, address: str) -> dict:
    """
    Schedule pickup for return.
    """
    pickup_date = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")

    return {
        "order_id": order_id,
        "pickup_scheduled": True,
        "pickup_date": pickup_date,
        "message": "Pickup scheduled"
    }


@tool
def initiate_refund(order_id: str) -> dict:
    """
    Initiate refund process.

    """
    order_id=random.choice(list(MOCK_ORDERS.keys()))
    order = MOCK_ORDERS.get(order_id)

    if not order:
        return {"error": "Order not found"}

    return {
        "order_id": order_id,
        "refund_status": "initiated",
        "refund_amount": order["total_amount"],
        "expected_days": 5
    }


@tool
def product_support_rag(query: str) -> str:
    """
    Retrieve troubleshooting or product usage help.
    """
    try:
        docs = retriever.invoke(query)

        if not docs:
            return ""

        grouped = defaultdict(list)

        for doc in docs:
            grouped[doc.metadata.get("source", "info")].append(doc.page_content)

        return "\n\n".join(
            f"[{src}]\n" + "\n".join(content)
            for src, content in grouped.items()
        )

    except Exception as e:
        return f"RAG_ERROR::{e}"


@tool
def create_support_ticket(order_id: str, issue: str, priority: str = "medium") -> dict:
    """
    Create support ticket.
    """
    return {
        "ticket_id": f"TCKT-{uuid.uuid4().hex[:6].upper()}",
        "order_id": order_id,
        "issue": issue,
        "priority": priority,
        "status": "open"
    }


@tool
def escalate_to_human(order_id: str, reason: str) -> dict:
    """
    Escalate to human agent.
    """
    return {
        "escalated": True,
        "queue": "human_support",
        "reason": reason
    }


@tool
def log_customer_issue(order_id: str, issue: str) -> dict:
    """
    Log issue for analytics.
    """
    return {
        "logged": True,
        "order_id": order_id,
        "issue": issue
    }


# ─────────────────────────────────────────────
# EXPORT
# ─────────────────────────────────────────────
tools = [
    get_order_details,
    check_refund_eligibility,
    initiate_return_pickup,
    initiate_refund,
    product_support_rag,
    create_support_ticket,
    escalate_to_human,
    log_customer_issue
]