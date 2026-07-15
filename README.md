# auditPaper_MCP — 한국 감사·회계기준 RAG MCP 서버

**한국 회계감사기준(ISA)·K-IFRS·회계감사실무지침 전문을 검색·인용하는 MCP 서버.**
표준 원문 212파일 20,970문단을 Qdrant Cloud에 적재하고
MCP 도구 3종으로 서빙한다. 모든 응답 문단에 복합 ID(cid, 예 `KIFRS::1115::31`)가 붙어
인용을 원문과 실물 대조할 수 있다.

## 도구 3종

| 도구 | 역할 |
|---|---|
| `standards_search` | 하이브리드 RRF 검색 + 용어 정의 주입. 기본은 기준서 본문만 검색 — 부록·적용사례(`include_examples`), 결론도출근거(`include_bc`), 개념체계(`include_framework`)는 별도 설정으로 검색 |
| `standards_get_paragraph` | cid 직조회 · 분할 문단 재조립 · 앞뒤 문맥 확장(context 0~3) |
| `standards_define_terms` | 용어 사전 664건 3단계 매칭(완전→어간→부분) |

**연결** — 로컬(stdio)은 루트 [`.mcp.json`](.mcp.json) 배선 그대로, 원격(HTTP)은
HF Space 상시 배포. 접속법·입출력 스키마·오류는
[`docs/사용안내_원격MCP.md`](docs/사용안내_원격MCP.md) 참조.

### 인용 표기 — cid 풀어 쓰기

cid는 내부 식별자다. 소비자 에이전트가 사용자에게 보여주는 본문에는 cid 원형을 그대로 쓰지
말고, 결과에 함께 오는 메타데이터(`standard_no`·`standard_title`·문단번호)로 풀어 쓰게 하라.
검증(재조회·채점)이 필요하면 본문 대신 각주·근거 목록에 cid를 병기한다.

| cid | 표기 |
|---|---|
| `KIFRS::1115::31` | K-IFRS 제1115호 '고객과의 계약에서 생기는 수익' 문단 31 |
| `KSA::315::A12` | 감사기준서 315 문단 A12(적용자료) — `A` 접두 문단은 적용자료 |
| `GUIDE::2017-1::25` | 회계감사실무지침 2017-1 문단 25 |
| `KIFRS::1116::BC1` | K-IFRS 제1116호 결론도출근거 BC1 (기준서 본문 아님을 병기) |
| `KIFRS::1103::IE사례5-2` | K-IFRS 제1103호 적용사례 사례 5의 문단 2 |
| `KSA::240::부록1` / `::정의-<용어>` | 감사기준서 240 부록 1 / '<용어>'의 정의(용어정의 부록) |

## 아키텍처 — 3층

```
[1층 · 데이터]   원문 DOCX/PDF/md (auditstandard_md·ifrs_md·Conceptual_framework_md·guidelines_raw)
                   │ scripts/normalize_corpus.py
                   ▼
                 corpus_md/ 203파일(정본 102 + BC·IE 101) + guidelines_md/ 9파일
                 = 212파일 · 20,970문단
                   │ scripts/build_index.py + Colab GPU    (bge-m3 dense + kiwipiepy/BM25 sparse)
                   ▼
                 Qdrant Cloud: standards_20250829_bgem3_v3
                              + *_meta (manifest·vocab·용어사전 664) — DB 단일 소스

[2층 · 서버]     server/ MCP 서버 (FastMCP, .env으로 기동)
                 stdio 로컬 또는 HTTP 원격(deploy/hf_space/ · colab/)

[3층 · 소비자]   MCP를 든 임의의 에이전트
```

## 저장소 지도

| 폴더 | 내용 |
|---|---|
| `server/` | **MCP 서버 3종 도구** (로컬 배선: 루트 `.mcp.json`) |
| `deploy/hf_space/` `colab/` | 원격 호스팅 — HF Space Docker(상시) · Colab+터널(일시) |
| `corpus_md/` | 규약 형식 통일 코퍼스 — 정본(감사기준 39 + 회계기준 63) + BC·IE 갈래 101(검색 옵트인) (적재 대상 ①) |
| `guidelines_md/` | 회계감사실무지침 9건 — 수작업 변환 원본(source of truth) (적재 대상 ②) |
| `auditstandard_md/` `ifrs_md/` `Conceptual_framework_md/` | 변환 전 원문 md (옛 스키마) |
| `guidelines_raw/` | 실무지침 원본(DOC/DOCX/PDF) |
| `scripts/` | `normalize_corpus.py`(변환기) · `build_index.py`(적재기) |
| `index/` | 적재 산출물(vocab·glossary·manifest) — 재구축·감사용 기록 |
| `tests/` | 인수 테스트 A1~A15 (실 Qdrant 대상) |
| `eval/` | 채점 전용 골드셋 + recall 채점기 + 채점 결과 + DB 품질 감사 도구 |
| `reports/` | 조서 해석 보고서 (`해석_{조서번호}.md`) |
| `docs/` | 규약 정본 · 원격 사용 안내 · 지시서 아카이브(`workorders/`) · 결함·이탈 대장(`LEDGER.md`) |

## 재현 방법

1. **재구축** — `.venv/bin/python scripts/normalize_corpus.py`(코퍼스 재생성, 바이트 결정적) →
   `.venv/bin/python scripts/build_index.py`(적재. dense는 `embedding.ipynb`로 Colab GPU 이관 가능 —
   `index/README.md`). 접속 정보는 `.env`의 `QDRANT_URL`/`QDRANT_API_KEY`(커밋 금지).
2. **기동** — `.venv/bin/python -m server.mcp_server` 또는 Claude Code가 `.mcp.json`으로 자동 기동.
   검증: `.venv/bin/pytest tests/test_acceptance.py -v`.
3. **데모** — 조서 파일(xlsx/docx)을 `reports/`에 두고 "해석해줘" →
   `CLAUDE.md` 플로우로 `reports/해석_{조서번호}.md` 산출 →
   `.venv/bin/python eval/score_interpretation.py reports/해석_{조서번호}.md {조서번호}`.

설계 규약: [`docs/규약_벡터저장소_스키마.md`](docs/규약_벡터저장소_스키마.md) ·
지시서: [`docs/workorders/`](docs/workorders/) ·
결함·이탈 전수 기록: [`docs/LEDGER.md`](docs/LEDGER.md)
