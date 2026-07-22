# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""Tests for adrian.session_persistence, per-cwd session_id storage."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import json
from pathlib import Path

import pytest
from adrian.session_persistence import (
    _config_dir,
    _config_path,
    _cwd_key,
    _read_persisted,
    _write_persisted,
    env_aware_resolve_session_id,
    resolve_session_id,
)


@pytest.fixture(autouse=True)
def _isolated_home(  # pyright: ignore[reportUnusedFunction]
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Point ``Path.home()`` at a per-test tmp dir so we don't touch ~/.adrian/."""
    monkeypatch.setenv("HOME", str(tmp_path))
    # Path.home() reads HOME on POSIX and USERPROFILE on Windows.
    monkeypatch.setenv("USERPROFILE", str(tmp_path))


@pytest.fixture
def cwd(tmp_path: Path) -> Path:
    """A fresh per-test fake project directory."""
    proj = tmp_path / "fake-project"
    proj.mkdir()
    return proj


# ------------------------------------------------------------------
# Path encoding
# ------------------------------------------------------------------


class TestCwdKey:
    def test_strips_leading_slash_to_dash(self, tmp_path: Path) -> None:
        # /tmp/.../fake → -tmp-...-fake (resolve gives an absolute POSIX path).
        key = _cwd_key(tmp_path)
        assert key.startswith("-")
        assert "/" not in key

    def test_distinct_paths_distinct_keys(self, tmp_path: Path) -> None:
        a = tmp_path / "a"
        b = tmp_path / "b"
        a.mkdir()
        b.mkdir()
        assert _cwd_key(a) != _cwd_key(b)


# ------------------------------------------------------------------
# Read / write round-trip
# ------------------------------------------------------------------


class TestReadWrite:
    def test_round_trip(self, cwd: Path) -> None:
        _write_persisted("the-session", cwd)
        assert _read_persisted(cwd) == "the-session"

    def test_read_missing_returns_none(self, cwd: Path) -> None:
        assert _read_persisted(cwd) is None

    def test_read_corrupt_json_returns_none(
        self,
        cwd: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        path = _config_path(cwd)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json")
        with caplog.at_level("WARNING", logger="adrian.session"):
            assert _read_persisted(cwd) is None
        assert any("failed to read" in r.message for r in caplog.records)

    def test_read_missing_session_key_returns_none(self, cwd: Path) -> None:
        path = _config_path(cwd)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"other_field": "x"}))
        assert _read_persisted(cwd) is None

    def test_read_empty_session_string_returns_none(self, cwd: Path) -> None:
        path = _config_path(cwd)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"session_id": ""}))
        assert _read_persisted(cwd) is None

    def test_write_creates_parent_dirs(self, cwd: Path) -> None:
        # _config_dir does not exist yet
        assert not _config_dir(cwd).exists()
        _write_persisted("sid", cwd)
        assert _config_path(cwd).is_file()


# ------------------------------------------------------------------
# resolve_session_id
# ------------------------------------------------------------------


class TestResolveSessionId:
    def test_first_call_generates_and_persists(self, cwd: Path) -> None:
        sid = resolve_session_id(cwd)
        assert sid
        # Same call again returns the same id.
        assert resolve_session_id(cwd) == sid
        # And it's actually on disk.
        on_disk = json.loads(_config_path(cwd).read_text())
        assert on_disk["session_id"] == sid

    def test_existing_file_is_reused(self, cwd: Path) -> None:
        path = _config_path(cwd)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"session_id": "preexisting"}))
        assert resolve_session_id(cwd) == "preexisting"

    def test_distinct_cwds_get_distinct_ids(self, tmp_path: Path) -> None:
        a = tmp_path / "proj-a"
        b = tmp_path / "proj-b"
        a.mkdir()
        b.mkdir()
        assert resolve_session_id(a) != resolve_session_id(b)

    def test_corrupt_file_falls_back_to_fresh(
        self,
        cwd: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        path = _config_path(cwd)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("garbage")

        with caplog.at_level("WARNING", logger="adrian.session"):
            sid = resolve_session_id(cwd)

        assert sid  # non-empty fresh UUID
        # And the corrupt file got overwritten with valid JSON.
        on_disk = json.loads(path.read_text())
        assert on_disk["session_id"] == sid

    def test_write_failure_logs_and_returns_anyway(
        self,
        cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        # Simulate disk-full / readonly home: make mkdir raise.
        def _broken_mkdir(_self: Path, *_args: object, **_kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(Path, "mkdir", _broken_mkdir)

        with caplog.at_level("WARNING", logger="adrian.session"):
            sid = resolve_session_id(cwd)

        assert sid  # caller still gets a usable id
        assert any("failed to persist" in r.message for r in caplog.records)


# ------------------------------------------------------------------
# env_aware_resolve_session_id, full layered chain
# ------------------------------------------------------------------


class TestEnvAwareResolve:
    def test_env_var_wins_over_explicit(
        self,
        cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ADRIAN_SESSION_ID", "from-env")
        assert env_aware_resolve_session_id("explicit-arg", cwd) == "from-env"

    def test_explicit_wins_over_persistent(
        self,
        cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ADRIAN_SESSION_ID", raising=False)
        # Pre-populate persistent file.
        _write_persisted("persisted-id", cwd)
        assert env_aware_resolve_session_id("explicit-arg", cwd) == "explicit-arg"

    def test_persistent_used_when_no_env_or_explicit(
        self,
        cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ADRIAN_SESSION_ID", raising=False)
        _write_persisted("persisted-id", cwd)
        assert env_aware_resolve_session_id(None, cwd) == "persisted-id"

    def test_explicit_does_not_pollute_persistent_file(
        self,
        cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Pre-populate file, then call with explicit override.
        monkeypatch.delenv("ADRIAN_SESSION_ID", raising=False)
        _write_persisted("persisted-id", cwd)
        env_aware_resolve_session_id("explicit-arg", cwd)
        # Persisted file is unchanged, the explicit override didn't write.
        assert _read_persisted(cwd) == "persisted-id"

    def test_env_var_does_not_pollute_persistent_file(
        self,
        cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("ADRIAN_SESSION_ID", "from-env")
        _write_persisted("persisted-id", cwd)
        env_aware_resolve_session_id(None, cwd)
        assert _read_persisted(cwd) == "persisted-id"

    def test_no_input_no_file_creates_persistent(
        self,
        cwd: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("ADRIAN_SESSION_ID", raising=False)
        sid = env_aware_resolve_session_id(None, cwd)
        assert sid
        assert _read_persisted(cwd) == sid
