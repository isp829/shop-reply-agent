"""생산성 측정: 문의별 처리 시간을 기획서의 직접 처리 기준값과 비교한다."""

from __future__ import annotations

from typing import Any

from .config import MANUAL_BASELINE_AVERAGE, MANUAL_BASELINE_SECONDS

CATEGORIES = ("배송", "환불", "교환")


def _avg(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


def _reduction(baseline: float, value: float | None) -> float | None:
    if value is None or baseline <= 0:
        return None
    return round((1 - value / baseline) * 100, 1)


def format_seconds(value: float | None) -> str:
    if value is None:
        return "-"
    total = int(round(value))
    minutes, seconds = divmod(total, 60)
    return f"{minutes}분 {seconds:02d}초" if minutes else f"{seconds}초"


def summarize(inquiries: list[dict[str, Any]]) -> dict[str, Any]:
    rows = []
    all_handling: list[float] = []
    all_ai: list[float] = []
    for category in CATEGORIES:
        items = [i for i in inquiries if i.get("category") == category]
        ai = [float(i["ai_seconds"]) for i in items if i.get("ai_seconds") is not None]
        handling = [float(i["handling_seconds"]) for i in items if i.get("handling_seconds") is not None]
        all_ai += ai
        all_handling += handling
        baseline = MANUAL_BASELINE_SECONDS[category]
        avg_handling = _avg(handling)
        rows.append({
            "category": category,
            "count": len(items),
            "decided": len(handling),
            "baseline_seconds": baseline,
            "avg_ai_seconds": _avg(ai),
            "avg_handling_seconds": avg_handling,
            "reduction_percent": _reduction(baseline, avg_handling),
        })
    overall_handling = _avg(all_handling)
    return {
        "rows": rows,
        "total": len(inquiries),
        "decided": len(all_handling),
        "baseline_average_seconds": round(MANUAL_BASELINE_AVERAGE, 1),
        "avg_ai_seconds": _avg(all_ai),
        "avg_handling_seconds": overall_handling,
        "reduction_percent": _reduction(MANUAL_BASELINE_AVERAGE, overall_handling),
        "has_data": bool(all_handling or all_ai),
    }
