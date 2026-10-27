"""저장소. 모든 데이터는 브라우저 작업 공간(session_id)별로 분리한다.

- InMemoryRepository: 로컬 개발과 테스트용
- TableRepository: Azure Table Storage. Managed Identity(DefaultAzureCredential)로만 접근한다.

주문 기본값은 SEED_ORDERS에 있고, 세션마다 바뀐 값(환불 상태)만 orders 테이블에 저장한다.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Any, Protocol

logger = logging.getLogger(__name__)

MAX_FIELD_CHARS = 30_000
JSON_FIELDS = ("screening", "citations", "checks", "evidence")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


# ---------------------------------------------------------------- 샘플 주문
_SEED_SPEC = [
    # (번호, 이름, 상품, 옵션, 금액, 주문 며칠 전, 배송 상태, 배송완료 며칠 전)
    (1001, "김민준", "무선 키보드", "블랙", 59000, 10, "배송완료", 6),
    (1002, "이서연", "보온 텀블러", "500ml / 화이트", 24000, 3, "배송중", None),
    (1003, "박지호", "노트북 거치대", "실버", 42000, 7, "배송준비", None),
    (1004, "최하은", "블루투스 이어폰", "네이비", 89000, 15, "배송완료", 11),
    (1005, "정우진", "데스크 매트", "그레이 / L", 18000, 4, "배송중", None),
    (1006, "강수아", "USB-C 허브", "7포트", 32000, 9, "배송완료", 5),
    (1007, "조예준", "모니터 조명", "화이트", 47000, 1, "배송준비", None),
    (1008, "윤지우", "스마트폰 케이스", "투명", 22000, 12, "배송완료", 8),
    (1009, "장도윤", "노트 세트", "3권", 12000, 2, "배송중", None),
    (1010, "임서윤", "미니 가습기", "핑크", 36000, 2, "배송준비", None),
    (1011, "한시우", "웹캠", "FHD", 67000, 20, "배송완료", 16),
    (1012, "오하린", "니트 가디건", "아이보리 / M", 54000, 6, "배송완료", 3),
    (1013, "서준우", "충전 케이블", "2m", 15000, 5, "배송완료", 2),
    (1014, "신채원", "서류 가방", "브라운", 73000, 1, "배송준비", None),
    (1015, "권지안", "책상 선반", "원목", 51000, 16, "배송완료", 12),
    (1016, "황은우", "노트북 파우치", "15인치 / 차콜", 28000, 3, "배송중", None),
    (1017, "안유나", "키보드 팜레스트", "월넛", 27000, 8, "배송완료", 4),
    (1018, "송건우", "블루투스 스피커", "블랙", 64000, 2, "배송중", None),
    (1019, "류다은", "캔들 워머", "베이지", 33000, 13, "배송완료", 9),
    (1020, "홍태윤", "여행용 어댑터", "멀티", 29000, 4, "배송준비", None),
]


def seed_orders(today: date | None = None) -> dict[str, dict[str, Any]]:
    today = today or utcnow().astimezone(timezone(timedelta(hours=9))).date()
    orders: dict[str, dict[str, Any]] = {}
    for num, name, product, option, amount, ordered_ago, status, delivered_ago in _SEED_SPEC:
        order_no = f"ORD-{num}"
        orders[order_no] = {
            "order_number": order_no,
            "customer_name": name,
            "phone": f"010-{2000 + num % 1000:04d}-{num:04d}",
            "product_name": product,
            "option": option,
            "amount": amount,
            "order_date": (today - timedelta(days=ordered_ago)).isoformat(),
            "shipping_status": status,
            "delivered_date": (today - timedelta(days=delivered_ago)).isoformat() if delivered_ago is not None else None,
            "refund_status": "없음",
        }
    return orders


def _clip(value: str) -> str:
    return value if len(value) <= MAX_FIELD_CHARS else value[:MAX_FIELD_CHARS]


class Repository(Protocol):
    def init(self) -> None: ...
    def get_order(self, sid: str, order_number: str) -> dict[str, Any] | None: ...
    def set_refund_status(self, sid: str, order_number: str, status: str) -> None: ...
    def save_inquiry(self, record: dict[str, Any]) -> None: ...
    def get_inquiry(self, sid: str, inquiry_id: str) -> dict[str, Any] | None: ...
    def update_inquiry(self, sid: str, inquiry_id: str, **fields: Any) -> dict[str, Any] | None: ...
    def list_inquiries(self, sid: str) -> list[dict[str, Any]]: ...
    def list_by_order(self, sid: str, order_number: str, limit: int = 5) -> list[dict[str, Any]]: ...
    def count_since(self, sid: str, since: datetime) -> int: ...
    def reset_session(self, sid: str) -> None: ...
    def purge_older_than(self, hours: int) -> int: ...
    def purge_all(self) -> None: ...
    def ping(self) -> bool: ...


# ---------------------------------------------------------------- 메모리 구현
class InMemoryRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._overrides: dict[tuple[str, str], dict[str, Any]] = {}
        self._inquiries: dict[tuple[str, str], dict[str, Any]] = {}

    def init(self) -> None:
        return None

    def ping(self) -> bool:
        return True

    def get_order(self, sid: str, order_number: str) -> dict[str, Any] | None:
        base = seed_orders().get(order_number)
        if base is None:
            return None
        with self._lock:
            base.update(self._overrides.get((sid, order_number), {}))
        return base

    def set_refund_status(self, sid: str, order_number: str, status: str) -> None:
        with self._lock:
            self._overrides.setdefault((sid, order_number), {})["refund_status"] = status

    def save_inquiry(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._inquiries[(record["session_id"], record["id"])] = copy.deepcopy(record)

    def get_inquiry(self, sid: str, inquiry_id: str) -> dict[str, Any] | None:
        with self._lock:
            found = self._inquiries.get((sid, inquiry_id))
            return copy.deepcopy(found) if found else None

    def update_inquiry(self, sid: str, inquiry_id: str, **fields: Any) -> dict[str, Any] | None:
        with self._lock:
            found = self._inquiries.get((sid, inquiry_id))
            if not found:
                return None
            found.update(copy.deepcopy(fields))
            return copy.deepcopy(found)

    def list_inquiries(self, sid: str) -> list[dict[str, Any]]:
        with self._lock:
            items = [copy.deepcopy(v) for (s, _), v in self._inquiries.items() if s == sid]
        return sorted(items, key=lambda r: r["created_at"], reverse=True)

    def list_by_order(self, sid: str, order_number: str, limit: int = 5) -> list[dict[str, Any]]:
        return [r for r in self.list_inquiries(sid) if r["order_number"] == order_number][:limit]

    def count_since(self, sid: str, since: datetime) -> int:
        return sum(1 for r in self.list_inquiries(sid) if (parse_iso(r["created_at"]) or since) >= since)

    def reset_session(self, sid: str) -> None:
        with self._lock:
            for key in [k for k in self._inquiries if k[0] == sid]:
                del self._inquiries[key]
            for key in [k for k in self._overrides if k[0] == sid]:
                del self._overrides[key]

    def purge_older_than(self, hours: int) -> int:
        cutoff = utcnow() - timedelta(hours=hours)
        with self._lock:
            old = [k for k, v in self._inquiries.items() if (parse_iso(v["created_at"]) or cutoff) < cutoff]
            for key in old:
                del self._inquiries[key]
        return len(old)

    def purge_all(self) -> None:
        with self._lock:
            self._inquiries.clear()
            self._overrides.clear()


# ---------------------------------------------------------------- Azure Table 구현
class TableRepository:
    """Azure Table Storage. 연결 문자열·계정 키 없이 DefaultAzureCredential로 접근한다."""

    def __init__(self, table_url: str) -> None:
        from azure.data.tables import TableServiceClient
        from azure.identity import DefaultAzureCredential

        self._service = TableServiceClient(endpoint=table_url, credential=DefaultAzureCredential())
        self._orders = self._service.get_table_client("orders")
        self._inquiries = self._service.get_table_client("inquiries")

    def init(self) -> None:
        for name in ("orders", "inquiries"):
            self._service.create_table_if_not_exists(name)

    def ping(self) -> bool:
        try:
            next(iter(self._inquiries.query_entities("PartitionKey eq 'healthcheck'", results_per_page=1)), None)
            return True
        except Exception:
            logger.warning("Table Storage 연결 확인 실패")
            return False

    # 주문
    def get_order(self, sid: str, order_number: str) -> dict[str, Any] | None:
        base = seed_orders().get(order_number)
        if base is None:
            return None
        from azure.core.exceptions import ResourceNotFoundError

        try:
            entity = self._orders.get_entity(partition_key=sid, row_key=order_number)
            if entity.get("refund_status"):
                base["refund_status"] = entity["refund_status"]
        except ResourceNotFoundError:
            pass
        return base

    def set_refund_status(self, sid: str, order_number: str, status: str) -> None:
        self._orders.upsert_entity({"PartitionKey": sid, "RowKey": order_number, "refund_status": status})

    # 문의
    @staticmethod
    def _to_entity(record: dict[str, Any]) -> dict[str, Any]:
        entity: dict[str, Any] = {"PartitionKey": record["session_id"], "RowKey": record["id"]}
        for key, value in record.items():
            if key in ("session_id", "id"):
                continue
            if key in JSON_FIELDS:
                entity[key] = _clip(json.dumps(value, ensure_ascii=False))
            elif isinstance(value, str):
                entity[key] = _clip(value)
            elif value is not None:
                entity[key] = value
        return entity

    @staticmethod
    def _from_entity(entity: dict[str, Any]) -> dict[str, Any]:
        record = {k: v for k, v in entity.items() if k not in ("PartitionKey", "RowKey")}
        record["session_id"] = entity["PartitionKey"]
        record["id"] = entity["RowKey"]
        for key in JSON_FIELDS:
            raw = record.get(key)
            if isinstance(raw, str):
                try:
                    record[key] = json.loads(raw)
                except json.JSONDecodeError:
                    record[key] = {} if key == "screening" else []
        return record

    def save_inquiry(self, record: dict[str, Any]) -> None:
        self._inquiries.upsert_entity(self._to_entity(record))

    def get_inquiry(self, sid: str, inquiry_id: str) -> dict[str, Any] | None:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self._from_entity(self._inquiries.get_entity(partition_key=sid, row_key=inquiry_id))
        except ResourceNotFoundError:
            return None

    def update_inquiry(self, sid: str, inquiry_id: str, **fields: Any) -> dict[str, Any] | None:
        record = self.get_inquiry(sid, inquiry_id)
        if record is None:
            return None
        record.update(fields)
        self.save_inquiry(record)
        return record

    def list_inquiries(self, sid: str) -> list[dict[str, Any]]:
        entities = self._inquiries.query_entities("PartitionKey eq @sid", parameters={"sid": sid})
        items = [self._from_entity(e) for e in entities]
        return sorted(items, key=lambda r: r.get("created_at", ""), reverse=True)

    def list_by_order(self, sid: str, order_number: str, limit: int = 5) -> list[dict[str, Any]]:
        entities = self._inquiries.query_entities(
            "PartitionKey eq @sid and order_number eq @order", parameters={"sid": sid, "order": order_number}
        )
        items = sorted((self._from_entity(e) for e in entities), key=lambda r: r.get("created_at", ""), reverse=True)
        return items[:limit]

    def count_since(self, sid: str, since: datetime) -> int:
        entities = self._inquiries.query_entities(
            "PartitionKey eq @sid and created_at ge @since",
            parameters={"sid": sid, "since": _iso(since)},
            select=["RowKey"],
        )
        return sum(1 for _ in entities)

    def _delete_where(self, table: Any, query: str, parameters: dict[str, Any] | None = None) -> int:
        count = 0
        for entity in table.query_entities(query, parameters=parameters or {}, select=["PartitionKey", "RowKey"]):
            table.delete_entity(partition_key=entity["PartitionKey"], row_key=entity["RowKey"])
            count += 1
        return count

    def reset_session(self, sid: str) -> None:
        self._delete_where(self._inquiries, "PartitionKey eq @sid", {"sid": sid})
        self._delete_where(self._orders, "PartitionKey eq @sid", {"sid": sid})

    def purge_older_than(self, hours: int) -> int:
        cutoff = _iso(utcnow() - timedelta(hours=hours))
        return self._delete_where(self._inquiries, "created_at lt @cutoff", {"cutoff": cutoff})

    def purge_all(self) -> None:
        self._delete_where(self._inquiries, "PartitionKey ne ''")
        self._delete_where(self._orders, "PartitionKey ne ''")
