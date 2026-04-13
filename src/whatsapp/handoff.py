import re
import uuid
from .client import send_text
from .llm_utils import is_general_query
from .graph_update import store_handoff_reply
from .faq_generation import update_faq
from .state import save_handoff, get_handoff, delete_handoff

# --------------------------------------------------
# HANDOFF CONFIG
# --------------------------------------------------

DEFAULT_HUMAN_PHONE = "919061293580"
HANDOFF_STORE = {}

# --------------------------------------------------
# HANDOFF HELPERS
# --------------------------------------------------

def generate_handoff_id() -> str:
    """Generate short handoff ID"""
    return uuid.uuid4().hex[:8].upper()

def trigger_handoff(user_phone: str, user_message: str):
    """
    Escalate to human.
    Stores context in memory.
    """
    handoff_id = generate_handoff_id()
    

    save_handoff(handoff_id, user_phone, user_message)

    print(HANDOFF_STORE)

    send_text(
        DEFAULT_HUMAN_PHONE,
        f"""🔔 BOT NEEDS HELP

Handoff ID: {handoff_id}
User: {user_phone}

Question:
"{user_message}"

Reply as:
ANSWER {handoff_id}: <your reply>
"""
    )

    send_text(
        user_phone,
        "Let me check this with the staff and get back to you 🙂"
    )

def handle_human_reply(from_phone: str, message: str) -> bool:
    """
    Handle staff reply.
    Returns True if message was a handoff reply.
    """
    if from_phone != DEFAULT_HUMAN_PHONE:
        return False

    match = re.search(
        r"ANSWER\s+([A-F0-9]{8}):\s*(.+)",
        message,
        re.DOTALL
    )

    if not match:
        return False

    handoff_id, answer = match.groups()
    handoff = get_handoff(handoff_id)

    print(handoff_id)
    print(HANDOFF_STORE)

    if not handoff:
        send_text(from_phone, "❌ Invalid or expired handoff ID")
        return True

    send_text(
        handoff["user_phone"],
        f"""Here’s an update from our staff 👇

    Your question:
    {handoff["user_message"]}

    Answer:
    {answer.strip()}
    """
    )
    send_text(from_phone, "✅ Reply sent")

    store_handoff_reply(
        thread_id=handoff["user_phone"],
        user_message=handoff["user_message"],
        staff_text=answer.strip()
    )


    if is_general_query(handoff["user_message"]):
        update_faq(handoff["user_message"], answer.strip())

    delete_handoff(handoff_id)

    return True
