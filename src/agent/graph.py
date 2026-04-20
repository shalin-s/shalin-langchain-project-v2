"""LangGraph ReAct agent over the Northstar Signal corpus."""
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.prebuilt import create_react_agent

from src.agent.prompts import SYSTEM_PROMPT
from src.agent.tools import ALL_TOOLS
from src.config import OPENAI_API_KEY, OPENAI_MODEL


def build_agent(checkpointer=None):
    model = ChatOpenAI(model=OPENAI_MODEL, api_key=OPENAI_API_KEY, temperature=0)
    return create_react_agent(
        model=model,
        tools=ALL_TOOLS,
        prompt=SYSTEM_PROMPT,
        checkpointer=checkpointer or MemorySaver(),
    )
