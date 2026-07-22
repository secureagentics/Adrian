# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""Adrian with a LangChain agent (``create_agent``).

The current LangChain agent constructor
(``langchain.agents.create_agent``), the successor to LangGraph's
``create_react_agent``. Bracket your normal agent code with
``adrian.init`` / ``adrian.shutdown`` and the SDK auto-instruments the
whole loop: every reasoning step (LLM call) and every tool call is
captured as a paired event and classified in the dashboard.

Uses the synchronous ``.invoke`` (the SDK instruments the sync and
async paths alike).

Required env:
    ADRIAN_API_KEY   adr_local_xxx (create one in the dashboard at
                                    Settings -> Agents -> New key)
    OPENAI_API_KEY   sk-xxx        (the agent's brain calls OpenAI)

Optional env:
    ADRIAN_WS_URL    defaults to ws://localhost:8080/ws (the SDK's default)

Install (in your own project):
    pip install adrian-sdk langchain langchain-openai
    # or, in a uv project:  uv add adrian-sdk langchain langchain-openai

Run:
    ADRIAN_API_KEY=adr_local_... OPENAI_API_KEY=sk-... \\
        python examples/python/create_agent.py
"""
from __future__ import annotations

import os
import sys

import adrian
from langchain.agents import create_agent
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI


@tool
def web_search(query: str) -> str:
    """Search the web and return a short summary of the top results."""
    # Stubbed so the example runs without a real search backend.
    return (
        f"Top results for {query!r}: three recently-listed companies are "
        "trading below their last private valuation; analyst sentiment is mixed."
    )


def main() -> int:
    if not os.environ.get("ADRIAN_API_KEY"):
        sys.stderr.write("ADRIAN_API_KEY is not set. Create one in the dashboard.\n")
        return 1
    if not os.environ.get("OPENAI_API_KEY"):
        sys.stderr.write("OPENAI_API_KEY is not set; the agent's brain is ChatOpenAI.\n")
        return 1

    adrian.init(api_key=os.environ["ADRIAN_API_KEY"])

    agent = create_agent(
        ChatOpenAI(model="gpt-4o-mini", temperature=0),
        [web_search],
        system_prompt="You are a research analyst. Use the tools available before answering.",
    )

    result = agent.invoke(
        {"messages": [("user", "Which recent IPOs look underpriced? Search first, then summarise.")]},
    )
    print(result["messages"][-1].content)

    adrian.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
