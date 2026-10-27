"""개인정보 마스킹. AI에 보내기 전, 저장하기 전, 화면·로그에 쓰기 전에 적용한다."""

from __future__ import annotations

import re

_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    ("주민등록번호", re.compile(r"(?<!\d)\d{6}\s?-\s?[1-4]\d{6}(?!\d)"), "[주민번호 가림]"),
    ("카드번호", re.compile(r"(?<!\d)(?:\d{4}[\s-]?){3}\d{4}(?!\d)"), "[카드번호 가림]"),
    ("전화번호", re.compile(r"(?<!\d)01[016789][\s-]?\d{3,4}[\s-]?\d{4}(?!\d)"), "[전화번호 가림]"),
    ("이메일", re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"), "[이메일 가림]"),
    ("계좌번호", re.compile(r"(?<!\d)\d{3,6}-\d{2,6}-\d{4,8}(?!\d)"), "[계좌번호 가림]"),
]


def mask_text(text: str) -> tuple[str, list[str]]:
    """문장 안의 개인정보를 가리고, 발견한 종류 목록을 함께 돌려준다."""
    found: list[str] = []
    for label, pattern, replacement in _PATTERNS:
        if pattern.search(text):
            found.append(label)
            text = pattern.sub(replacement, text)
    return text, found


def mask_phone(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 8:
        return "***"
    return f"{digits[:3]}-****-{digits[-4:]}"


def mask_name(name: str) -> str:
    name = name.strip()
    if len(name) <= 1:
        return "*"
    if len(name) == 2:
        return name[0] + "*"
    return name[0] + "*" * (len(name) - 2) + name[-1]
