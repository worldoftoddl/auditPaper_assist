# server/ — 콜드 해석 데모의 MCP 서버 (3단계)

지시서 `docs/workorders/03_MCP_콜드해석_v1.1.md`의 산출물. Qdrant Cloud
`standards_20250829_bgem3`(10,063포인트)를 지식 도구로 노출하는 FastMCP stdio 서버다.

- `core.py` — Gateway(도구 3종 구현)·정책·정의 주입. MCP 비의존 (원격 전송 어댑터 교체 대비)
- `contracts.py` — 기동 검증 6항목. 메타 컬렉션의 manifest와 실행 환경이 불일치하면 기동 거부
- `mcp_server.py` — FastMCP 도구 3종 + 오류 봉투. `python -m server.mcp_server`

배선: 루트 `.mcp.json` (접속 실값은 `.env` — 커밋 금지). 인수 테스트:
`.venv/bin/pytest tests/test_acceptance.py -v` (A1~A10, 실 Qdrant 대상 ~30초).

원격 공유: `MCP_TRANSPORT=http`면 HTTP로 서빙하며 `MCP_AUTH_TOKEN`(Bearer, 16자 이상)이
없으면 기동 거부한다. 호스팅: HuggingFace Space(상시 — `deploy/hf_space/`) 또는
Colab+터널(일시 — `colab/auditpaper_mcp_colab.ipynb`). 소비자 안내는 `docs/사용안내_원격MCP.md`.

## v1.1 전환 사유 (완료 기준 ⑥)

v1의 `standards_worksheet_map` 런타임 도구(조서번호 → 기준서 사전 라우팅 표)는 폐기했다.
사전 라우팅 표가 런타임에 개입하면 "에이전트가 처음 보는 조서를 스스로 해석했다"는 데모
서사가 성립하지 않기 때문이다. 라우팅 표는 `eval/routing_gold.json`(채점 전용 골드셋)으로
승격됐고, 코드 전체에서 이 파일을 참조하는 곳은 `eval/score_interpretation.py`(채점기)뿐이다
— 서버·에이전트 런타임은 로드하지 않는다. **사전 라우팅 → 콜드 해석 + 채점표.**

## 구현 확정·이탈 기록

기록 전수(D-11~D-20: DB 단일 소스 전환, 논리 ID 해석, 정의 주입 대표 선정, define_terms
3단계 매칭, A7·A9 조정, 인코더 지연 로드 등)는 [`docs/LEDGER.md`](../docs/LEDGER.md)로 이관했다.

## 도구 요약

| 도구 | 역할 | 핵심 규칙 |
|---|---|---|
| `standards_get_paragraph` | cid 직조회·인용 검증 | `#` 조각 ID 거부, 분할 재조립, context 0~3 이웃 |
| `standards_search` | 하이브리드(RRF) + 정의 주입 | 비정본 옵트인 2축: 예시류(부록·적용사례)=`include_examples`, 결론도출근거=`include_bc` — 기본 제외(`examples_excluded`/`bc_excluded` 집계), 참조 가드 note, oov 토큰 기록 |
| `standards_define_terms` | 용어 사전 직조회 | 3단계 매칭, context_standard 우선 대표 + alternates |

기동 순서: contracts(메타 컬렉션 로드 → 모델·sparse·토크나이저·컬렉션·glossary·프로브)
→ Gateway → stdio. 전 도구 애노테이션 readOnly/idempotent, 오류 봉투 4코드
(NOT_FOUND · INVALID_INPUT · CONTRACT_MISMATCH · UPSTREAM_UNAVAILABLE) + 행동 힌트.
