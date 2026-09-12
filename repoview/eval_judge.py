from repoview.agent.llm import LLM


def build_judge_prompt(
    question: str, expected_finding: str, review: str, *, is_negative: bool
) -> str:
    if is_negative:
        return f"""아래 코드 리뷰가 실제로는 존재하지 않는 다음 문제를 있다고 잘못 주장했는지 판정하세요.

[질문] {question}
[실제로는 존재하지 않는 문제] {expected_finding}
[리뷰 결과]
{review}

리뷰가 이 문제가 있다고 주장했다면 "YES", 없다고 판단했거나 언급하지 않았다면 "NO"라고
첫 줄에 정확히 쓰고, 다음 줄에 판단 이유를 한 문장으로 쓰세요."""

    return f"""아래 코드 리뷰가 다음 문제를 실제로 발견했는지 판정하세요.

[질문] {question}
[찾아야 할 문제] {expected_finding}
[리뷰 결과]
{review}

리뷰가 이 문제를 정확히 발견했다면 "YES", 발견하지 못했다면 "NO"라고 첫 줄에 정확히 쓰고,
다음 줄에 판단 이유를 한 문장으로 쓰세요."""


def judge_case(
    judge_llm: LLM,
    question: str,
    expected_finding: str,
    review: str,
    *,
    is_negative: bool,
) -> tuple[bool, str]:
    prompt = build_judge_prompt(question, expected_finding, review, is_negative=is_negative)
    response = judge_llm.call([{"role": "user", "content": prompt}], [])
    return _parse_verdict(response.text or "")


def _parse_verdict(text: str) -> tuple[bool, str]:
    lines = text.strip().splitlines()
    if not lines:
        return False, ""
    verdict = lines[0].strip().upper().startswith("YES")
    reason = "\n".join(line.strip() for line in lines[1:]).strip()
    return verdict, reason
