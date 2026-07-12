# auditpaper-standards 원격 MCP 사용 안내

다른 컴퓨터·다른 프로젝트에서 이 MCP 서버를 HTTP로 쓰는 소비자(클라이언트) 안내서.
서버 호스팅은 두 경로: **HuggingFace Space**(상시·고정 URL — 권장,
[`deploy/hf_space/`](../deploy/hf_space/)) 또는 Colab+터널(일시,
[`colab/auditpaper_mcp_colab.ipynb`](../colab/auditpaper_mcp_colab.ipynb)).
서버 내부 구조는 [`server/README.md`](../server/README.md) 참조.

## 1. 연결 방법

프로젝트 루트 `.mcp.json`에 아래를 넣고 Claude Code 재시작 → MCP 승인 →
`claude mcp list`에서 ✔ Connected 확인. 토큰(`MCP_AUTH_TOKEN`)은 서버 운영자에게
안전한 경로로 받는다. 현행 배포는 HF Space `toddl/auditpaper-mcp`(고정 URL — 아래 예시).
Colab+터널 호스팅이면 URL을 세션마다 운영자에게 확인.

```json
{
  "mcpServers": {
    "auditpaper-standards": {
      "type": "http",
      "url": "https://toddl-auditpaper-mcp.hf.space/mcp",
      "headers": { "Authorization": "Bearer <MCP_AUTH_TOKEN>" }
    }
  }
}
```

- 서버 기동 직후 약 1분은 임베딩 모델(bge-m3) 로드 중이라 `standards_search`만
  대기가 걸릴 수 있다. `standards_get_paragraph`·`standards_define_terms`는 즉시 동작.
- 같은 머신의 로컬 사용(stdio)은 루트 `.mcp.json` 원본 배선을 그대로 쓰면 된다 — 이 문서는 원격 HTTP 소비자용.

## 2. 도구 3종 — 입력·출력 스키마

**공통**: 모든 응답(오류 포함) 최상위에 `"collection"` 필드가 붙는다 — 인용 판본 고정용
(규약 3.1). 오류는 4코드 봉투:

```json
{ "collection": "…", "error": { "code": "NOT_FOUND | INVALID_INPUT | CONTRACT_MISMATCH | UPSTREAM_UNAVAILABLE", "message": "…", "hint": "권장 다음 행동" } }
```

### standards_get_paragraph — cid 직조회·인용 검증

입력:

| 파라미터 | 타입 | 규칙 |
|---|---|---|
| `cid` | string, 필수 | 논리 복합 ID — `KSA::<번호>::<문단>` / `KIFRS::…` / `GUIDE::…` (예: `KIFRS::1115::31`). `#` 포함 물리 조각 ID는 거부 |
| `context` | int, 기본 0 | 0~3. 같은 기준서의 seq ±N 이웃 문단 포함 |

출력:

```json
{
  "collection": "standards_20250829_bgem3",
  "found": true,
  "paragraphs": [{
    "cid": "KIFRS::1115::31",
    "source_type": "회계기준",
    "standard_no": "1115",
    "standard_title": "고객과의 계약에서 생기는 수익",
    "para_no": "31",
    "para_type": "요구사항",
    "section_path": "수행의무의 이행 > …",
    "seq": 123,
    "text": "…문단 원문 전문…",
    "is_context": false
  }],
  "notes": ["분할 문단 2조각 재조립"]
}
```

- `source_type`: `감사기준` | `회계기준` | `실무지침`
- `para_type`: `정의` | `참조` | `부록` | `요구사항` | `적용지침` | `본문`
- `section_path`: 절 경로(`상위 > 하위` 합성), `seq`: 기준서 내 문서 순번
- `is_context`: `context`로 딸려온 이웃 문단이면 `true`
- `notes`: 분할 문단 재조립 등 해당 시에만

### standards_search — 하이브리드 검색 + 정의 주입

입력:

| 파라미터 | 타입 | 규칙 |
|---|---|---|
| `query` | string, 필수 | 자연어 질의 1~500자 |
| `standard_no` | string[], 선택 | 기준서 번호 필터 (예: `["1115"]`) — 명시 참조 발견 시 라우팅 |
| `source_type` | string[], 선택 | `감사기준` \| `회계기준` \| `실무지침` |
| `para_type` | string, 선택 | 위 6종 중 1개 |
| `top_k` | int, 기본 8 | 1~20 |
| `include_examples` | bool, 기본 false | 예시류(부록·**적용사례 IE**) 포함 스위치 — 문안·예시·회계처리 사례 작업이면 true |
| `include_bc` | bool, 기본 false | **결론도출근거(BC)** 포함 스위치 — 기준 제정 근거·연혁·논리구축 질의면 true |

출력:

```json
{
  "collection": "…",
  "results": [{
    "cid": "KSA::315::12", "score": 0.87,
    "standard_no": "315", "standard_title": "…", "para_no": "12",
    "para_type": "요구사항", "section_path": "…", "text": "…",
    "notes": []
  }],
  "definitions": [{
    "term": "수행의무", "source_cid": "KIFRS::1115::정의-수행의무",
    "standard_no": "1115", "text": "…정의 원문…"
  }],
  "applied": {
    "filters": { "standard_no": ["1115"] },
    "fusion": "server-RRF (dense+sparse, prefetch 각 50)",
    "oov_query_tokens": [],
    "examples_excluded": 2,
    "notes": ["예시류(부록) 2건 제외 — …"]
  }
}
```

- `score`: RRF 융합 점수 — 상대 순위 비교용 (절대 임계값으로 쓰지 말 것)
- `results[].notes`: 참조 문단이면 "발췌 대조표 — 원전 문단 우선 인용" 경고
- `definitions`: 질의·결과에 등장한 용어의 정의 자동 주입 (최대 5건)
- `applied`: 검색이 실제로 어떻게 수행됐는지 회신 — `oov_query_tokens`는 어휘집 밖이라
  sparse 검색에서 무시된 질의 토큰, `examples_excluded`는 제외된 부록 건수

### standards_define_terms — 용어 사전 직조회 (664건)

입력:

| 파라미터 | 타입 | 규칙 |
|---|---|---|
| `terms` | string[], 필수 | 1~10건 |
| `context_standard` | string, 선택 | 복수 정의 충돌 시 이 기준서의 정의를 대표로 선택 |

출력:

```json
{
  "collection": "…",
  "definitions": [{
    "term": "지배력",
    "matched": "피투자자에 대한 지배력",
    "source_cid": "KIFRS::1110::정의-…", "standard_no": "1110",
    "source_type": "회계기준", "text": "…정의 원문…",
    "alternates": [{ "source_cid": "…", "standard_no": "…" }]
  }],
  "not_found": ["없는용어"]
}
```

- `matched`: 실제 매칭된 사전 표제 — 원문 정확 일치 → 공백 제거 일치 → 포함 일치
  폴백 순이므로 요청 `term`과 다를 수 있다
- `alternates`: 다른 기준서의 동명 정의 포인터 (본문은 `source_cid`로 직조회)

## 3. 반환 메타데이터 범위

| 항목 | 반환 여부 | 필드 |
|---|---|---|
| 기준서 문서 ID | ✅ | `cid`(안정 식별자) + `standard_no` + `standard_title` + `source_type` |
| 문단 | ✅ | `para_no`, `para_type`, `section_path`, `seq`, 원문 전문 `text` |
| corpus 버전 | ✅ | 모든 응답의 `collection`(스냅샷 식별자, 예: `standards_20250829_bgem3`) |
| 적용일(시행일) | ❌ | **구조화 필드 없음.** 코퍼스는 2025 개정 감사기준·K-IFRS 정본의 스냅샷이며, 시행일 정보는 기준서가 본문에 직접 서술한 문단(경과규정 등)의 `text` 안에만 존재한다. 시행일을 구조화해 쓰려면 클라이언트가 해당 문단을 파싱해야 함 |

## 4. 조회 결과 캐시 규칙

캐시 **가능하고 안전하다**. 근거와 규칙:

- 세 도구 모두 read-only·idempotent로 선언돼 있고(MCP annotations), 코퍼스는 동결
  스냅샷이라 같은 컬렉션 안에서는 같은 cid가 항상 같은 원문을 돌려준다.
- **캐시 키에 반드시 `collection` 값을 포함**할 것
  (예: `standards_20250829_bgem3::KIFRS::1115::31`). 코퍼스 재적재 시 컬렉션 이름이
  바뀌므로 이름이 곧 무효화 신호 — TTL 불필요.
- 캐시 적합도: `get_paragraph`·`define_terms`는 완전 결정적이라 영구 캐시에 최적.
  `search`는 같은 컬렉션·같은 질의면 재현되지만 탐색 용도이므로 캐시 이득이 적다 —
  검색은 실시간으로 하고 **확정된 cid의 원문만 캐시**하는 패턴을 권장.
- 캐시에 저장할 때 `text`와 함께 `cid`·`collection`을 같이 보존할 것 — 법정 기준서
  원문 인용이므로 출처·판본 추적이 끊기면 안 된다. 원문 변형(요약·수정) 저장은 금지.

## 5. 운영 유의사항

- `UPSTREAM_UNAVAILABLE` 오류: Qdrant 무료 티어 휴면 또는 터널/세션 끊김 —
  서버 운영자에게 문의 (Colab 세션은 최대 24시간).
- HF Space 호스팅은 48시간 미사용 시 슬립한다 — 첫 요청이 자동으로 깨우며
  1~2분 걸릴 수 있으니, 응답이 없으면 잠시 후 재시도.
- 토큰은 접속 비밀번호다 — 설정 파일을 공개 저장소에 커밋하지 말 것.
- 규정을 인용할 때는 응답의 `cid`를 그대로 달고, 중요한 인용은
  `standards_get_paragraph`로 원문을 재확인하는 것을 권장.
