# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""Review status and rating of the work-quality page (pure parts; the API is checked end to end in Django)."""

import pytest

pytest.importorskip("django")

from cvat.apps.qc3d.quality import rating, review_status  # noqa: E402


@pytest.mark.parametrize(
    "stage, state, has_qc, expected",
    [
        ("annotation", "new", False, "in_progress"),
        ("annotation", "in progress", True, "in_progress"),
        ("annotation", "completed", False, "waiting_review"),
        ("annotation", "rejected", True, "returned"),
        ("validation", "new", False, "waiting_review"),
        ("validation", "new", True, "in_review"),
        ("validation", "in progress", False, "in_review"),
        ("validation", "completed", True, "reviewed"),
        ("validation", "rejected", True, "returned"),
        ("acceptance", "in progress", True, "in_acceptance"),
        ("acceptance", "completed", True, "accepted"),
    ],
)
def test_review_status(stage, state, has_qc, expected):
    assert review_status(stage, state, has_qc) == expected


def test_rating_bands():
    assert rating(None) is None
    assert rating(0.99)["key"] == "excellent"
    assert rating(0.96)["key"] == "good"
    assert rating(0.91)["key"] == "pass"
    assert rating(0.5)["key"] == "improve"
