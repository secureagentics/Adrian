"""LangGraph adapter for agent identity detection.

Parses the ``langgraph_checkpoint_ns`` (checkpoint namespace) field from
LangGraph callback metadata into a stable agent identity path. The
namespace encodes the graph nesting hierarchy as pipe-delimited segments
like ``"Alice:uuid|agent:uuid"``. We strip the UUIDs and keep the node
names to produce a stable identity like ``"Alice|agent"``.

This adapter is called first by ``identity.derive_agent_id``. If the
metadata doesn't contain LangGraph fields, it returns ``None`` and the
dispatcher tries the next strategy (system prompt hash).
"""

from __future__ import annotations

from adrian.types import CallbackMetadata

_CHECKPOINT_NS_KEY = "langgraph_checkpoint_ns"


def is_langgraph_metadata(metadata: CallbackMetadata) -> bool:
    """Check whether callback metadata contains LangGraph fields.

    Args:
        metadata: Callback metadata dict from a LangChain event.

    Returns:
        True if ``langgraph_checkpoint_ns`` is present.
    """
    return _CHECKPOINT_NS_KEY in metadata


def derive_langgraph_agent_id(metadata: CallbackMetadata) -> str | None:
    """Derive agent identity from LangGraph checkpoint namespace.

    Parses the checkpoint namespace into a pipe-delimited path of node
    names. For example:

        "Alice:550e8400|agent:6ba7b810"  →  "Alice|agent"
        "tools:ae85|tools:bf06|reason:75f3"  →  "tools|tools|reason"
        "reason:7d55"  →  "reason"

    Args:
        metadata: Callback metadata dict containing LangGraph fields.

    Returns:
        Pipe-delimited node path string, or ``None`` if the checkpoint
        namespace key is missing or empty.
    """
    ns = metadata.get(_CHECKPOINT_NS_KEY)

    if not ns or not isinstance(ns, str):
        return None

    segments = ns.split("|")
    node_names = [seg.split(":")[0] for seg in segments if seg]

    if not node_names:
        return None

    return "|".join(node_names)
