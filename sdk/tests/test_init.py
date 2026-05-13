"""Tests for adrian.init / shutdown and auto-instrumentation."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import adrian
import pytest
from adrian.config import AdrianConfig, get_config, is_initialized
from langchain_core.callbacks.manager import CallbackManager
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.runnables.base import Runnable
from langgraph.prebuilt import ToolNode
from langgraph.pregel import Pregel


@pytest.fixture(autouse=True)  # pyright: ignore[reportUntypedFunctionDecorator]
def _cleanup() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Ensure each test starts with a clean SDK state."""
    yield
    adrian.shutdown()


class TestInit:
    """Tests for adrian.init()."""

    def test_creates_config(self, tmp_path: Path) -> None:
        """init() should create config and mark SDK as initialised."""
        log = tmp_path / "events.jsonl"
        adrian.init(log_file=str(log), auto_instrument=False)

        assert is_initialized()
        cfg = get_config()
        assert cfg.log_file == log

    def test_default_log_file(self, tmp_path: Path) -> None:
        """init() with default log_file should set the default path in config."""
        log = tmp_path / "events.jsonl"
        adrian.init(log_file=str(log), auto_instrument=False)
        adrian.shutdown()

        # Verify the dataclass default itself
        default_cfg = AdrianConfig()
        assert default_cfg.log_file == Path("events.jsonl")

    def test_reads_env_vars(self, tmp_path: Path) -> None:
        """init() should read ADRIAN_LOG_FILE env var."""
        log = tmp_path / "env.jsonl"

        with patch.dict(
            os.environ,
            {"ADRIAN_LOG_FILE": str(log)},
        ):
            adrian.init(auto_instrument=False)

        cfg = get_config()
        assert cfg.log_file == log

    def test_creates_jsonl_file(self, tmp_path: Path) -> None:
        """init() should create the JSONL file on disk."""
        log = tmp_path / "events.jsonl"
        adrian.init(log_file=str(log), auto_instrument=False)

        assert log.exists()


class TestShutdown:
    """Tests for adrian.shutdown()."""

    def test_cleans_up_state(self, tmp_path: Path) -> None:
        """shutdown() should reset global state."""
        log = tmp_path / "events.jsonl"
        adrian.init(log_file=str(log), auto_instrument=False)

        assert is_initialized()
        adrian.shutdown()
        assert not is_initialized()

    def test_idempotent(self) -> None:
        """shutdown() should be safe to call when not initialised."""
        adrian.shutdown()
        adrian.shutdown()


class TestAutoInstrumentation:
    """Tests for monkey-patch guard flags."""

    def test_unique_guard_flags(self, tmp_path: Path) -> None:
        """Each patched class should have its own unique guard flag."""
        log = tmp_path / "events.jsonl"
        adrian.init(log_file=str(log), auto_instrument=True)

        assert getattr(Runnable, "_adrian_patched", False) is True
        assert getattr(CallbackManager, "_adrian_cbm_patched", False) is True
        assert getattr(BaseChatModel, "_adrian_chat_model_patched", False) is True
        assert getattr(Pregel, "_adrian_pregel_patched", False) is True
        assert getattr(ToolNode, "_adrian_tool_node_patched", False) is True
