# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""Tests for adrian.pii._engine, redact_text standalone function."""

from __future__ import annotations

from adrian.pii._engine import PiiConfig, redact_text
from adrian.pii._patterns import PiiType
from adrian.pii._strategies import RedactionStrategy


class TestRedactTextBasic:
    def test_no_pii(self) -> None:
        result = redact_text("Hello, world!")
        assert result.text == "Hello, world!"
        assert result.detections == []

    def test_empty_string(self) -> None:
        result = redact_text("")
        assert result.text == ""
        assert result.detections == []

    def test_single_email_replace(self) -> None:
        result = redact_text("contact user@example.com now")
        assert "[EMAIL_REDACTED]" in result.text
        assert "user@example.com" not in result.text
        assert len(result.detections) == 1
        assert result.detections[0].pii_type == PiiType.EMAIL

    def test_multiple_pii_types(self) -> None:
        text = "email user@test.com or call 555-123-4567"
        result = redact_text(text)
        assert "[EMAIL_REDACTED]" in result.text
        assert "[PHONE_REDACTED]" in result.text
        assert len(result.detections) == 2


class TestRedactTextStrategies:
    def test_replace_strategy(self) -> None:
        cfg = PiiConfig(strategy=RedactionStrategy.REPLACE)
        result = redact_text("SSN: 123-45-6789", cfg)
        assert "[SSN_REDACTED]" in result.text

    def test_mask_strategy_email(self) -> None:
        cfg = PiiConfig(strategy=RedactionStrategy.MASK)
        result = redact_text("email: john@example.com", cfg)
        assert "j***@***.com" in result.text

    def test_mask_strategy_phone(self) -> None:
        cfg = PiiConfig(strategy=RedactionStrategy.MASK)
        result = redact_text("phone: 555-123-4567", cfg)
        assert "***-***-4567" in result.text

    def test_mask_strategy_ssn(self) -> None:
        cfg = PiiConfig(strategy=RedactionStrategy.MASK)
        result = redact_text("SSN: 123-45-6789", cfg)
        assert "***-**-6789" in result.text

    def test_hash_strategy(self) -> None:
        cfg = PiiConfig(strategy=RedactionStrategy.HASH)
        result = redact_text("email: user@example.com", cfg)
        assert result.text.startswith("email: [EMAIL:")
        assert len(result.detections) == 1

    def test_hash_is_deterministic(self) -> None:
        cfg = PiiConfig(strategy=RedactionStrategy.HASH)
        r1 = redact_text("user@example.com", cfg)
        r2 = redact_text("user@example.com", cfg)
        assert r1.text == r2.text


class TestRedactTextConfig:
    def test_enabled_types_filter(self) -> None:
        cfg = PiiConfig(enabled_types=frozenset({PiiType.EMAIL}))
        text = "email user@test.com phone 555-123-4567"
        result = redact_text(text, cfg)
        assert "[EMAIL_REDACTED]" in result.text
        assert "555-123-4567" in result.text  # phone not redacted

    def test_no_mutation_of_input(self) -> None:
        original = "email: user@test.com"
        _ = redact_text(original)
        assert original == "email: user@test.com"


class TestRedactionResult:
    def test_detections_sorted_by_position(self) -> None:
        text = "phone 555-123-4567 email user@test.com"
        result = redact_text(text)
        positions = [d.start for d in result.detections]
        assert positions == sorted(positions)

    def test_detection_offsets_match_original(self) -> None:
        text = "hi user@example.com bye"
        result = redact_text(text)
        det = result.detections[0]
        assert text[det.start : det.end] == det.text
