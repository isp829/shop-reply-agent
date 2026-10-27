from app.grounding import extract_facts, summary, verify
from app.policies import search

EVIDENCE = [
    '{"evidence_id": "ORDER", "order_number": "ORD-1007", "amount_text": "47,000원", "order_date": "2026-10-26", '
    '"shipping_status": "배송준비", "days_since_order": "1일"}',
    '[{"evidence_id": "REFUND-1", "title": "환불 정책 1조", "text": "상품 발송 전(배송준비 상태)에는 전액 환불을 요청할 수 있다."}]',
]


def test_supported_facts():
    checks = verify("주문하신 상품은 47,000원이며 현재 배송준비 상태입니다.", EVIDENCE, refund_approved=False)
    assert checks[0]["status"] == "supported"
    assert "47,000원" in checks[0]["facts"]


def test_unsupported_amount_and_date():
    checks = verify("환불 금액은 52,000원이며 10월 30일까지 입금됩니다.", EVIDENCE, refund_approved=False)
    assert checks[0]["status"] == "unsupported"
    assert "52,000원" in checks[0]["missing"]


def test_policy_reference_is_checked():
    ok = verify("환불 정책 1조에 따라 환불을 요청할 수 있습니다.", EVIDENCE, refund_approved=False)
    bad = verify("환불 정책 9조에 따라 처리합니다.", EVIDENCE, refund_approved=False)
    assert ok[0]["status"] == "supported"
    assert bad[0]["status"] == "unsupported"


def test_general_sentence():
    checks = verify("문의해 주셔서 감사합니다.", EVIDENCE, refund_approved=False)
    assert checks[0]["status"] == "general"


def test_refund_completion_needs_approval():
    before = verify("환불이 완료되었습니다.", EVIDENCE, refund_approved=False)
    after = verify("환불이 완료되었습니다.", EVIDENCE, refund_approved=True)
    assert before[0]["status"] == "unsupported"
    assert "승인되지 않은 환불을 완료로 안내함" in before[0]["reasons"]
    assert after[0]["status"] == "general"


def test_korean_date_matches_iso_date():
    checks = verify("10월 26일에 주문하셨습니다.", EVIDENCE, refund_approved=False)
    assert checks[0]["status"] == "supported"


def test_extract_facts_and_summary():
    facts = extract_facts("ORD-1003은 2026-10-20에 주문했고 3영업일이 지났습니다.")
    assert "ORD-1003" in facts and "2026-10-20" in facts and "3영업일" in facts
    counts = summary(verify("감사합니다. 52,000원입니다.", EVIDENCE, False))
    assert counts == {"supported": 0, "unsupported": 1, "general": 1}


def test_policy_search_finds_relevant_articles():
    ids = [a.id for a in search("다른 색상으로 교환하고 싶어요", "교환")]
    assert ids[0].startswith("EXCHANGE")
    assert "CS-1" in ids
