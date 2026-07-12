# index/ — 적재기(2단계) 산출물

`scripts/build_index.py`가 `corpus_md/`(102파일·9,885문단) + `guidelines_md/`(9파일·177문단)를
Qdrant Cloud 컬렉션 `standards_20250829_bgem3`으로 적재하며 생성한다.
지시서는 `docs/workorders/02_적재기_v2_Qdrant.md`, 참조 규약은 `docs/규약_벡터저장소_스키마.md`(v2.1).

## 산출물 (커밋 대상)

| 파일 | 내용 |
|---|---|
| `vocab.json` | sparse 어휘집(토큰→정수 인덱스) + 메타(k1·b·avgdl·kiwipiepy 버전·품사 규칙) |
| `glossary.jsonl` | 용어 사전 — 원천 ① 정의 조각 280 + ② 구기준 기울임-콜론 + ③ 감사기준 정의 문단 |
| `manifest.json` | 재구축 계약서: 코퍼스 커밋 해시·컬렉션·UUID5 네임스페이스·임베딩/스파스 파라미터. 동일 내용이 메타 컬렉션에도 적재되며 **3단계 서버는 메타 컬렉션의 사본을 읽어** 모델 동일성 불일치면 기동 거부한다 (이 파일은 재구축·감사용 기록) |
| `build_report.md` | 최근 실행의 점검 1~7·스모크 S1~S9·P1~P3 결과 |
| `cache/` | dense 임베딩 캐시(.npy) — gitignore, 소모품 |

재구축: `.venv/bin/python scripts/build_index.py` (접속 정보는 `.env`의 `QDRANT_URL`/`QDRANT_API_KEY` — 커밋 금지).

### dense 임베딩의 Colab GPU 이관 경로

CPU 임베딩(1~2시간)을 피하기 위해 dense만 외부 GPU로 계산할 수 있다:

1. `--stage export` → `index/embed_input.jsonl` (행당 `{"cid", "text"}`, 20,552행 assert — v2: 정본 10,062 + BC·IE 갈래 10,474 + 분할 순증 16)
2. Colab에서 bge-m3(fp16)로 임베딩 → `embeddings.npy`(float32, L2 정규화)·`cids.json`(npy 행 순서의 cid 목록)·`embed_meta.json`(`embedding_device`/`embedding_runtime`)을 `index/`에 배치
3. `--stage upsert --embeddings index/embeddings.npy --cids index/cids.json`
   — 무결성 검증: 건수 20,552 / cid 집합의 파서 산출 일치(순서가 아니라 **cid 조인**으로 결합) /
   shape (20552,1024)·float32(이탈 시 변환+경고) / L2 노름≈1(이탈 시 재정규화+경고)

`embeddings.npy`·`cids.json`은 gitignore(바이너리). 대신 manifest에
`embeddings_sha256`·`embedding_device`·`embedding_runtime`을 기록한다.
`embed_input.jsonl`은 **커밋 대상으로 전환** — Colab이 저장소 raw에서 입력을 직접 확보하는
워크플로(embedding.ipynb 셀 2)를 위해서다. `--stage export` 재실행으로 언제든 재생성 가능.
Colab(tarball, `.git` 부재) 실행 시 manifest의 코퍼스 커밋은 `CORPUS_COMMIT` 환경변수 폴백으로
기록한다(노트북 셀 2가 GitHub API에서 조회해 설정).

## 적재 계약 요약

- 포인트 ID = `uuid5(프로젝트 네임스페이스, 복합ID)` — 결정적, 멱등 재적재. 복합 ID는 payload `composite_id`.
- payload에 `document`(문단 본문 원문) 포함 + payload 전용 메타 컬렉션 `standards_20250829_bgem3_meta`
  (manifest 1 + vocab 1 + glossary 전건) — 3단계 서버의 DB 단일 소스. 메타 컬렉션은 파생물이라
  적재 시 삭제 후 전량 재생성. **재적재는 재임베딩 불필요** — 기존 `embeddings.npy`/`cids.json`으로
  `--stage upsert` 재실행.
- sparse = kiwipiepy(NNG·NNP·XR·SL·SN·SH) + BM25 문서측 가중(k1=1.5, b=0.75), dense = bge-m3 1024차원
  합성문(`{유형} {번호} {제목} | {절 경로} | {본문}`) 전문 임베딩.
- DB는 소모품 원칙 — 마크다운 원본 + 결정적 UUID5 + 이 폴더의 커밋 산출물만으로 언제든 멱등 재구축된다.

구현 확정·이탈 기록 전수(D-01~D-10: 물리/논리 키, 분할·seq, 용어 사전 추출 규칙, 임베딩 정책,
메타 컬렉션 등)와 결함 이력(C-14 standard_title 오염)은 [`docs/LEDGER.md`](../docs/LEDGER.md) 참조.
