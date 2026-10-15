# Copyright (C) CVAT.ai Corporation
#
# SPDX-License-Identifier: MIT

"""
Review / acceptance workflow rules (no Django): python -m pytest cvat/apps/qc3d/tests/test_workflow.py
"""

from cvat.apps.qc3d.qc import workflow_rules as W
from cvat.apps.qc3d.qc.guideline import GuidelineError, acceptance_criteria, builtin_content, normalize

CRITERIA = acceptance_criteria(None)


def ctx(status, roles=(), **changes):
    base = dict(status=status, roles=set(roles), qc_whole_job=True, qc_stale_reason=None, criteria=CRITERIA)
    base.update(changes)
    return W.Context(**base)


def test_status_from_cvat_stage_and_state():
    assert W.job_status("annotation", "new") == "in_progress"
    assert W.job_status("annotation", "in progress") == "in_progress"
    assert W.job_status("annotation", "completed") == "waiting_review"
    assert W.job_status("validation", "in progress") == "in_review"
    assert W.job_status("acceptance", "new") == "waiting_acceptance"
    assert W.job_status("acceptance", "in progress") == "in_acceptance"
    assert W.job_status("acceptance", "completed") == "accepted"
    assert W.job_status("acceptance", "completed", signed_off=True) == "signed_off"
    assert W.job_status("validation", "rejected") == "returned"


def test_every_action_lands_on_the_status_of_its_stage_and_state():
    for name, spec in W.ACTIONS.items():
        assert W.job_status(*spec.stage_state, signed_off=name == "sign_off") == spec.target, name


def test_submit_by_the_annotator_or_lead_only():
    assert W.check("submit", ctx("in_progress", is_annotator=True)) == (0, "")
    assert W.check("submit", ctx("returned", roles={"lead"})) == (0, "")
    assert W.check("submit", ctx("in_progress", roles={"qa"}))[0] == W.FORBIDDEN
    assert W.check("submit", ctx("in_review", is_annotator=True))[0] == W.CONFLICT


def test_review_pass_needs_a_current_whole_job_qc_without_blockers():
    reviewer = {"reviewer"}
    assert W.check("review_pass", ctx("in_review", reviewer)) == (0, "")
    assert W.check("review_pass", ctx("in_review", reviewer, qc_whole_job=False))[0] == W.CONFLICT
    assert W.check("review_pass", ctx("in_review", reviewer, qc_stale_reason="nhãn đã thay đổi"))[0] == W.CONFLICT
    assert W.check("review_pass", ctx("in_review", reviewer, open_blockers=2))[0] == W.CONFLICT
    assert W.check("review_pass", ctx("in_review", {"qa"}))[0] == W.FORBIDDEN


def test_return_and_reject_need_a_note():
    assert W.check("review_return", ctx("in_review", {"reviewer"}))[0] == W.BAD_REQUEST
    assert W.check("review_return", ctx("in_review", {"reviewer"}), note="box lệch") == (0, "")
    assert W.check("reject", ctx("in_acceptance", {"qa"}), note="  ")[0] == W.BAD_REQUEST


def test_sample_size_and_reproducibility():
    frames = list(range(84))
    sample = W.sample_frames(frames, CRITERIA, seed=7)
    assert len(sample) == 9 and sample == sorted(set(sample)) and set(sample) <= set(frames)  # ceil(8.4)
    assert sample == W.sample_frames(frames, CRITERIA, seed=7)
    assert len(W.sample_frames(list(range(10)), CRITERIA, seed=1)) == 5  # min_frames
    assert len(W.sample_frames(list(range(3)), CRITERIA, seed=1)) == 3  # never more than the job


def test_acceptance_evaluation_and_accept_conditions():
    frames = [1, 2]
    half = W.evaluate_acceptance(frames, {"1": {"boxes_checked": 50, "errors": 1}}, CRITERIA, 0)
    assert half["passes"] is None and half["checked_frames"] == 1
    assert W.check("accept", ctx("in_acceptance", {"qa"}, acceptance=half))[0] == W.CONFLICT
    good = W.evaluate_acceptance(frames, {"1": {"boxes_checked": 50, "errors": 1},
                                          "2": {"boxes_checked": 50, "errors": 1}}, CRITERIA, 0)
    assert good["error_rate"] == 0.02 and good["passes"] is True
    assert W.check("accept", ctx("in_acceptance", {"qa"}, acceptance=good)) == (0, "")
    bad = W.evaluate_acceptance(frames, {"1": {"boxes_checked": 50, "errors": 3},
                                         "2": {"boxes_checked": 50, "errors": 0}}, CRITERIA, 0)
    assert bad["passes"] is False
    assert W.check("accept", ctx("in_acceptance", {"qa"}, acceptance=bad))[0] == W.FORBIDDEN
    assert W.check("accept", ctx("in_acceptance", {"lead"}, acceptance=bad))[0] == W.BAD_REQUEST
    assert W.check("accept", ctx("in_acceptance", {"lead"}, acceptance=bad), note="khách chấp nhận",
                   override=True) == (0, "")
    clean = {"1": {"boxes_checked": 9, "errors": 0}, "2": {"boxes_checked": 9, "errors": 0}}
    blocked = W.evaluate_acceptance(frames, clean, CRITERIA, 1)
    assert blocked["passes"] is False  # an open blocker fails the acceptance


def test_available_actions_for_the_ui():
    items = {a["action"]: a for a in W.available(ctx("in_review", {"reviewer"}, open_blockers=1))}
    assert set(items) == {"review_pass", "review_return"}
    assert not items["review_pass"]["enabled"] and "nghiêm trọng" in items["review_pass"]["reason"]
    assert items["review_return"]["enabled"] and items["review_return"]["note_required"]
    bad = W.evaluate_acceptance([1], {"1": {"boxes_checked": 10, "errors": 5}}, CRITERIA, 0)
    lead = {a["action"]: a for a in W.available(ctx("in_acceptance", {"lead"}, acceptance=bad))}
    assert lead["accept"]["enabled"] and lead["accept"]["needs_override"]
    qa = {a["action"]: a for a in W.available(ctx("in_acceptance", {"qa"}, acceptance=bad))}
    assert not qa["accept"]["enabled"]


def test_frame_result_validation():
    assert W.frame_result({"boxes_checked": "25", "errors": 1, "note": "x"}) == {
        "boxes_checked": 25, "errors": 1, "note": "x"}
    for bad in ({"boxes_checked": 2, "errors": 3}, {"boxes_checked": -1, "errors": 0}, {"errors": 1}):
        try:
            W.frame_result(bad)
        except ValueError:
            continue
        raise AssertionError(bad)


def test_guideline_acceptance_criteria():
    content, _ = normalize({**builtin_content(), "acceptance": {"sample_ratio": 0.2, "max_error_rate": 0.05}})
    assert content["acceptance"] == {**CRITERIA, "sample_ratio": 0.2, "max_error_rate": 0.05}
    assert normalize(builtin_content())[0]["acceptance"] == CRITERIA
    for bad in ({"sample_ratio": 0}, {"min_frames": 2.5}, {"max_error_rate": 2}):
        try:
            normalize({**builtin_content(), "acceptance": bad})
        except GuidelineError:
            continue
        raise AssertionError(bad)
