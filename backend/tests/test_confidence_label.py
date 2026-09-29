"""The confidence label has to mean something about whether the pick lands.

It used to be data quality plus model agreement. Agreement turned out to
carry no information -- on 3,717 held-out matches the top pick came in ~50%
of the time whatever the agreement, and 98% of matches cleared the HIGH bar
-- so HIGH was the default label for any mid-season fixture and the public
track record would have shown "HIGH confidence" landing half the time. The
top pick's probability is what separates ~40% from ~70% (app.quality has the
measured bands), so it now decides the band, with the old factors as gates.
"""

from __future__ import annotations

from app.quality import HIGH_CONFIDENCE_PROBABILITY, MEDIUM_CONFIDENCE_PROBABILITY, confidence_label

GOOD_DATA = 1.0
GOOD_AGREEMENT = 0.95


def test_a_close_call_is_not_high_confidence_however_good_the_data():
    assert confidence_label(GOOD_DATA, GOOD_AGREEMENT, top_probability=0.42) == "LOW"
    assert confidence_label(GOOD_DATA, GOOD_AGREEMENT, top_probability=0.52) == "MEDIUM"


def test_a_strong_favourite_with_good_data_is_high():
    assert confidence_label(GOOD_DATA, GOOD_AGREEMENT, top_probability=0.68) == "HIGH"


def test_thresholds_are_inclusive():
    assert confidence_label(GOOD_DATA, GOOD_AGREEMENT, top_probability=HIGH_CONFIDENCE_PROBABILITY) == "HIGH"
    assert confidence_label(GOOD_DATA, GOOD_AGREEMENT, top_probability=MEDIUM_CONFIDENCE_PROBABILITY) == "MEDIUM"


def test_thin_history_still_caps_the_band():
    assert confidence_label(0.7, GOOD_AGREEMENT, top_probability=0.75) == "MEDIUM"
    assert confidence_label(0.3, GOOD_AGREEMENT, top_probability=0.75) == "LOW"


def test_models_that_disagree_still_cap_the_band():
    assert confidence_label(GOOD_DATA, 0.7, top_probability=0.75) == "MEDIUM"
    assert confidence_label(GOOD_DATA, 0.5, top_probability=0.75) == "LOW"


def test_without_a_probability_the_old_label_is_unchanged():
    assert confidence_label(GOOD_DATA, GOOD_AGREEMENT) == "HIGH"
    assert confidence_label(0.7, 0.7) == "MEDIUM"
    assert confidence_label(0.3, GOOD_AGREEMENT) == "LOW"
