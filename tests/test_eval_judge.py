from repoview.agent.llm import FakeLLM, make_text_response
from repoview.eval_judge import judge_case


def test_judge_case_parses_yes_verdict_and_reason():
    judge_llm = FakeLLM([make_text_response("YES\nN+1 쿼리를 정확히 지적했다")])

    verdict, reason = judge_case(
        judge_llm, "성능 문제 있어?", "N+1 쿼리", "리뷰 본문...", is_negative=False
    )

    assert verdict is True
    assert reason == "N+1 쿼리를 정확히 지적했다"


def test_judge_case_parses_no_verdict():
    judge_llm = FakeLLM([make_text_response("NO\n문제를 언급하지 않았다")])

    verdict, _ = judge_case(judge_llm, "질문", "기대 결과", "리뷰", is_negative=False)

    assert verdict is False


def test_judge_case_is_case_insensitive_on_verdict():
    judge_llm = FakeLLM([make_text_response("yes\n이유")])

    verdict, _ = judge_case(judge_llm, "질문", "기대 결과", "리뷰", is_negative=False)

    assert verdict is True


def test_judge_case_builds_different_prompt_for_negative_case():
    judge_llm = FakeLLM([make_text_response("NO\n주장 없음")])

    judge_case(judge_llm, "질문", "존재하지 않는 문제", "리뷰", is_negative=True)

    sent_prompt = judge_llm.received_messages[0][0]["content"]
    assert "실제로는 존재하지 않는" in sent_prompt


def test_judge_case_handles_missing_reason_line():
    judge_llm = FakeLLM([make_text_response("YES")])

    verdict, reason = judge_case(judge_llm, "질문", "기대", "리뷰", is_negative=False)

    assert verdict is True
    assert reason == ""
