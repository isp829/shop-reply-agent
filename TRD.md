# Reply Desk TRD

## 1. 문서 목적

[PRD.md](PRD.md)에 정의한 Reply Desk의 기술 구성과 구현 방식을 정리한다.

## 2. 시스템 구성

```
브라우저 (HTML 폼)
   ↓ POST /inquiries
FastAPI 앱 (main.py)
   ├─ 앱 메모리: 샘플 주문 20건, 처리한 문의 목록
   └─ Microsoft Agent Framework 순차 워크플로
        분류 에이전트 → 답변 에이전트
        (각 에이전트는 GitHubCopilotAgent = GitHub Copilot SDK로 모델 연결)
```

- 서버 렌더링 방식이다. 페이지는 Jinja2 템플릿으로 만들고, 스타일은 `static/style.css` 하나로 정한다.
- 앱 코드는 `main.py` 파일 하나에 있다.

## 3. 기술 스택

| 구분 | 내용 |
|---|---|
| 언어 | Python 3.11 |
| 웹 프레임워크 | FastAPI, Jinja2 템플릿, python-multipart |
| 실행 서버 | gunicorn + uvicorn 워커 1개, 타임아웃 120초 |
| 에이전트 프레임워크 | Microsoft Agent Framework (`agent-framework-github-copilot` 2.1.0, `agent-framework-orchestrations`) |
| 모델 연결 | GitHub Copilot SDK (`github-copilot-sdk` 1.0.26), Copilot CLI (`@github/copilot`, Node.js 22) |
| 데이터 저장 | 앱 메모리 (파이썬 딕셔너리·리스트) |
| 호스팅 | Azure App Service (Linux 컨테이너, B1) |

## 4. 화면과 API

| 메서드 | 경로 | 기능 | 템플릿 |
|---|---|---|---|
| GET | `/` | 문의 입력 화면 | `index.html` |
| POST | `/inquiries` | 문의 처리: 분류, 답변 생성, 환불 상태 변경, 결과 표시 | `index.html` |
| GET | `/inquiries?refund_filter=` | 문의 목록. 환불 상태(all/환불완료/없음)로 필터 | `list.html` |
| GET | `/admin` | 데이터 초기화 화면 | `admin.html` |
| POST | `/admin/reset` | 비밀번호가 맞으면 주문과 문의 목록을 처음 상태로 되돌림 | `admin.html` |

POST `/inquiries` 입력값:

| 필드 | 필수 | 제한 |
|---|:-:|---|
| `customer_name` | O | |
| `order_number` | O | `ORD-1001` ~ `ORD-1020` |
| `inquiry` | O | 최대 2000자 |

응답 상태 코드:

| 상황 | 상태 코드 | 화면 메시지 |
|---|:-:|---|
| 정상 처리 | 200 | 유형·불만도·답변 표시 |
| 없는 주문번호 | 400 | "주문번호를 확인해 주세요" |
| AI 응답 실패 또는 100초 초과 | 502 | "답변 생성에 실패했습니다. 다시 시도해 주세요" |
| 관리자 비밀번호 불일치 | 403 | "비밀번호가 올바르지 않습니다" |

## 5. 데이터 구조

### 5.1 주문 (`ORDERS`)

앱이 시작될 때 주문 20건(`ORD-1001` ~ `ORD-1020`)을 만들고, 초기화할 때 쓰도록 원본(`INITIAL_ORDERS`)을 따로 복사해 둔다.

| 필드 | 예시 | 설명 |
|---|---|---|
| `order_number` | `ORD-1003` | 주문번호 |
| `customer_name` | `샘플 고객 3` | 고객명 |
| `contact` | `010-0000-1003` | 연락처 |
| `product_name` | `노트북 거치대` | 상품명 |
| `amount` | `42000` | 금액 (원) |
| `order_date` | `2026-04-04` | 주문일 |
| `shipping_status` | `배송준비` | 배송준비 / 배송중 / 배송완료 |
| `refund_status` | `없음` | 없음 / 환불완료 |

### 5.2 문의 (`INQUIRIES`)

| 필드 | 설명 |
|---|---|
| `customer_name` | 입력한 고객명 |
| `order_number` | 입력한 주문번호 |
| `inquiry` | 문의 내용 |
| `category` | 배송 / 환불 / 교환 / 기타 |
| `anger` | 불만도 1~5. 판단하지 못하면 비움 |
| `reply` | 답변 |
| `refund_status` | 처리 직후 환불 상태 |
| `created_at` | 처리 날짜 |

데이터는 메모리에만 있다. 앱이 다시 시작되면 처음 상태로 돌아간다.

## 6. 에이전트 워크플로

### 6.1 구성

| 순서 | 에이전트 | 역할 | 출력 |
|:-:|---|---|---|
| 1 | 분류 에이전트 | 문의 유형(배송/환불/교환/기타)과 불만도(1~5) 판단 | 분류 결과 텍스트 |
| 2 | 답변 에이전트 | 분류 결과와 주문 정보로 한국어 답변 작성, 환불 요청 여부 판단 | JSON |

답변 에이전트의 출력 형식:

```json
{"category": "배송", "anger": 2, "reply": "답변", "refund": false}
```

### 6.2 실행 흐름

1. 입력한 주문번호로 `ORDERS`에서 주문을 찾는다. 없으면 400으로 응답한다.
2. `GitHubCopilotAgent` 두 개를 만든다.
3. `SequentialBuilder(participants=[분류, 답변]).build()`로 순차 워크플로를 만든다.
4. `as_agent()`로 워크플로를 에이전트로 감싼다.
5. 주문 정보(JSON)와 문의 내용을 담은 프롬프트로 실행한다. 분류 에이전트의 결과가 대화에 쌓이고, 답변 에이전트가 이어서 받는다.
6. 전체 실행은 `asyncio.wait_for`로 100초까지 기다린다.
7. 최종 응답을 JSON으로 읽고 검증한다.
   - `category`가 네 유형 중 하나인지 확인한다.
   - `anger`가 1~5 정수인지 확인한다.
   - 검증에 실패하면 원본 응답을 답변으로 쓰고, 유형은 `기타`, 환불은 `false`로 처리한다.
8. `refund`가 `true`이면 주문의 `refund_status`를 `환불완료`로 바꾼다.
9. 문의 기록을 `INQUIRIES`에 추가하고 결과를 표시한다.

에이전트는 문의 1건마다 새로 만들고, `async with`로 열고 닫는다.

### 6.3 오류 처리

- 다음 예외가 나면 오류 로그를 남기고, 사용자에게 다시 시도하라는 메시지를 보여 준다.
  - `asyncio.TimeoutError`
  - `RuntimeError`
  - `ConnectionError`
  - `AgentException` (Agent Framework)
- 입력값은 폼을 다시 채워 보여 주므로, 실패해도 다시 입력하지 않아도 된다.

## 7. 화면 처리

- 문의를 제출하면 버튼이 비활성화되고 "처리 중... (최대 1분)"으로 바뀐다.
- 결과 영역에는 "AI가 작성한 초안" 표시, 유형·불만도 배지, 답변을 보여 준다. 환불이 처리되면 "환불상태: 환불완료"를 표시한다.
- 문의 목록은 주문번호, 문의, 유형, 불만도, 환불 상태를 표로 보여 준다.

## 8. 인증과 설정

| 항목 | 방식 |
|---|---|
| Copilot 인증 | 환경변수 `GITHUB_TOKEN` |
| 관리자 비밀번호 | 환경변수 `ADMIN_PASSWORD`. 설정되지 않으면 초기화 기능이 동작하지 않음 |
| 사용자 인증 | 없음. 로그인 없이 모든 화면에 접근 |

`.env`, `copilot-prompt.md`, `server.log`는 `.gitignore`와 `.dockerignore`로 저장소와 이미지에서 제외한다.

## 9. 배포

- Docker 이미지를 GitHub Container Registry에 올리고, Azure App Service(Linux 컨테이너, B1)에서 실행한다.
- `Dockerfile` 구성:
  1. `python:3.11-slim` 이미지를 기반으로 한다.
  2. Node.js 22와 Copilot CLI(`@github/copilot`)를 설치한다.
  3. `requirements.txt` 패키지를 설치한다.
  4. 8000번 포트에서 gunicorn으로 실행한다.
- App Service 앱 설정: `GITHUB_TOKEN`, `ADMIN_PASSWORD`, `WEBSITES_PORT=8000`
- 배포 링크: https://reply-desk-isp829.azurewebsites.net

## 10. 로컬 실행

```bash
npm install -g @github/copilot
pip install --pre -r requirements.txt
uvicorn main:app --reload
```

`GITHUB_TOKEN`을 설정해야 하고, 관리자 초기화를 쓰려면 `ADMIN_PASSWORD`도 설정한다.

## 11. 제약 사항

- 데이터가 메모리에만 있어서 재시작하면 사라지고, 모든 접속자가 같은 데이터를 공유한다.
- 에이전트는 도구를 호출하지 않는다. 주문 정보는 프롬프트에 직접 넣는다.
- 환불은 사람의 승인 없이 AI 판단으로 바로 처리된다.
- 응답까지 수십 초 걸리며, 진행 상황은 버튼 문구로만 표시한다.
- 테스트 코드와 CI는 없다.
