# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics
#
# Licensed under the Apache Licence, Version 2.0 (the "Licence").
# You may not use this file except in compliance with the Licence.
# A copy of the Licence is included at LICENSE in the repository root.
"""Minimal quickstart: monitor Anthropic API calls with Adrian.

Run::

    export ANTHROPIC_API_KEY="sk-ant-..."
    export ADRIAN_API_KEY="..."          # optional -- omit to collect locally only
    python examples/python/anthropic_quickstart.py
"""

from __future__ import annotations

import asyncio
import os

import anthropic
import adrian

# ------------------------------------------------------------------
# 1. Initialise Adrian.  This auto-instruments Anthropic by default.
# ------------------------------------------------------------------
adrian.init(
    api_key=os.environ.get("ADRIAN_API_KEY", ""),
    session_id="anthropic-quickstart-session",
)

# ------------------------------------------------------------------
# 2. Create an Anthropic client as normal.
# ------------------------------------------------------------------
client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


async def main() -> None:
    print("Sending first request...")

    # ------------------------------------------------------------------
    # 3. Wrap related calls in an invocation context so Adrian groups them.
    # ------------------------------------------------------------------
    async with adrian.anthropic_invocation():
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system="You are a concise assistant.",
            messages=[{"role": "user", "content": "What is 2 + 2? Answer in one sentence."}],
        )

        text = next(
            (block.text for block in response.content if hasattr(block, "text")),
            "",
        )
        print(f"Model says: {text}")

        # A second call in the same invocation -- same invocation_id in Adrian.
        follow_up = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system="You are a concise assistant.",
            messages=[
                {"role": "user", "content": "What is 2 + 2? Answer in one sentence."},
                {"role": "assistant", "content": text},
                {"role": "user", "content": "Now multiply that result by 10."},
            ],
        )

        follow_text = next(
            (block.text for block in follow_up.content if hasattr(block, "text")),
            "",
        )
        print(f"Follow-up: {follow_text}")

    # ------------------------------------------------------------------
    # 4. Always shut down Adrian cleanly to flush any pending events.
    # ------------------------------------------------------------------
    await adrian.shutdown()
    print("Done.  Check your Adrian dashboard for the captured events.")


if __name__ == "__main__":
    asyncio.run(main())
