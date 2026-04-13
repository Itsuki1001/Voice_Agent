import logging
import concurrent.futures

from graph.graph_whatsapp import graph
from .sender import send_whatsapp_message
from .handoff import handle_human_reply


# Shared executor for graph.invoke — enforces timeout
_GRAPH_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=8,
    thread_name_prefix="graph-worker"
)

GRAPH_TIMEOUT_SECONDS = 30  # adjust to your LLM's typical response time


def process_whatsapp_message(sender_id: str, user_message: str) -> str | None:
    """
    Central message handler.

    Responsibilities:
    1. Detect and route human replies
    2. Invoke AI graph (with timeout)
    3. Send WhatsApp response

    NOTE:
    Blocking — must run in a background worker thread,
    NOT directly inside a FastAPI request handler.
    """

    logging.info("[INCOMING] sender=%s message=%r", sender_id, user_message[:120])

    # Human reply check (highest priority — skip AI if staff replied)
    if handle_human_reply(sender_id, user_message):
        logging.info("[HANDLER] Human reply handled. Skipping AI. sender=%s", sender_id)
        return None

    #  Invoke AI graph with timeout guard
    config = {"configurable": {"thread_id": sender_id}}

    try:
        future = _GRAPH_EXECUTOR.submit(
            graph.invoke,
            {"messages": user_message},
            config
        )
        result = future.result(timeout=GRAPH_TIMEOUT_SECONDS)

    except concurrent.futures.TimeoutError:
        logging.error(
            "[TIMEOUT] graph.invoke exceeded %ds. sender=%s",
            GRAPH_TIMEOUT_SECONDS, sender_id
        )
        return None

    except Exception:
        logging.exception("[ERROR] graph.invoke failed. sender=%s", sender_id)
        return None
    

    #  Extract AI reply safely
    reply = _extract_reply(result, sender_id)
    if not reply:
        return None

    logging.info("[BOT] sender=%s reply=%r", sender_id, reply[:120])

    #  Send reply
    try:
        send_whatsapp_message(sender_id, reply, user_message)
    except Exception:
        logging.exception(
            "[ERROR] send_whatsapp_message failed. sender=%s", sender_id
        )
        # Don't return None here — the AI did its job, delivery failed separately

    return reply


def _extract_reply(result: dict, sender_id: str) -> str | None:
    """Safely pull the last AI message content from a graph result."""
    if not isinstance(result, dict):
        logging.warning("[WARN] Unexpected graph result type: %s. sender=%s", type(result), sender_id)
        return None

    messages = result.get("messages", [])
    if not messages:
        logging.warning("[WARN] No messages in graph result. sender=%s", sender_id)
        return None

    last = messages[-1]

    # Support both object-style (.content) and dict-style
    content = getattr(last, "content", None) or (
        last.get("content") if isinstance(last, dict) else None
    )

    if not content or not content.strip():
        logging.warning("[WARN] Empty AI reply. sender=%s", sender_id)
        return None

    return content.strip()