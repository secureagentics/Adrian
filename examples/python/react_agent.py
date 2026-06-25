"""Adrian with a LangGraph ReAct agent (``create_react_agent``).

``langgraph.prebuilt.create_react_agent`` is the long-standing prebuilt
ReAct agent. It is being superseded by ``langchain.agents.create_agent``
(deprecated in LangGraph 1.0, to be removed in 2.0) - see
``examples/python/create_agent.py`` for the current form - but plenty of
existing code still uses it, and Adrian instruments both identically.

Uses the asynchronous ``.ainvoke``.

Required env:
    ADRIAN_API_KEY   adr_local_xxx (create one in the dashboard at
                                    Settings -> Agents -> New key)
    OPENAI_API_KEY   sk-xxx        (the agent's brain calls OpenAI)

Optional env:
    ADRIAN_WS_URL    defaults to ws://localhost:8080/ws (the SDK's default)

Install (in your own project):
    pip install adrian-sdk langgraph langchain-openai
    # or, in a uv project:  uv add adrian-sdk langgraph langchain-openai

Run:
    ADRIAN_API_KEY=adr_local_... OPENAI_API_KEY=sk-... \\
        python examples/python/react_agent.py
"""
from __future__ import annotations

import asyncio
import os
import sys

import adrian
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langgraph.prebuilt import create_react_agent


@tool
def get_stock_quote(ticker: str) -> str:
    """Return the latest price and day change for a stock ticker."""
    # Stubbed so the example runs without a real market-data backend.
    return f"{ticker.upper()}: $187.42 (+1.8% today)"


async def main() -> int:
    if not os.environ.get("ADRIAN_API_KEY"):
        sys.stderr.write("ADRIAN_API_KEY is not set. Create one in the dashboard.\n")
        return 1
    if not os.environ.get("OPENAI_API_KEY"):
        sys.stderr.write("OPENAI_API_KEY is not set; the agent's brain is ChatOpenAI.\n")
        return 1

    adrian.init(api_key=os.environ["ADRIAN_API_KEY"])

    agent = create_react_agent(
        ChatOpenAI(model="gpt-4o-mini", temperature=0),
        [get_stock_quote],
        prompt="You are a markets assistant. Use the tools available before answering.",
    )

    result = await agent.ainvoke(
        {"messages": [("user", "What is NVDA trading at right now?")]},
    )
    print(result["messages"][-1].content)

    adrian.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
