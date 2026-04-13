import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

# --------------------------------------------------
# LLM SETUP
# --------------------------------------------------

load_dotenv()

llm = ChatOpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    model="gpt-4.1-mini",
    timeout=15
)

# --------------------------------------------------
# QUERY CLASSIFICATION
# --------------------------------------------------

def is_general_query(user_message: str) -> bool:
    """
    Returns True if question is general (FAQ-worthy),
    False if user-specific.
    """
    prompt = f"""
    You are classifying questions for a resort FAQ system.

    A GENERAL query:
    - Applies to most guests
    - About facilities, policies, or nearby places
    - Example: "Do you have wifi?", "Is there a pool?", "Is there a beach nearby?"

    A USER-SPECIFIC query:
    - Depends on a specific booking, person, or situation
    - Example: "Can I check in early?", "Can I get a refund?", "My room AC not working"

    Answer ONLY one word: true or false

    Query: {user_message}
    """
    response = llm.invoke(prompt)
    return response.content.strip().lower() == "true"
