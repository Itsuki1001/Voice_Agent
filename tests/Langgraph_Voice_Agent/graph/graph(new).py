import os
import logging
from typing import Annotated, TypedDict, Literal

from dotenv import load_dotenv
from pydantic import BaseModel

from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from langgraph.graph import StateGraph, START,END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
    AIMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.utils import trim_messages, count_tokens_approximately
from langchain_core.runnables import RunnableConfig

from .tools import tools
from .memory import memory
from prompts.prmopt_lesstokens import get_system_prompt

# -------------------------------------------------------------------
# SETUP
# -------------------------------------------------------------------

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_TOKENS = 1000
MAX_STEPS = 6
TIMEOUT = 30

if not os.getenv("LLM_API_KEY"):
    raise ValueError("LLM_API_KEY not set (Groq)")

if not os.getenv("OPENAI_API_KEY"):
    raise ValueError("OPENAI_API_KEY not set")

# LangSmith (optional)
os.environ["LANGCHAIN_TRACING_V2"] = "true"
os.environ["LANGSMITH_ENDPOINT"] = "https://api.smith.langchain.com"
os.environ["LANGSMITH_API_KEY"] = os.getenv("LANGSMITH_API_KEY", "")
os.environ["LANGCHAIN_PROJECT"] = "Production - Petes Inn Resort"

# -------------------------------------------------------------------
# LLMs
# -------------------------------------------------------------------

# Human / planning / chit-chat model
human_llm = ChatGroq(
    api_key=os.getenv("LLM_API_KEY"),
    model="/gpt-oss-openai120b",
    streaming=False,
    timeout=TIMEOUT,
    max_retries=2,
)

# Strict / ops model (booking, photos, links, availability, distance)
strict_llm = ChatOpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    model="gpt-4o-mini",
    temperature=0.2,
    timeout=TIMEOUT,
    max_retries=2,
)

human_llm_with_tools = human_llm.bind_tools(tools)
strict_llm_with_tools = strict_llm.bind_tools(tools)

# -------------------------------------------------------------------
# STATE
# -------------------------------------------------------------------

class State(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]
    route: str

# -------------------------------------------------------------------
# ROUTER SCHEMA
# -------------------------------------------------------------------

class RouteDecision(BaseModel):
    intent: Literal["ops_strict", "planning", "info_rag", "chit_chat"]
    confidence: float

# -------------------------------------------------------------------
# MESSAGE FILTERING
# -------------------------------------------------------------------

def filter_messages(messages: list[BaseMessage]) -> list[BaseMessage]:
    filtered = []

    for msg in messages:
        if isinstance(msg, HumanMessage):
            filtered.append(HumanMessage(content=msg.content))

        elif isinstance(msg, AIMessage):
            filtered.append(
                AIMessage(
                    content=msg.content or "",
                    tool_calls=getattr(msg, "tool_calls", []),
                )
            )

        elif isinstance(msg, ToolMessage):
            filtered.append(
                ToolMessage(
                    content=msg.content or "",
                    tool_call_id=msg.tool_call_id,
                    name=msg.name,
                )
            )

    return filtered

# -------------------------------------------------------------------
# ROUTER NODE (LLM-based, no keywords)
# -------------------------------------------------------------------

def router_node(state: State, config: RunnableConfig) -> dict:
    
    messages= trim_messages(
        state["messages"],
        strategy="last",
        token_counter=count_tokens_approximately,
        max_tokens=600,
        start_on="human",
        end_on=("human", "tool"),
    )
    

    system = SystemMessage(content="""
You are an intent router for a resort chatbot.

Classify the user's intent into one of:
- ops_strict: booking, availability, photos, distance, links, payments, tools, confirmations
- info_rag: questions about resort info, policies, amenities, nearby places, food, rules
- planning: itineraries, suggestions, plans, comparisons, schedules
- chit_chat: greetings, small talk, casual chat

Return ONLY valid JSON:
{
  "intent": "...",
  "confidence": 0.0
}
""")

    decision = strict_llm.with_structured_output(RouteDecision).invoke([system] + messages)

    logger.info(f"[ROUTER] intent={decision.intent} confidence={decision.confidence}")

    return {"route": decision.intent}

# -------------------------------------------------------------------
# HUMAN LLM NODE (gpt-oss-120b)
# -------------------------------------------------------------------

def human_llm_node(state: State, config: RunnableConfig) -> State:
    sender_id = config["configurable"].get("thread_id", "unknown")
    step_count = config["configurable"].get("step_count", 0)

    if step_count >= MAX_STEPS:
        return {"messages": [AIMessage(content="I'm having trouble completing this. Can you rephrase?")]}

    clean = filter_messages(state["messages"])

    trimmed = trim_messages(
        clean,
        strategy="last",
        token_counter=count_tokens_approximately,
        max_tokens=MAX_TOKENS,
        start_on="human",
        end_on=("human", "tool"),
    )

    system_prompt = get_system_prompt(sender_id)
    conversation = [SystemMessage(content=system_prompt)] + trimmed

    logger.info(f"[HUMAN LLM] Tokens: {count_tokens_approximately(conversation)}")

    response = human_llm_with_tools.invoke(conversation)
    return {"messages": [response]}

# -------------------------------------------------------------------
# STRICT LLM NODE (gpt-4o-mini)
# -------------------------------------------------------------------

def strict_llm_node(state: State, config: RunnableConfig) -> State:
    sender_id = config["configurable"].get("thread_id", "unknown")
    step_count = config["configurable"].get("step_count", 0)

    if step_count >= MAX_STEPS:
        return {"messages": [AIMessage(content="I'm having trouble completing this. Can you rephrase?")]}

    clean = filter_messages(state["messages"])

    trimmed = trim_messages(
        clean,
        strategy="last",
        token_counter=count_tokens_approximately,
        max_tokens=MAX_TOKENS,
        start_on="human",
        end_on=("human", "tool"),
    )

    system_prompt = get_system_prompt(sender_id)
    conversation = [SystemMessage(content=system_prompt)] + trimmed

    logger.info(f"[STRICT LLM] Tokens: {count_tokens_approximately(conversation)}")

    response = strict_llm_with_tools.invoke(conversation)
    return {"messages": [response]}

# -------------------------------------------------------------------
# GRAPH
# -------------------------------------------------------------------

builder = StateGraph(State)

builder.add_node("router", router_node)
builder.add_node("human_llm", human_llm_node)
builder.add_node("strict_llm", strict_llm_node)
builder.add_node("tools", ToolNode(tools))
builder.add_node("tools2", ToolNode(tools)) 

builder.add_edge(START, "router")

def route_selector(state: State):
    return state["route"]

builder.add_conditional_edges(
    "router",
    route_selector,
    {
        "ops_strict": "strict_llm",
        "planning": "human_llm",
        "info_rag": "human_llm",
        "chit_chat": "human_llm",
    },
)

# Tool loops
builder.add_conditional_edges("human_llm", tools_condition)
builder.add_conditional_edges("strict_llm", tools_condition)

# Always return tool results to STRICT model (for safe formatting)
builder.add_edge("tools", "strict_llm")

graph = builder.compile(checkpointer=memory)

logger.info("✅ Graph ready (Human + Strict dual-LLM with router)")
