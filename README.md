# 쇼핑몰 문의 응대 에이전트

소규모 온라인 쇼핑몰 운영자가 고객 문의를 빠르고 정확하게 처리하도록 돕는 웹 앱입니다. 에이전트가 주문·정책·이전 문의를 직접 조회해 근거가 붙은 답장 초안을 만들고, 운영자는 확인한 뒤 확정합니다.

- 대상 사용자: 하루 수십 건의 고객 문의를 직접 처리하는 소규모 온라인 쇼핑몰 운영자와 직원
- 기획서: [기획서(PRD).md](<기획서(PRD).md>)
- 배포 링크: https://app-shopreply-zj2ite.azurewebsites.net

## 개선 효과

샘플 문의 3건을 직접 처리한 시간과 앱으로 처리한 시간을 측정했습니다 (기획서 4장).

| 문의 | 직접 처리 | 앱 사용 | 단축 |
|---|:-:|:-:|:-:|
| 배송 지연 | 4분 20초 | 1분 15초 | 71% |
| 환불 요청 | 5분 14초 | 34초 | 89% |
| 교환 문의 | 4분 45초 | 32초 | 89% |
| 합계 | 14분 19초 | 2분 21초 | 84% |

앱의 생산성 대시보드는 실제 사용 중에도 문의마다 처리 시간을 기록해 같은 기준과 비교합니다.

## 처리 흐름

```
사전 점검(코드) → 분류 에이전트 → 조사 에이전트(도구 3개) → 답변 에이전트 → 근거 검증(코드) → 운영자 확정·환불 승인
```

| 단계 | 하는 일 |
|---|---|
| 사전 점검 | 문의 속 개인정보를 가리고, 지시문처럼 보이는 표현(프롬프트 인젝션 신호)을 찾는다 |
| 분류 에이전트 | 유형(배송/환불/교환/기타), 불만도(1~5), 핵심 요청 판단 |
| 조사 에이전트 | `get_order`, `search_policy`, `get_inquiry_history` 도구로 확인된 사실만 정리 |
| 답변 에이전트 | 확인된 사실만으로 답장 초안과 문장별 근거 작성, 환불은 제안만 |
| 근거 검증 | 답장 문장의 금액·날짜·기간·상태·정책 조항을 실제 조회 결과와 대조 |

- 사전 점검과 에이전트 3개는 Microsoft Agent Framework 순차 워크플로 하나로 연결되고, 스트리밍으로 실행됩니다. 진행 단계, 도구 호출, 답장 작성 과정이 화면에 실시간으로 표시됩니다.
- 모델 연결은 GitHub Copilot SDK를 쓰고, 앱 전체가 Copilot 클라이언트 하나를 공유합니다.
- 에이전트는 위 도구 3개만 쓸 수 있고, 나머지 권한 요청(명령 실행, 파일 접근 등)은 거부됩니다. 도구는 현재 문의의 주문만 조회합니다.

## 책임 있는 AI와 보안

- 환불은 AI가 제안만 하고 운영자가 승인해야 처리됩니다.
- 근거가 없는 문장은 표시되고, 운영자가 확인해야 답장을 확정할 수 있습니다. 운영자가 고친 답장도 다시 검증합니다.
- 전화번호·이메일·카드번호·계좌번호·주민등록번호는 AI에 보내기 전에 가리고, 가린 내용만 저장합니다.
- 공개 데모는 로그인 없이 쓰되 브라우저마다 작업 공간을 나누고, 문의 기록은 24시간 뒤 자동 삭제합니다.
- 브라우저당 시간당 처리 횟수를 제한합니다.
- 보안 헤더(CSP 등)를 적용하고 인라인 스크립트를 쓰지 않습니다.

## Azure 구성

`infra/main.bicep` 하나로 전체 인프라를 만듭니다.

| 서비스 | 용도 |
|---|---|
| App Service (Linux 컨테이너, B1) | 앱 실행, 상태 확인 `/healthz` |
| Table Storage | 브라우저별 주문 상태와 문의 기록 (계정 키 접근 차단) |
| Key Vault | `GITHUB-TOKEN`, `ADMIN-PASSWORD` 보관 |
| Managed Identity | 앱이 비밀 값 없이 Storage·Key Vault에 접근 (최소 권한 역할) |
| Application Insights, Log Analytics | 요청·오류·응답 시간 수집, 컨테이너 로그 |
| 경보 | 5xx 오류 증가, 상태 확인 실패 |
| 예산 | 리소스 그룹 월 예산 80% 도달 알림 |

비용을 고려해 단일 B1 인스턴스, 로그 일일 수집량 1GB 상한을 씁니다.

## 로컬 실행

```bash
pip install -r requirements-dev.txt
npm install -g @github/copilot
```

간단히 실행 (메모리 저장소):

```powershell
$env:GITHUB_TOKEN="github_pat_..."   # Copilot Requests 권한이 있는 토큰
$env:ADMIN_PASSWORD="원하는비밀번호"
uvicorn app.main:app --reload
```

Azure 리소스를 쓰는 실행 (`az login` 후):

```powershell
$env:KEY_VAULT_URL="https://<key-vault-name>.vault.azure.net/"
$env:STORAGE_TABLE_URL="https://<storage-name>.table.core.windows.net/"
uvicorn app.main:app --reload
```

선택 환경변수: `COPILOT_MODEL`(모델 이름), `RATE_LIMIT_PER_HOUR`(기본 8), `RETENTION_HOURS`(기본 24), `MAX_CONCURRENT_RUNS`(기본 2)

## 테스트

```bash
pytest -q
```

실제 Copilot을 호출하지 않고 가짜 에이전트로 실제 Agent Framework 워크플로를 실행합니다. 워크플로 단계 순서, 도구 호출, 근거 검증, 권한 핸들러, 세션 분리, 환불 승인, 인젝션 처리, 개인정보 마스킹, 횟수 제한, 스트리밍 응답을 검증합니다. GitHub Actions(`ci.yml`)가 푸시마다 테스트와 Bicep 컴파일을 실행합니다.

## 배포

1. GitHub Actions의 `build-image`가 테스트를 통과한 뒤 이미지를 `ghcr.io/<owner>/shop-reply-agent`에 올립니다.
2. 인프라 배포:
   ```powershell
   ./scripts/deploy.ps1                         # 기본
   ./scripts/deploy.ps1 -AlertEmail you@example.com
   ./scripts/deploy.ps1 -CopilotModel <모델 이름>
   ```
3. 출력된 Key Vault에 `GITHUB-TOKEN`, `ADMIN-PASSWORD` 비밀을 추가하고 앱을 다시 시작합니다.

## 사용 예시

샘플 주문번호는 `ORD-1001` ~ `ORD-1020`입니다. 메인 화면의 예시 버튼으로 바로 시험할 수 있습니다.

| 주문 | 문의 | 확인할 점 |
|---|---|---|
| ORD-1003 | 배송이 언제 오나요? 일주일째 소식이 없네요 | 배송 지연 정책 근거, 사과 문장 |
| ORD-1007 | 환불해 주세요 | 환불 대기 → 운영자 승인 |
| ORD-1012 | 다른 색상으로 교환하고 싶어요 | 7일 이내 교환 정책 |

에이전트 3개가 차례로 실행되어 응답까지 1~2분 걸릴 수 있습니다.
