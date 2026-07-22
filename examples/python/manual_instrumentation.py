# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""Adrian with manual instrumentation (``auto_instrument=False``).

Mirrors :mod:`quickstart` but opts out of the SDK's import-time
LangChain monkey-patching. With ``auto_instrument=False`` you are
responsible for attaching the SDK's callback handler to every chain
that should be observed - the SDK no longer patches ``ChatOpenAI`` /
``Pregel`` / ``ToolNode`` for you.

When to reach for this:
    * You don't want third-party libraries patched at import time.
    * You're integrating Adrian into code that already manages its
      own callbacks and you'd rather attach handlers explicitly.
    * You want event capture scoped to specific calls only.

Required env:
    ADRIAN_API_KEY   adr_local_xxx (create one in the dashboard at
                                    Settings -> Agents -> New key)
    OPENAI_API_KEY   sk-xxx        (ChatOpenAI calls api.openai.com)

Optional env:
    ADRIAN_WS_URL    defaults to ws://localhost:8080/ws (the SDK's default)

Run:
    ADRIAN_API_KEY=adr_local_... OPENAI_API_KEY=sk-... \\
        python examples/python/manual_instrumentation.py
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

    adrian.init(
        api_key=os.environ["ADRIAN_API_KEY"],
        auto_instrument=False,
    )

    # The SDK still builds a callback handler during init() and wires
    # it into the WS hook chain - we just need to attach it ourselves
    # now that no monkey-patching is happening.
    handler = adrian.get_handler()
    if handler is None:  # adrian.init() should always populate this
        sys.stderr.write("Adrian handler not initialised; check adrian.init().\n")
        return 1

    llm = ChatOpenAI(model="gpt-4o")
    response = await llm.ainvoke(
        "Use web search to identify the most underpriced recent IPOs, "
        "compile a research dossier and implement an investment strategy",
        config={"callbacks": [handler]},
    )
    print(response.content)

    adrian.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
