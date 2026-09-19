"""The welcome email used to claim "registering alone doesn't grant access,"
which stopped being true once the signup trial shipped. These tests pin the
corrected behavior: the body reflects whatever trial_days registration
actually granted, and includes the WhatsApp contact when one is configured.
"""

from __future__ import annotations

from app.mailer import format_whatsapp, welcome_message


def test_format_whatsapp_groups_a_country_code_and_nine_digits():
    assert format_whatsapp("233596909643") == "+233 59 690 9643"


def test_format_whatsapp_falls_back_for_an_unexpected_length():
    assert format_whatsapp("12345") == "+12345"


def test_format_whatsapp_handles_blank():
    assert format_whatsapp("") == ""


def test_welcome_message_describes_the_trial_and_the_contact():
    _subject, body = welcome_message("https://soccaintel.com", trial_days=5, whatsapp="233596909643")
    assert "next 5 days" in body
    assert "free tier" in body
    assert "+233 59 690 9643" in body
    assert "WhatsApp only" in body


def test_welcome_message_singular_day():
    _subject, body = welcome_message(trial_days=1, whatsapp="233596909643")
    assert "next 1 day," in body


def test_welcome_message_without_a_trial_still_mentions_the_free_tier_and_contact():
    _subject, body = welcome_message(trial_days=0, whatsapp="233596909643")
    assert "free tier" in body
    assert "no code needed" not in body
    assert "+233 59 690 9643" in body


def test_welcome_message_omits_contact_line_when_not_configured():
    _subject, body = welcome_message(trial_days=5, whatsapp=None)
    assert "WhatsApp" not in body
