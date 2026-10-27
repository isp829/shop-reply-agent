"""답장 문장별 근거 검증.

답장의 각 문장에서 확인 가능한 사실(금액, 날짜, 기간, 주문번호, 상태, 정책 조항)을 뽑아
에이전트가 실제로 조회한 결과(근거)에 있는지 대조한다. 근거에 없는 사실이 있는 문장은
"근거 없음"으로 표시해 운영자가 확정 전에 확인하게 한다.
"""

from __future__ import annotations

import re

_SPLIT = re.compile(r"(?<=[.!?。])\s+|\n+")

_FACT_PATTERNS = [
    re.compile(r"\d{1,3}(?:,\d{3})+\s?원|\d+\s?원"),
    re.compile(r"\d{4}-\d{2}-\d{2}"),
    re.compile(r"\d{1,2}월\s?\d{1,2}일"),
    re.compile(r"\d+\s?영업일"),
    re.compile(r"(?<![\d월~])\d+\s?일"),
    re.compile(r"ORD-\d{4}"),
    re.compile(r"배송준비|배송중|배송완료|환불완료"),
    re.compile(r"[가-힣]+\s?정책\s?\d+\s?조"),
]

_REFUND_DONE = re.compile(r"환불[이가을를은는]?\s*(?:모두\s*)?(?:완료|처리)\s*(?:되었|됐|했|하였)")


def _norm(value: str) -> str:
    return re.sub(r"[\s,]", "", value)


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(text or "") if s and s.strip()]


def extract_facts(sentence: str) -> list[str]:
    facts: list[str] = []
    for pattern in _FACT_PATTERNS:
        for match in pattern.findall(sentence):
            if match not in facts:
                facts.append(match)
    # "10월 26일" 안의 "26일"처럼 다른 사실에 포함된 조각은 뺀다
    return [f for f in facts if not any(f != other and f in other for other in facts)]


def _date_variants(fact: str) -> list[str]:
    """'10월 20일' 같은 표기는 근거의 YYYY-MM-DD와도 대조한다."""
    m = re.match(r"(\d{1,2})월\s?(\d{1,2})일", fact)
    if not m:
        return [fact]
    month, day = int(m.group(1)), int(m.group(2))
    return [fact, f"-{month:02d}-{day:02d}"]


def verify(reply: str, evidence_texts: list[str], refund_approved: bool) -> list[dict]:
    corpus = _norm(" ".join(evidence_texts))
    results: list[dict] = []
    for sentence in split_sentences(reply):
        facts = extract_facts(sentence)
        missing = [f for f in facts if not any(_norm(v) in corpus for v in _date_variants(f))]
        reasons: list[str] = []
        if missing:
            reasons.append("조회 결과에서 확인되지 않음: " + ", ".join(missing))
        if not refund_approved and _REFUND_DONE.search(sentence):
            reasons.append("승인되지 않은 환불을 완료로 안내함")
        if reasons:
            status = "unsupported"
        elif facts:
            status = "supported"
        else:
            status = "general"
        results.append({"sentence": sentence, "status": status, "facts": facts, "missing": missing, "reasons": reasons})
    return results


def summary(checks: list[dict]) -> dict[str, int]:
    out = {"supported": 0, "unsupported": 0, "general": 0}
    for check in checks:
        out[check["status"]] = out.get(check["status"], 0) + 1
    return out
