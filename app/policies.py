"""쇼핑몰 운영 정책 문서와 검색."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyArticle:
    id: str
    title: str
    category: str
    text: str
    keywords: tuple[str, ...]


POLICIES: list[PolicyArticle] = [
    PolicyArticle("REFUND-1", "환불 정책 1조", "환불",
                  "상품 발송 전(배송준비 상태)에는 사유와 관계없이 전액 환불을 요청할 수 있다.",
                  ("환불", "취소", "발송 전", "배송준비")),
    PolicyArticle("REFUND-2", "환불 정책 2조", "환불",
                  "배송 완료 후 7일 이내이고 상품이 미개봉 상태이면 환불할 수 있다. 단순 변심일 때 왕복 배송비 6,000원은 고객이 부담한다.",
                  ("환불", "반품", "미개봉", "배송완료", "변심", "배송비")),
    PolicyArticle("REFUND-3", "환불 정책 3조", "환불",
                  "배송 중인 상품은 환불 요청을 접수할 수 있으나, 상품 수령 후 반품 절차를 거쳐 환불한다.",
                  ("환불", "배송중", "반품")),
    PolicyArticle("REFUND-4", "환불 정책 4조", "환불",
                  "상품 불량이나 오배송으로 인한 환불은 배송비를 판매자가 부담하며 수령 후 30일 이내 요청할 수 있다.",
                  ("환불", "불량", "파손", "오배송", "잘못")),
    PolicyArticle("EXCHANGE-1", "교환 정책 1조", "교환",
                  "색상·사이즈 교환은 수령 후 7일 이내, 미사용 상태에서 가능하다. 단순 변심 교환의 왕복 배송비 6,000원은 고객이 부담한다.",
                  ("교환", "색상", "사이즈", "변경")),
    PolicyArticle("EXCHANGE-2", "교환 정책 2조", "교환",
                  "교환 상품은 회수된 상품을 확인한 뒤 1~2영업일 안에 발송한다. 교환하려는 옵션이 품절이면 환불로 안내한다.",
                  ("교환", "품절", "재발송", "회수")),
    PolicyArticle("SHIP-1", "배송 정책 1조", "배송",
                  "결제 완료 후 2영업일 안에 발송한다. 발송 후 도착까지 보통 1~3영업일이 걸린다.",
                  ("배송", "발송", "도착", "언제")),
    PolicyArticle("SHIP-2", "배송 정책 2조", "배송",
                  "배송준비 상태가 3영업일을 넘기면 지연으로 보고, 고객에게 먼저 지연 사유와 예상 발송일을 안내한다.",
                  ("배송", "지연", "늦", "배송준비", "소식")),
    PolicyArticle("SHIP-3", "배송 정책 3조", "배송",
                  "배송완료로 표시되었으나 고객이 받지 못한 경우, 수령 장소(경비실·문 앞·택배함)를 먼저 안내하고 택배사에 분실 조회를 요청한다. 분실이 확인되면 재발송 또는 환불한다.",
                  ("배송완료", "못 받", "분실", "안 왔", "도착 안")),
    PolicyArticle("CS-1", "응대 원칙 1조", "기타",
                  "확인되지 않은 날짜·금액·처리 결과는 약속하지 않는다. 확인이 필요한 사항은 확인 후 다시 안내한다고 답한다.",
                  ("응대", "약속", "확인")),
]

_BY_ID = {p.id: p for p in POLICIES}


def get_policy(policy_id: str) -> PolicyArticle | None:
    return _BY_ID.get(policy_id)


def search(query: str, category: str | None = None, limit: int = 3) -> list[PolicyArticle]:
    query = (query or "").lower()
    scored: list[tuple[int, PolicyArticle]] = []
    for article in POLICIES:
        score = sum(2 for kw in article.keywords if kw.lower() in query)
        score += sum(1 for word in query.split() if len(word) >= 2 and word in article.text.lower())
        if category and article.category == category:
            score += 3
        if score > 0:
            scored.append((score, article))
    scored.sort(key=lambda pair: (-pair[0], pair[1].id))
    results = [article for _, article in scored[:limit]]
    if not any(a.id == "CS-1" for a in results):
        results.append(_BY_ID["CS-1"])
    return results
