from __future__ import annotations

from app.models.narrative import SCQA, NarrativePlan, QuestionLadderNode


def _scqa():
    return SCQA(
        situation="Vi khuẩn từng dễ bị tiêu diệt bởi kháng sinh",
        complication="Nay ngày càng nhiều chủng kháng thuốc",
        question="Vì sao vi khuẩn kháng thuốc?",
        answer="Chọn lọc tự nhiên ở cấp độ vi sinh",
    )


def test_valid_ladder_with_last_next_question_null():
    ladder = [
        QuestionLadderNode(
            id="Q1",
            question="Kháng sinh hoạt động thế nào?",
            why_viewer_cares="Ai cũng từng uống kháng sinh",
            partial_answer="Phá vỡ thành tế bào vi khuẩn",
            creates_next_question="Q2",
            information_gap="Vì sao vi khuẩn có thể kháng lại?",
        ),
        QuestionLadderNode(
            id="Q2",
            question="Vì sao vi khuẩn kháng thuốc?",
            why_viewer_cares="Ảnh hưởng trực tiếp tới sức khoẻ",
            partial_answer="Đột biến gen được chọn lọc tự nhiên",
            creates_next_question=None,
            information_gap="Không còn câu hỏi mở tiếp theo",
        ),
    ]
    plan = NarrativePlan(
        central_question="Vì sao vi khuẩn kháng thuốc?",
        scqa=_scqa(),
        opening="MYSTERY_FIRST",
        question_ladder=ladder,
        ti_role="Người điều tra dẫn chuyện",
        ending="Kêu gọi dùng kháng sinh đúng cách",
    )
    assert plan.question_ladder[-1].creates_next_question is None
    assert plan.question_ladder[0].creates_next_question == "Q2"


def test_narrative_plan_round_trip_serialization():
    plan = NarrativePlan(
        central_question="Vì sao vi khuẩn kháng thuốc?",
        scqa=_scqa(),
        opening="QUESTION_FIRST",
        ti_role="Người dẫn chuyện",
        ending="Kết luận",
    )
    dumped = plan.model_dump_json()
    restored = NarrativePlan.model_validate_json(dumped)
    assert restored == plan
