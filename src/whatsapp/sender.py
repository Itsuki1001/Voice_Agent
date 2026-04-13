import logging
from concurrent.futures import ThreadPoolExecutor, Future

from .client import send_text

from .handoff import trigger_handoff


# --------------------------------------------------
# Constants
# --------------------------------------------------

HANDOFF_TRIGGER = "let me check with the staff"

SEND_WORKERS = 8        # I/O-bound, safe to keep high


# --------------------------------------------------
# Executor
# --------------------------------------------------

SEND_EXECUTOR = ThreadPoolExecutor(
    max_workers=SEND_WORKERS,
    thread_name_prefix="wa-sender"
)


# --------------------------------------------------
# Internal helpers
# --------------------------------------------------

def _submit(fn, *args) -> Future:
    """Submit a task and attach a done-callback for error logging."""
    future = SEND_EXECUTOR.submit(fn, *args)

    def _on_done(f: Future):
        exc = f.exception()
        if exc:
            logging.error(
                "[SENDER] %s(%s) failed: %s",
                fn.__name__, args[0], exc   # args[0] is always recipient
            )

    future.add_done_callback(_on_done)
    return future


# --------------------------------------------------
# Public API
# --------------------------------------------------

def send_whatsapp_message(
    recipient: str,
    ai_response: str,
    user_message: str | None = None,
) -> None:
    """
    Dispatches WhatsApp messages asynchronously.
    Does NOT block — safe to call from worker threads or FastAPI.


    """
    if not ai_response:
        logging.warning("[SENDER] Empty ai_response for recipient=%s", recipient)
        return

    # 1️⃣ Handoff detection — checked BEFORE any parsing
    if HANDOFF_TRIGGER in ai_response.lower().strip():
        if not user_message:
            logging.error(
                "[SENDER] Handoff triggered but user_message missing. recipient=%s",
                recipient
            )
            return  # fail safe — don't crash the caller
        logging.info("[SENDER] Handoff triggered. recipient=%s", recipient)
        trigger_handoff(recipient, user_message)
        return
    
    _submit(send_text, recipient, ai_response)


    