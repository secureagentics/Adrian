# Adrian for the Anthropic SDK

Anthropic SDK instrumentation for [Adrian](https://github.com/secureagentics/Adrian) security monitoring. Every `messages.create` and `messages.stream` call is captured as a `PairedEvent` and streamed to your backend. Your call sites stay unchanged.

## Install

```sh
pip install "adrian-sdk[anthropic]"
```

Requires Python 3.12+. The extra pins a supported `anthropic` version. Plain `pip install adrian-sdk` also works, since the instrumentation patches whichever `anthropic` your project already depends on. If the package is absent, Adrian skips Anthropic patching and everything else continues as normal.

## Usage

`init` and `shutdown` bracket your normal Anthropic code:

```python
import asyncio
import os

import adrian
import anthropic


async def main():
    adrian.init(api_key="adr_local_...")

    # Your Anthropic code runs normally, and every call is captured.
    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    async with adrian.anthropic_invocation():
        response = await client.messages.create(
            model="claude-opus-5",
            max_tokens=1024,
            system="You are a helpful assistant.",
            messages=[{"role": "user", "content": "What is 2 + 2?"}],
        )
        # Thinking blocks can precede the text block, so select by type.
        print(next(b.text for b in response.content if b.type == "text"))

    adrian.shutdown()


asyncio.run(main())
```

Both `anthropic.Anthropic` and `anthropic.AsyncAnthropic` are instrumented. For synchronous code use `adrian.anthropic_invocation_sync()`. Backend configuration, handlers, and the `PairedEvent` schema are shared with the rest of the SDK and are covered in the [SDK README](README.md).

<sup>Last verified with `anthropic==0.96.0` (2026-08-11).</sup>

## Grouping related calls

An invocation is Adrian's unit of work. A single Anthropic call is not one, so wrap related calls to group them under a shared `invocation_id`:

```python
async with adrian.anthropic_invocation():
    first = await client.messages.create(...)
    second = await client.messages.create(...)   # same invocation_id
```

Calls made outside an invocation are still captured, but carry `invocation_id="no_invocation"` and cannot be correlated with each other.

## Streaming

Text deltas stream through untouched. The event is emitted when the final message is requested:

```python
async with adrian.anthropic_invocation():
    async with client.messages.stream(
        model="claude-opus-5",
        max_tokens=1024,
        messages=[{"role": "user", "content": "Count to five."}],
    ) as stream:
        async for text in stream.text_stream:
            print(text, end="", flush=True)

        message = await stream.get_final_message()   # emitted and gated here
```

## Execution modes

The agent profile's execution mode is set in the dashboard and pushed to the SDK in the `LoginAck` frame. In Alert mode the response passes through unchanged. In Block and Human Review modes each `tool_use` block in the response waits on the classifier verdict before the response is returned to your code, so a halted tool call never reaches your execution loop. Halted blocks are rewritten to a text block reading `[BLOCKED by security policy]`, and `stop_reason` is downgraded from `tool_use` to `end_turn` so agentic loops terminate cleanly.

The gate fails closed. If no `LoginAck` arrives within 5s, all tool calls are blocked, and in Block mode a verdict timeout blocks the tool call.

## Manual instrumentation

`init()` patches the Anthropic SDK automatically. To control when that happens:

```python
adrian.init(api_key="adr_local_...", auto_instrument=False)
adrian.patch_anthropic()
```

Patching is idempotent and safe to call more than once.

## Not yet covered

- `client.beta.messages` still being a beta feature is not instrumented
- Reasoning content is not captured. A summarised version is available and will be implemented in a follow-up PR.

## Licence

Apache-2.0
