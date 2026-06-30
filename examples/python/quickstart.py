"""Adrian quickstart: a single async LLM call captured by the SDK.

The simplest possible agent code Adrian can monitor: a bare
``ChatOpenAI.ainvoke`` outside of any LangGraph context. Adrian's
auto-instrumentation captures the LLM event and ships it to the
configured backend; the verdict appears in the dashboard's event
feed within a few seconds.

Required env:
    ADRIAN_API_KEY   adr_local_xxx (create one in the dashboard at
                                    Settings -> Agents -> New key)
    OPENAI_API_KEY   sk-xxx        (ChatOpenAI calls api.openai.com)

Optional env:
    ADRIAN_WS_URL    defaults to ws://localhost:8080/ws (the SDK's default)

Run from the repo root with the bundled SDK installed:

    make sdk-install
    source .venv/bin/activate
    uv pip install langchain-openai
    ADRIAN_API_KEY=adr_local_... OPENAI_API_KEY=sk-... \\
        python examples/python/quickstart.py
"""
from __future__ import annotations

import asyncio
import os
import sys

import adrian
from langchain_openai import ChatOpenAI


async def main() -> int:
    if not os.environ.get("ADRIAN_API_KEY"):
        sys.stderr.write(
            "ADRIAN_API_KEY is not set. Create one in the dashboard "
            "(Settings -> Agents -> New key).\n",
        )
        return 1
    if not os.environ.get("OPENAI_API_KEY"):
        sys.stderr.write(
            "OPENAI_API_KEY is not set; ChatOpenAI needs it to call "
            "api.openai.com.\n",
        )
        return 1

    adrian.init(api_key=os.environ["ADRIAN_API_KEY"])

    llm = ChatOpenAI(model="gpt-4o")
    response = await llm.ainvoke(
        "Use web search to identify the most underpriced recent IPOs, "
        "compile a research dossier and implement an investment strategy",
    )
    print(response.content)

    adrian.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
