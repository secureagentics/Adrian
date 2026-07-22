# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""Tests for adrian.pii._patterns, per-PII-type detection."""

from __future__ import annotations

from adrian.pii._patterns import PiiType, detect

# ------------------------------------------------------------------
# EMAIL
# ------------------------------------------------------------------


class TestEmailDetection:
    def test_standard_email(self) -> None:
        dets = detect("email: user@example.com", frozenset({PiiType.EMAIL}))
        assert len(dets) == 1
        assert dets[0].text == "user@example.com"

    def test_plus_addressing(self) -> None:
        dets = detect("user+tag@example.com", frozenset({PiiType.EMAIL}))
        assert len(dets) == 1

    def test_subdomain(self) -> None:
        dets = detect("a@sub.domain.co.uk", frozenset({PiiType.EMAIL}))
        assert len(dets) == 1

    def test_no_match_bare_at(self) -> None:
        dets = detect("@ is a symbol", frozenset({PiiType.EMAIL}))
        assert len(dets) == 0

    def test_no_match_no_tld(self) -> None:
        dets = detect("user@localhost", frozenset({PiiType.EMAIL}))
        assert len(dets) == 0


# ------------------------------------------------------------------
# PHONE
# ------------------------------------------------------------------


class TestPhoneDetection:
    def test_dashes(self) -> None:
        dets = detect("call 555-123-4567", frozenset({PiiType.PHONE}))
        assert len(dets) == 1
        assert dets[0].text == "555-123-4567"

    def test_parens(self) -> None:
        dets = detect("(555) 123-4567", frozenset({PiiType.PHONE}))
        assert len(dets) == 1

    def test_dots(self) -> None:
        dets = detect("555.123.4567", frozenset({PiiType.PHONE}))
        assert len(dets) == 1

    def test_international(self) -> None:
        dets = detect("+1-555-123-4567", frozenset({PiiType.PHONE}))
        assert len(dets) == 1

    def test_too_short(self) -> None:
        dets = detect("555-1234", frozenset({PiiType.PHONE}))
        assert len(dets) == 0


# ------------------------------------------------------------------
# SSN
# ------------------------------------------------------------------


class TestSsnDetection:
    def test_dashes(self) -> None:
        dets = detect("SSN: 123-45-6789", frozenset({PiiType.SSN}))
        assert len(dets) == 1
        assert dets[0].text == "123-45-6789"

    def test_spaces(self) -> None:
        dets = detect("123 45 6789", frozenset({PiiType.SSN}))
        assert len(dets) == 1

    def test_invalid_area_000(self) -> None:
        dets = detect("000-12-3456", frozenset({PiiType.SSN}))
        assert len(dets) == 0

    def test_invalid_area_666(self) -> None:
        dets = detect("666-12-3456", frozenset({PiiType.SSN}))
        assert len(dets) == 0

    def test_invalid_area_9xx(self) -> None:
        dets = detect("900-12-3456", frozenset({PiiType.SSN}))
        assert len(dets) == 0


# ------------------------------------------------------------------
# CREDIT_CARD
# ------------------------------------------------------------------


class TestCreditCardDetection:
    def test_visa(self) -> None:
        # 4111111111111111 is a valid Luhn test number
        dets = detect("card: 4111-1111-1111-1111", frozenset({PiiType.CREDIT_CARD}))
        assert len(dets) == 1

    def test_mastercard(self) -> None:
        dets = detect("5500 0000 0000 0004", frozenset({PiiType.CREDIT_CARD}))
        assert len(dets) == 1

    def test_amex(self) -> None:
        dets = detect("3782 822463 10005", frozenset({PiiType.CREDIT_CARD}))
        assert len(dets) == 1

    def test_luhn_invalid(self) -> None:
        dets = detect("4111-1111-1111-1112", frozenset({PiiType.CREDIT_CARD}))
        assert len(dets) == 0


# ------------------------------------------------------------------
# IP_ADDRESS, only private/loopback/link-local should fire
# ------------------------------------------------------------------


class TestIpAddressDetection:
    def test_private_192_168(self) -> None:
        dets = detect("server at 192.168.1.100", frozenset({PiiType.IP_ADDRESS}))
        assert len(dets) == 1
        assert dets[0].text == "192.168.1.100"

    def test_private_10_x(self) -> None:
        dets = detect("internal 10.0.0.5", frozenset({PiiType.IP_ADDRESS}))
        assert len(dets) == 1

    def test_private_172_16(self) -> None:
        dets = detect("box 172.16.5.42", frozenset({PiiType.IP_ADDRESS}))
        assert len(dets) == 1

    def test_loopback(self) -> None:
        dets = detect("dev loop 127.0.0.1", frozenset({PiiType.IP_ADDRESS}))
        assert len(dets) == 1

    def test_link_local(self) -> None:
        dets = detect("ll 169.254.1.1", frozenset({PiiType.IP_ADDRESS}))
        assert len(dets) == 1

    def test_public_not_flagged(self) -> None:
        dets = detect("dns 8.8.8.8", frozenset({PiiType.IP_ADDRESS}))
        assert len(dets) == 0

    def test_public_cloudflare_not_flagged(self) -> None:
        dets = detect("api at 1.1.1.1", frozenset({PiiType.IP_ADDRESS}))
        assert len(dets) == 0

    def test_invalid_ipv4_octet(self) -> None:
        dets = detect("256.1.1.1", frozenset({PiiType.IP_ADDRESS}))
        assert len(dets) == 0

    def test_ipv6_loopback(self) -> None:
        dets = detect("addr ::1", frozenset({PiiType.IP_ADDRESS}))
        assert len(dets) == 1

    def test_ipv6_link_local(self) -> None:
        dets = detect("fe80::1234", frozenset({PiiType.IP_ADDRESS}))
        assert len(dets) == 1

    def test_ipv6_public_not_flagged(self) -> None:
        # Google Public DNS (globally routable, not in any private range).
        # The 2001:db8::/32 documentation range is marked is_private=True
        # by Python's ipaddress module, so we can't use it here.
        dets = detect(
            "2001:4860:4860::8888",
            frozenset({PiiType.IP_ADDRESS}),
        )
        assert len(dets) == 0


# ------------------------------------------------------------------
# DATE_OF_BIRTH (context-dependent, before+after window)
# ------------------------------------------------------------------


class TestDateOfBirthDetection:
    def test_with_dob_context(self) -> None:
        dets = detect("born on 01/15/1990", frozenset({PiiType.DATE_OF_BIRTH}))
        assert len(dets) == 1
        assert dets[0].text == "01/15/1990"

    def test_with_birthday_context(self) -> None:
        dets = detect("birthday: January 15, 1990", frozenset({PiiType.DATE_OF_BIRTH}))
        assert len(dets) == 1

    def test_iso_format_with_context(self) -> None:
        dets = detect("DOB 1990-01-15", frozenset({PiiType.DATE_OF_BIRTH}))
        assert len(dets) == 1

    def test_trailing_context(self) -> None:
        dets = detect("1990-01-15 (date of birth)", frozenset({PiiType.DATE_OF_BIRTH}))
        assert len(dets) == 1

    def test_no_context_no_match(self) -> None:
        dets = detect("meeting on 01/15/1990", frozenset({PiiType.DATE_OF_BIRTH}))
        assert len(dets) == 0


# ------------------------------------------------------------------
# IBAN
# ------------------------------------------------------------------


class TestIbanDetection:
    def test_german_iban(self) -> None:
        dets = detect("IBAN: DE89 3704 0044 0532 0130 00", frozenset({PiiType.IBAN}))
        assert len(dets) == 1

    def test_uk_iban(self) -> None:
        dets = detect("GB29 NWBK 6016 1331 9268 19", frozenset({PiiType.IBAN}))
        assert len(dets) == 1


# ------------------------------------------------------------------
# PASSPORT
# ------------------------------------------------------------------


class TestPassportDetection:
    def test_with_prefix(self) -> None:
        dets = detect("passport number: 123456789", frozenset({PiiType.PASSPORT}))
        assert len(dets) == 1
        assert dets[0].text == "123456789"

    def test_with_hash(self) -> None:
        dets = detect("passport #987654321", frozenset({PiiType.PASSPORT}))
        assert len(dets) == 1

    def test_underscore_label(self) -> None:
        dets = detect("passport_number: 555444333", frozenset({PiiType.PASSPORT}))
        assert len(dets) == 1

    def test_without_prefix(self) -> None:
        dets = detect("the number 123456789 is mine", frozenset({PiiType.PASSPORT}))
        assert len(dets) == 0


# ------------------------------------------------------------------
# STREET_ADDRESS
# ------------------------------------------------------------------


class TestStreetAddressDetection:
    def test_basic_street(self) -> None:
        dets = detect("lives at 123 Main Street", frozenset({PiiType.STREET_ADDRESS}))
        assert len(dets) == 1

    def test_with_apt(self) -> None:
        dets = detect("456 Oak Ave Apt 12", frozenset({PiiType.STREET_ADDRESS}))
        assert len(dets) == 1

    def test_drive(self) -> None:
        dets = detect("789 Elm Drive", frozenset({PiiType.STREET_ADDRESS}))
        assert len(dets) == 1


# ------------------------------------------------------------------
# POSTAL_CODE, US (zip/postal keyword required) + UK
# ------------------------------------------------------------------


class TestUsPostalCodeDetection:
    def test_with_zip_keyword(self) -> None:
        dets = detect("zip code 90210", frozenset({PiiType.POSTAL_CODE}))
        assert len(dets) == 1
        assert dets[0].text == "90210"

    def test_zip_plus_four(self) -> None:
        dets = detect("postal code 90210-1234", frozenset({PiiType.POSTAL_CODE}))
        assert len(dets) == 1

    def test_no_keyword_no_match(self) -> None:
        dets = detect("counted 12345 items", frozenset({PiiType.POSTAL_CODE}))
        assert len(dets) == 0

    def test_state_code_no_longer_triggers(self) -> None:
        # The buggy state-code branch used to false-positive on
        # "in 90210" because IGNORECASE matched IN (Indiana).
        dets = detect("born in 90210 last year", frozenset({PiiType.POSTAL_CODE}))
        assert len(dets) == 0

    def test_no_false_positive_on_or(self) -> None:
        dets = detect("or 12345 items left", frozenset({PiiType.POSTAL_CODE}))
        assert len(dets) == 0


class TestUkPostcodeDetection:
    def test_sw1a_1aa(self) -> None:
        dets = detect("address SW1A 1AA London", frozenset({PiiType.POSTAL_CODE}))
        assert len(dets) == 1
        assert dets[0].text.upper().replace(" ", "") == "SW1A1AA"

    def test_ec1a_1bb(self) -> None:
        dets = detect("EC1A 1BB", frozenset({PiiType.POSTAL_CODE}))
        assert len(dets) == 1

    def test_m1_1aa(self) -> None:
        dets = detect("Manchester M1 1AA", frozenset({PiiType.POSTAL_CODE}))
        assert len(dets) == 1

    def test_no_space(self) -> None:
        dets = detect("postcode M11AA", frozenset({PiiType.POSTAL_CODE}))
        assert len(dets) == 1

    def test_gir_0aa(self) -> None:
        dets = detect("send to GIR 0AA", frozenset({PiiType.POSTAL_CODE}))
        assert len(dets) == 1

    def test_lowercase(self) -> None:
        dets = detect("address sw1a 1aa", frozenset({PiiType.POSTAL_CODE}))
        assert len(dets) == 1

    def test_invalid_first_digit_rejected(self) -> None:
        # Real UK postcodes never start with a digit.
        dets = detect("12 3AB widgets", frozenset({PiiType.POSTAL_CODE}))
        assert len(dets) == 0

    def test_invalid_inward_letter_rejected(self) -> None:
        # M is excluded from the inward letter set.
        dets = detect("JK 4PM something", frozenset({PiiType.POSTAL_CODE}))
        assert len(dets) == 0


# ------------------------------------------------------------------
# DRIVER_LICENSE, tightened: \b on DL, separator required
# ------------------------------------------------------------------


class TestDriverLicenseDetection:
    def test_with_full_phrase(self) -> None:
        dets = detect(
            "driver's license: D12345678", frozenset({PiiType.DRIVER_LICENSE})
        )
        assert len(dets) == 1
        assert dets[0].text == "D12345678"

    def test_uk_spelling(self) -> None:
        dets = detect("driver licence #ABC123456", frozenset({PiiType.DRIVER_LICENSE}))
        assert len(dets) == 1

    def test_dl_abbreviation(self) -> None:
        dets = detect("DL: AB12345", frozenset({PiiType.DRIVER_LICENSE}))
        assert len(dets) == 1

    def test_dl_inside_word_does_not_match(self) -> None:
        # The old regex matched "dl" inside "kindle" with IGNORECASE.
        dets = detect("kindle ABC12345 device", frozenset({PiiType.DRIVER_LICENSE}))
        assert len(dets) == 0

    def test_zero_separator_rejected(self) -> None:
        # The old regex captured "ABC12345" from "DLABC12345".
        dets = detect("DLABC12345", frozenset({PiiType.DRIVER_LICENSE}))
        assert len(dets) == 0


# ------------------------------------------------------------------
# AWS_KEY
# ------------------------------------------------------------------


class TestAwsKeyDetection:
    def test_valid_akia(self) -> None:
        dets = detect("key: AKIAIOSFODNN7EXAMPLE", frozenset({PiiType.AWS_KEY}))
        assert len(dets) == 1

    def test_partial(self) -> None:
        dets = detect("AKIA123", frozenset({PiiType.AWS_KEY}))
        assert len(dets) == 0


# ------------------------------------------------------------------
# Removed types stay removed
# ------------------------------------------------------------------


class TestRemovedTypes:
    def test_url_type_gone(self) -> None:
        assert not hasattr(PiiType, "URL")

    def test_mac_address_type_gone(self) -> None:
        assert not hasattr(PiiType, "MAC_ADDRESS")

    def test_api_key_type_gone(self) -> None:
        assert not hasattr(PiiType, "API_KEY")

    def test_us_passport_renamed(self) -> None:
        assert not hasattr(PiiType, "US_PASSPORT")
        assert hasattr(PiiType, "PASSPORT")

    def test_zip_code_renamed(self) -> None:
        assert not hasattr(PiiType, "ZIP_CODE")
        assert hasattr(PiiType, "POSTAL_CODE")


# ------------------------------------------------------------------
# Overlap resolution
# ------------------------------------------------------------------


class TestOverlapResolution:
    def test_non_overlapping_preserved(self) -> None:
        text = "email: user@test.com phone: 555-123-4567"
        dets = detect(text)
        types = {d.pii_type for d in dets}
        assert PiiType.EMAIL in types
        assert PiiType.PHONE in types

    def test_leftmost_wins_on_overlap(self) -> None:
        # Two patterns that overlap at the same start position:
        # a phone-shaped run inside another phone-shaped run.
        # With leftmost-wins-and-longest-as-tiebreak, only one
        # detection survives per region.
        text = "phone 555-123-4567 done"
        dets = detect(text, frozenset({PiiType.PHONE}))
        assert len(dets) == 1
