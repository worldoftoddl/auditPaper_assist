#!/usr/bin/env python3
"""적재기(2단계): corpus_md/ + guidelines_md/ → Qdrant Cloud.

지시서: TASK.md (v2 — Qdrant판). 참조 규약: guidelines_raw/벡터저장소_스키마_및_마크다운_작성규약.md (v2.1)

단계: [A]파서 → [B]para_type 유도 → [C]분할(240::부록1) → [D]dense 임베딩 →
[E]sparse 벡터 → [F]용어 사전 → [G]Qdrant 업서트 → [H]점검 1~7 + 스모크 S1~S9

사용법:
  build_index.py --stage export    # 파서·합성만 재실행 → index/embed_input.jsonl (Colab GPU 임베딩용)
  build_index.py --stage offline   # 파서~임베딩 캐시·vocab·glossary (Qdrant 불필요)
  build_index.py --stage upsert \
      --embeddings index/embeddings.npy --cids index/cids.json
                                   # 외부(Colab) 임베딩 → Qdrant 적재 + 점검 + 스모크 + 매니페스트
  build_index.py                   # 전체 로컬 (--stage all)

외부 임베딩 메타(장비·런타임)는 index/embed_meta.json(선택)에서 읽어 매니페스트에 기록:
  {"embedding_device": "colab-T4-fp16", "embedding_runtime": "FlagEmbedding 1.3.x"}

접속 정보는 환경변수 QDRANT_URL / QDRANT_API_KEY 로만 전달한다 (.env 자동 로드, gitignore 대상).
"""

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from normalize_corpus import HEAD_RE, gloss_norm  # 행머리·용어 정규화 공유 (복제 금지)

ROOT = Path(__file__).resolve().parent.parent
INDEX_DIR = ROOT / "index"
CACHE_DIR = INDEX_DIR / "cache"

# ── 상수 (지시서 0·2·5·7장) ──────────────────────────────────────────
TYPE_CODE = {"감사기준": "KSA", "회계기준": "KIFRS", "실무지침": "GUIDE"}
EXPECTED_COUNTS = {"감사기준": 3630, "회계기준": 6255, "실무지침": 177}  # 2장 5호 총계 고정
EXPECTED_TOTAL = 10062
EXPECTED_POINTS = 10063          # 분할로 부록1 1건 → 2조각 (순증 +1)
EXPECTED_DEF = 280               # para_type=정의
EXPECTED_REF = 14                # para_type=참조

COLLECTION = "standards_20250829_bgem3"
MODEL_NAME = "BAAI/bge-m3"
DENSE_DIM = 1024
MAX_TOKENS = 8192
NS_STRING = "github.com/worldoftoddl/auditPaper_assist"
PROJECT_NS = uuid.uuid5(uuid.NAMESPACE_URL, NS_STRING)

BM25_K1, BM25_B = 1.5, 0.75
# kiwipiepy 형태소 중 sparse 색인에 남기는 품사: 명사(NNG/NNP)·어근(XR)·영문(SL)·숫자(SN)·한자(SH)
KEEP_TAGS = {"NNG", "NNP", "XR", "SL", "SN", "SH"}

# [C] 필수 분할 1건 — KSA::240::부록1, 최상위 범주 경계(부정한 재무보고/자산의 횡령)에서 2조각
SPLIT_TARGET = ("KSA", "240", "부록1")
SPLIT_MARKER = "자산의 횡령으로 인한 왜곡표시"   # 이 텍스트로 시작하는 본문 행 앞에서 절단

# [F] 용어 사전 — 정의 절 로케이터: section_path 말단 세그먼트의 완전 일치(괄호 참조 변형 허용)
DEF_SECTION_RE = re.compile(r"^(용어의\s*)?정의\s*(\([^)]*\))?$")
# 구기준 기울임-콜론 표기 두 변형: *용어:* (콜론이 기울임 안) / *용어*: (콜론이 기울임 밖)
ITALIC_TERM_RE = re.compile(r"\*([^*\n:：]{1,60}?)\s*(?:[:：]\s*\*|\*\s*[:：])")
# 감사기준 정의 항목: (a)류 마커 + 용어 + 대시 분리자(– — ― -). 대시가 앞머리에 없으면 정의 항목이 아님
ISA_ITEM_RE = re.compile(r"^\s*\(([a-z]{1,4})\)\s*(.+)$")
ISA_DASH_RE = re.compile(r"\s*[–—―-]\s+|\s+[–—―-]\s*")


# ══════════════════════════════ [A] 파서 ══════════════════════════════

def parse_frontmatter(text):
    assert text.startswith("---\n"), "frontmatter 없음"
    end = text.index("\n---\n", 4)
    fm = {}
    for line in text[4:end].split("\n"):
        if ":" in line:
            k, v = line.split(":", 1)
            fm[k.strip()] = v.strip().strip('"')
    return fm, text[end + 5:]


def parse_file(path):
    """한 파일 → 문단 레코드 목록.

    규칙(지시서 2장): `## ` 행으로 절 추적, HEAD_RE 절단, 절 제목 행은 본문 불포함.
    예외: 절 행 뒤 다음 행머리 전에 일반 텍스트가 이어지면(문단이 절을 가로지르는 구조,
    예: KSA 240 부록1) 그 절 행의 말단 세그먼트를 소제목 텍스트로 본문에 보존한다.
    """
    fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
    st, no, title = fm["source_type"], fm["standard_no"], fm["standard_title"]
    code = TYPE_CODE[st]
    records = []
    section = ""          # 최신 `## ` 절 제목
    cur = None            # 진행 중 문단: [para_no, section_at_head, lines]
    pending_secs = []     # 문단 진행 중 만난 절 행(아직 본문 편입 미확정)

    def flush():
        nonlocal cur
        if cur is None:
            return
        para_no, sec, lines = cur
        while lines and not lines[-1].strip():
            lines.pop()
        doc = "\n".join(lines)
        records.append({
            "composite_id": f"{code}::{no}::{para_no}",
            "source_type": st, "standard_no": no, "standard_title": title,
            "para_no": para_no, "section_path": sec,
            "document": doc, "char_len": len(doc),
            "has_table": any(l.lstrip().startswith("|") for l in lines),
            "source_file": path.name,
        })
        cur = None

    for line in body.split("\n"):
        m = HEAD_RE.match(line)
        if m:
            flush()
            pending_secs = []
            cur = [m.group(1), section, [line[m.end():]]]
        elif line.startswith("## "):
            section = line[3:].strip()
            if cur is not None:
                pending_secs.append(section)
        elif cur is not None:
            if line.strip():
                # 절 행을 가로지른 문단 — 보류 중이던 절의 말단 세그먼트를 소제목으로 편입
                for s in pending_secs:
                    cur[2].append(s.split(" > ")[-1])
                pending_secs = []
                cur[2].append(line)
            elif not pending_secs:
                cur[2].append(line)  # 문단 내 빈 행 유지(보류 절이 없을 때만)
    flush()

    for i, r in enumerate(records, 1):
        r["seq"] = i
    return records


def parse_corpus():
    files = sorted((ROOT / "corpus_md").glob("*.md")) + sorted((ROOT / "guidelines_md").glob("*.md"))
    files = [f for f in files if f.name != "README.md"]
    records = []
    for f in files:
        records.append(parse_file(f)) if False else records.extend(parse_file(f))
    # 총계 고정 검증 (드리프트 방지 장치)
    by_type = Counter(r["source_type"] for r in records)
    for st, exp in EXPECTED_COUNTS.items():
        assert by_type[st] == exp, f"총계 불일치: {st} {by_type[st]} != {exp}"
    assert len(records) == EXPECTED_TOTAL, f"총계 {len(records)} != {EXPECTED_TOTAL}"
    # 복합 ID 중복 사전 검사 (발견 즉시 실패)
    dup = [k for k, v in Counter(r["composite_id"] for r in records).items() if v > 1]
    assert not dup, f"복합 ID 중복: {dup[:5]}"
    return records


# ══════════════════════════ [B] para_type 유도 ══════════════════════════

def derive_para_type(r):
    p, st = r["para_no"], r["source_type"]
    if p.startswith("정의-"):
        return "정의"
    if re.fullmatch(r"참조\d+", p):
        return "참조"
    if re.fullmatch(r"부록\d+|부록-사례\d+|사례[A-Z]", p):
        return "부록"
    if st == "감사기준":
        return "요구사항" if re.fullmatch(r"\d+(\.\d+)*", p) else "적용지침"
    if st == "회계기준":
        return "본문" if re.fullmatch(r"한?\d[0-9.]*", p) else "적용지침"
    return "본문"  # 실무지침


# ══════════════════════════ [C] 분할: 240::부록1 ══════════════════════════

def split_records(records):
    out, done = [], False
    for r in records:
        key = (TYPE_CODE[r["source_type"]], r["standard_no"], r["para_no"])
        if key != SPLIT_TARGET:
            out.append(r)
            continue
        lines = r["document"].split("\n")
        cut = next(i for i, l in enumerate(lines) if l.strip().startswith(SPLIT_MARKER))
        assert cut > 0
        parts = ["\n".join(lines[:cut]).rstrip(), "\n".join(lines[cut:]).rstrip()]
        for n, text in enumerate(parts, 1):
            pr = dict(r)
            pr.update(composite_id=f"{r['composite_id']}#{n}", document=text,
                      char_len=len(text), part_no=n, part_total=2,
                      has_table=any(l.lstrip().startswith("|") for l in text.split("\n")))
            out.append(pr)
        done = True
    assert done, f"분할 대상 {SPLIT_TARGET} 미발견"
    return out


# ══════════════════════════ [D] 합성문 ══════════════════════════

def synth_text(r):
    head = f"{r['source_type']} {r['standard_no']} {r['standard_title']}"
    sec = r["section_path"]
    if "part_no" in r:
        sec += f" ({r['part_no']}/{r['part_total']})"
    if r["para_type"] == "부록":
        sec += " · 부록 예시"
    return f"{head} | {sec} | {r['document']}"


# ══════════════════════════ [F] 용어 사전 파생 ══════════════════════════

def build_glossary(records):
    entries, stats = [], Counter()
    for r in records:
        base = {"source_id": r["composite_id"], "standard_no": r["standard_no"],
                "source_type": r["source_type"]}
        # 원천 ① para_type="정의" (본문 = "{용어}: {정의문}")
        if r["para_type"] == "정의":
            term, _, definition = r["document"].partition(":")
            entries.append({"term": term.strip(), "term_norm": gloss_norm(term),
                            "definition": definition.strip(), **base})
            stats["①정의조각"] += 1
            continue
        last_seg = r["section_path"].split(" > ")[-1]
        if not DEF_SECTION_RE.fullmatch(last_seg):
            continue
        if r["source_type"] == "회계기준":
            # 원천 ② 구기준 기울임-콜론 표기 *{용어}:*
            doc = r["document"]
            marks = list(ITALIC_TERM_RE.finditer(doc))
            for i, m in enumerate(marks):
                end = marks[i + 1].start() if i + 1 < len(marks) else len(doc)
                definition = re.sub(r"\*+", "", doc[m.end():end]).strip().rstrip("*").strip()
                term = re.sub(r"\*+", "", m.group(1)).strip()
                entries.append({"term": term, "term_norm": gloss_norm(term),
                                "definition": definition, **base})
                stats[f"②구기준:{r['source_file']}"] += 1
        elif r["source_type"] == "감사기준":
            # 원천 ③ — 실측 표제 4형식 (형식 확정 근거는 corpus README 2단계 절 참조)
            found = 0
            for line in r["document"].split("\n"):
                stripped = re.sub(r"^\s*[-•]\s*", "", line.strip())  # ASSR-3000의 '- ' 항목 마커
                im = ISA_ITEM_RE.match(stripped)
                body = im.group(2) if im else stripped
                dm = ISA_DASH_RE.search(body)
                term = definition = None
                if dm and dm.start() <= 40:
                    # 형식 ⑴/⑶: {용어} – {정의} ((x) 마커 유무 불문)
                    term, definition = body[:dm.start()], body[dm.end():]
                elif im:
                    # 형식 ⑵: (x) {용어(영문)}. {정의} — 1100
                    pm = re.match(r"([^.]{2,50}?(\([A-Za-z ,'-]+\))?)\.\s+(.{10,})", body)
                    if pm:
                        term, definition = pm.group(1), pm.group(3)
                if term and definition and len(term.strip()) <= 40 and len(definition.strip()) >= 5:
                    term = re.sub(r"^[a-z]{1,3}\.\s*", "", term.strip())  # 하위 항목 마커 제거 (ASSR-3000)
                    entries.append({"term": term, "term_norm": gloss_norm(term),
                                    "definition": definition.strip(), **base})
                    stats[f"③감사기준:{r['source_file']}"] += 1
                    found += 1
            if not found:
                # 형식 ⑷ 문단 단위 폴백: 산문형 "…사용되는 {용어}(이)란 …" (320·520)
                pm = re.search(r"사용(?:하는|되는)\s*[“\"]?([^“”\"\s]{2,25}?)[”\"]?이?란\s", r["document"])
                if pm:
                    term = pm.group(1)
                    entries.append({"term": term, "term_norm": gloss_norm(term),
                                    "definition": r["document"].strip(), **base})
                    stats[f"③감사기준:{r['source_file']}"] += 1
    return entries, stats


# ══════════════════════════ [E] sparse 벡터 ══════════════════════════

def build_sparse(texts):
    from kiwipiepy import Kiwi
    kiwi = Kiwi()
    docs_tokens = []
    for i, t in enumerate(texts):
        toks = [tk.form.lower() if tk.tag == "SL" else tk.form
                for tk in kiwi.tokenize(t) if tk.tag in KEEP_TAGS]
        docs_tokens.append(toks)
        if (i + 1) % 2000 == 0:
            print(f"  sparse 토큰화 {i + 1}/{len(texts)}")
    vocab = {tok: idx for idx, tok in enumerate(sorted({t for d in docs_tokens for t in d}))}
    avgdl = sum(len(d) for d in docs_tokens) / len(docs_tokens)
    vectors = []
    for toks in docs_tokens:
        dl = len(toks)
        tf = Counter(toks)
        idx_val = sorted(
            (vocab[tok], c * (BM25_K1 + 1) / (c + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl)))
            for tok, c in tf.items())
        vectors.append(([i for i, _ in idx_val], [v for _, v in idx_val]))
    import kiwipiepy
    meta = {"tokenizer": "kiwipiepy", "kiwipiepy_version": kiwipiepy.__version__,
            "keep_tags": sorted(KEEP_TAGS), "lowercase_SL": True,
            "k1": BM25_K1, "b": BM25_B, "avgdl": round(avgdl, 4),
            "vocab_size": len(vocab), "doc_count": len(texts),
            "query_side": "토큰당 1.0 (IDF는 Qdrant 서버가 적용)"}
    return vocab, vectors, meta


# ══════════════════════════ [D] dense 임베딩 ══════════════════════════

def fingerprint(texts):
    h = hashlib.sha256(MODEL_NAME.encode())
    for t in texts:
        h.update(b"\x00" + t.encode())
    return h.hexdigest()[:16]


def build_dense(texts):
    import numpy as np
    fp = fingerprint(texts)
    cache = CACHE_DIR / f"dense_{fp}.npy"
    if cache.exists():
        print(f"  dense 캐시 재사용: {cache.name}")
        return np.load(cache), fp
    from sentence_transformers import SentenceTransformer
    print(f"  {MODEL_NAME} 로드 중 (CPU)...")
    model = SentenceTransformer(MODEL_NAME)
    tok = model.tokenizer
    over = [(i, n) for i, t in enumerate(texts)
            if (n := len(tok(t, add_special_tokens=True)["input_ids"])) > MAX_TOKENS]
    if over:  # 절단 금지 — 실패 목록 기록 후 사람 판단
        raise SystemExit(f"[중단] {MAX_TOKENS} 토큰 초과 {len(over)}건: {over[:10]}")
    vecs = model.encode(texts, batch_size=16, normalize_embeddings=True,
                        show_progress_bar=True, convert_to_numpy=True).astype("float32")
    assert vecs.shape == (len(texts), DENSE_DIM)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    np.save(cache, vecs)
    return vecs, fp


# ══════════════════════════ [G] Qdrant ══════════════════════════

def load_env():
    envf = ROOT / ".env"
    if envf.exists():
        for line in envf.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def get_client():
    load_env()
    url, key = os.environ.get("QDRANT_URL"), os.environ.get("QDRANT_API_KEY")
    if not url:
        raise SystemExit("[중단] QDRANT_URL / QDRANT_API_KEY 환경변수(.env) 필요 — "
                         "Qdrant Cloud 클러스터 생성 후 .env에 기록하세요.")
    from qdrant_client import QdrantClient
    return QdrantClient(url=url, api_key=key, timeout=120)


def ensure_collection(client, name):
    from qdrant_client import models as qm
    if not client.collection_exists(name):
        client.create_collection(
            collection_name=name,
            vectors_config={"dense": qm.VectorParams(size=DENSE_DIM, distance=qm.Distance.COSINE)},
            sparse_vectors_config={"sparse": qm.SparseVectorParams(modifier=qm.Modifier.IDF)},
        )
    for field, ftype in [("composite_id", qm.PayloadSchemaType.KEYWORD),
                         ("source_type", qm.PayloadSchemaType.KEYWORD),
                         ("standard_no", qm.PayloadSchemaType.KEYWORD),
                         ("para_no", qm.PayloadSchemaType.KEYWORD),
                         ("para_type", qm.PayloadSchemaType.KEYWORD),
                         ("seq", qm.PayloadSchemaType.INTEGER),
                         ("part_no", qm.PayloadSchemaType.INTEGER)]:
        try:
            client.create_payload_index(name, field_name=field, field_schema=ftype)
        except Exception:
            pass  # 이미 존재 — 멱등


def upsert_all(client, records, dense, sparse_vecs):
    from qdrant_client import models as qm
    points = []
    for r, dv, (si, sv) in zip(records, dense, sparse_vecs):
        payload = {k: r[k] for k in ("composite_id", "source_type", "standard_no",
                                     "standard_title", "para_no", "para_type", "section_path",
                                     "seq", "has_table", "char_len", "source_file")}
        if "part_no" in r:
            payload["part_no"], payload["part_total"] = r["part_no"], r["part_total"]
        points.append(qm.PointStruct(
            id=str(uuid.uuid5(PROJECT_NS, r["composite_id"])),
            vector={"dense": dv.tolist(), "sparse": qm.SparseVector(indices=si, values=sv)},
            payload=payload))
    for i in range(0, len(points), 256):
        client.upsert(COLLECTION, points=points[i:i + 256], wait=True)
        print(f"  업서트 {min(i + 256, len(points))}/{len(points)}")


# ══════════════════════════ [H] 점검 + 스모크 ══════════════════════════

def qfilter(**kw):
    from qdrant_client import models as qm
    return qm.Filter(must=[qm.FieldCondition(key=k, match=qm.MatchValue(value=v))
                           for k, v in kw.items()])


def run_checks(client, records, report):
    from qdrant_client import models as qm
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        report.append(f"  [{'PASS' if cond else 'FAIL'}] {name} {detail}")

    report.append("── 점검 1~7 (규약 5장) ──")
    n_points = client.count(COLLECTION, exact=True).count
    check("1 문단 수", n_points == EXPECTED_POINTS, f"포인트 {n_points} (기대 {EXPECTED_POINTS})")
    # 2 연속성: seq 1..N (분할 조각은 원문단의 seq 공유 — 파서 산출 기준)
    bad_seq = []
    by_std = defaultdict(set)
    for r in records:
        by_std[(r["source_type"], r["standard_no"])].add(r["seq"])
    for k, seqs in by_std.items():
        if sorted(seqs) != list(range(1, len(seqs) + 1)):
            bad_seq.append(k)
    check("2 seq 연속성", not bad_seq, f"이상 {bad_seq[:3]}")
    ids = [r["composite_id"] for r in records]
    uuids = {str(uuid.uuid5(PROJECT_NS, i)) for i in ids}
    check("3 ID 유일성", len(set(ids)) == len(ids) == len(uuids) == n_points,
          f"복합 {len(set(ids))} / UUID {len(uuids)} / 포인트 {n_points}")
    top_tables = sorted((r for r in records if r["has_table"]),
                        key=lambda r: -r["char_len"])[:5]
    report.append("  [정보] 4 표 표본(사람 육안): " + ", ".join(r["composite_id"] for r in top_tables))
    # 5 벡터: 전수 스크롤로 dense·sparse 보유 검사
    import math
    n_dense = n_sparse = n_bad = 0
    offset = None
    while True:
        pts, offset = client.scroll(COLLECTION, limit=500, offset=offset,
                                    with_payload=False, with_vectors=True)
        for p in pts:
            dv = p.vector.get("dense")
            sv = p.vector.get("sparse")
            if dv is not None and len(dv) == DENSE_DIM:
                n_dense += 1
                s = sum(x * x for x in dv)
                if not (0.5 < s < 2.0) or any(math.isnan(x) for x in dv[:8]):
                    n_bad += 1
            if sv is not None and len(sv.indices) > 0:
                n_sparse += 1
        if offset is None:
            break
    check("5 벡터", n_dense == n_sparse == n_points and n_bad == 0,
          f"dense {n_dense} / sparse {n_sparse} / 이상 {n_bad}")
    report.append("  [정보] 6 무마침표 — 코퍼스 검증기 승계 (매니페스트의 커밋 해시로 갈음)")
    parts = defaultdict(list)
    for r in records:
        if "part_no" in r:
            parts[(r["standard_no"], r["para_no"])].append((r["part_no"], r["part_total"]))
    part_ok = all(sorted(p for p, _ in v) == list(range(1, v[0][1] + 1)) for v in parts.values())
    check("7 분할 정합", part_ok and list(parts) == [("240", "부록1")], f"{dict(parts)}")
    report.append("  [정보] 8 cross_refs 실재성 — v2 백로그, 보류")
    return ok


def run_smokes(client, records, model, report):
    from qdrant_client import models as qm
    from kiwipiepy import Kiwi
    kiwi = Kiwi()
    vocab = json.loads((INDEX_DIR / "vocab.json").read_text())["tokens"]
    ok = True

    def q_dense(text):
        return model.encode([text], normalize_embeddings=True)[0].tolist()

    def q_sparse(text):
        toks = [t.form.lower() if t.tag == "SL" else t.form
                for t in kiwi.tokenize(text) if t.tag in KEEP_TAGS]
        idx = sorted({vocab[t] for t in toks if t in vocab})
        return qm.SparseVector(indices=idx, values=[1.0] * len(idx))

    def check(name, cond, detail=""):
        nonlocal ok
        ok &= bool(cond)
        report.append(f"  [{'PASS' if cond else 'FAIL'}] {name} {detail}")

    report.append("── 스모크 S1~S9 ──")
    # S1 정확 라우팅
    n315_local = sum(1 for r in records if r["source_type"] == "감사기준" and r["standard_no"] == "315")
    n315 = client.count(COLLECTION, count_filter=qfilter(source_type="감사기준", standard_no="315"),
                        exact=True).count
    check("S1 라우팅 315", n315 == n315_local, f"{n315} = {n315_local}")
    # S2 논리 ID 직조회
    pid = str(uuid.uuid5(PROJECT_NS, "KIFRS::1115::31"))
    rec31 = next(r for r in records if r["composite_id"] == "KIFRS::1115::31")
    got = client.retrieve(COLLECTION, ids=[pid], with_payload=True)
    check("S2 직조회 KIFRS::1115::31",
          got and got[0].payload["composite_id"] == "KIFRS::1115::31"
          and "수행의무를 이행할 때" in rec31["document"], "")
    # S3·S4 dense 의미
    for name, query, want in [
            ("S3 dense '수행의무의 정의'", "수행의무의 정의", ["KIFRS::1115::정의-수행의무"]),
            ("S4 dense '지배력의 세 가지 요소'", "지배력의 세 가지 요소", ["KIFRS::1110::"])]:
        res = client.query_points(COLLECTION, query=q_dense(query), using="dense",
                                  limit=5, with_payload=True)
        tops = [p.payload["composite_id"] for p in res.points]
        hit = any(any(t.startswith(w) or t == w for w in want) for t in tops)
        check(name, hit, f"top5={tops}")
    # S5 서버측 하이브리드 (+ 필터 결합 변형)
    for name, flt in [("S5 하이브리드 '핵심감사사항'", None),
                      ("S5' +감사기준 필터", qfilter(source_type="감사기준"))]:
        dv, sv = q_dense("핵심감사사항"), q_sparse("핵심감사사항")
        res = client.query_points(
            COLLECTION,
            prefetch=[qm.Prefetch(query=dv, using="dense", limit=50, filter=flt),
                      qm.Prefetch(query=sv, using="sparse", limit=50, filter=flt)],
            query=qm.FusionQuery(fusion=qm.Fusion.RRF), limit=5, with_payload=True)
        tops = [p.payload["composite_id"] for p in res.points]
        check(name, any(t.startswith("KSA::701::") for t in tops), f"top5={tops}")
    # S6 성격 필터
    n = client.count(COLLECTION, count_filter=qfilter(para_type="정의", standard_no="1200"),
                     exact=True).count
    check("S6 정의×1200", n == 65, f"{n}건")
    # S7 분할 재조립
    pts, _ = client.scroll(COLLECTION, scroll_filter=qfilter(standard_no="240", para_no="부록1"),
                           limit=10, with_payload=True)
    pn = sorted(p.payload.get("part_no") for p in pts)
    check("S7 부록1 재조립", len(pts) == 2 and pn == [1, 2], f"{len(pts)}건 part_no={pn}")
    # S8 참조 갈래
    n_ref_local = sum(1 for r in records if r["para_type"] == "참조")
    n_ref = client.count(COLLECTION, count_filter=qfilter(para_type="참조"), exact=True).count
    check("S8 참조", n_ref == n_ref_local == EXPECTED_REF, f"{n_ref}건")
    # S9 왕복 변환 전수
    mismatch, offset, seen = 0, None, 0
    local_ids = {r["composite_id"] for r in records}
    while True:
        pts, offset = client.scroll(COLLECTION, limit=1000, offset=offset, with_payload=["composite_id"])
        for p in pts:
            cid = p.payload["composite_id"]
            seen += 1
            if str(uuid.uuid5(PROJECT_NS, cid)) != str(p.id) or cid not in local_ids:
                mismatch += 1
        if offset is None:
            break
    check("S9 왕복 변환 전수", mismatch == 0 and seen == EXPECTED_POINTS,
          f"{seen}건 검사, 불일치 {mismatch}")
    # P1~P3 장문 프로브 (기록용 — 합격 기준 아님)
    report.append("── P1~P3 장문 프로브 (기록용: dense 순위 / 하이브리드 순위) ──")
    for name, query, target in [("P1", "전문가적 의구심의 정의", "KSA::200::13"),
                                ("P2", "일괄상계약정", "KIFRS::1032::50"),
                                ("P3", "재고자산 정의", "KIFRS::1002::6")]:
        dv, sv = q_dense(query), q_sparse(query)
        r1 = client.query_points(COLLECTION, query=dv, using="dense", limit=20, with_payload=True)
        d_rank = next((i + 1 for i, p in enumerate(r1.points)
                       if p.payload["composite_id"] == target), ">20")
        r2 = client.query_points(
            COLLECTION,
            prefetch=[qm.Prefetch(query=dv, using="dense", limit=50),
                      qm.Prefetch(query=sv, using="sparse", limit=50)],
            query=qm.FusionQuery(fusion=qm.Fusion.RRF), limit=20, with_payload=True)
        h_rank = next((i + 1 for i, p in enumerate(r2.points)
                       if p.payload["composite_id"] == target), ">20")
        top3 = [p.payload["composite_id"] for p in r2.points[:3]]
        report.append(f"  [기록] {name} '{query}' → {target}: dense {d_rank}위 / 하이브리드 {h_rank}위"
                      f" (하이브리드 top3: {top3})")
    return ok


# ══════════════════════════ 메인 ══════════════════════════

def prepare():
    """오프라인 공통부: 파서 → 유도 → 분할 → 합성문 → 분포 리포트."""
    records = parse_corpus()
    for r in records:
        r["para_type"] = derive_para_type(r)
    dist = Counter(r["para_type"] for r in records)
    assert dist["정의"] == EXPECTED_DEF, f"정의 {dist['정의']} != {EXPECTED_DEF}"
    assert dist["참조"] == EXPECTED_REF, f"참조 {dist['참조']} != {EXPECTED_REF}"
    records = split_records(records)
    assert len(records) == EXPECTED_POINTS
    texts = [synth_text(r) for r in records]
    return records, texts, dist


def stage_export():
    """파서·합성 경로만 재실행 → Colab GPU 임베딩 입력 파일 생성."""
    records, texts, _ = prepare()
    INDEX_DIR.mkdir(exist_ok=True)
    path = INDEX_DIR / "embed_input.jsonl"
    with open(path, "w", encoding="utf-8") as f:
        for r, t in zip(records, texts):
            f.write(json.dumps({"cid": r["composite_id"], "text": t}, ensure_ascii=False) + "\n")
    n = sum(1 for _ in open(path, encoding="utf-8"))
    assert n == EXPECTED_POINTS, f"embed_input 행 수 {n} != {EXPECTED_POINTS}"
    print(f"embed_input 생성: {path} ({n}행)")
    return path


def load_external_dense(records, emb_path, cids_path, report):
    """Colab 등 외부에서 계산한 dense 임베딩을 cid 조인으로 결합 + 무결성 검증."""
    import numpy as np
    vecs = np.load(emb_path)
    cids = json.loads(Path(cids_path).read_text(encoding="utf-8"))
    # ① 건수 ② cid 집합 일치 (순서가 아니라 조인으로 결합)
    assert len(cids) == EXPECTED_POINTS, f"cids {len(cids)} != {EXPECTED_POINTS}"
    local = {r["composite_id"] for r in records}
    assert set(cids) == local, (f"cid 집합 불일치: 외부에만 {list(set(cids) - local)[:3]}, "
                                f"파서에만 {list(local - set(cids))[:3]}")
    # ③ shape·dtype
    assert vecs.shape == (EXPECTED_POINTS, DENSE_DIM), f"shape {vecs.shape}"
    if vecs.dtype != np.float32:
        report.append(f"  [경고] 외부 임베딩 dtype {vecs.dtype} → float32 변환")
        vecs = vecs.astype(np.float32)
    # ④ L2 노름 ≈ 1 (벗어나면 재정규화 + 경고)
    norms = np.linalg.norm(vecs, axis=1)
    if not np.allclose(norms, 1.0, atol=1e-3):
        report.append(f"  [경고] L2 노름 이탈(범위 {norms.min():.4f}~{norms.max():.4f}) → 재정규화")
        vecs = vecs / norms[:, None]
    order = {c: i for i, c in enumerate(cids)}
    dense = vecs[[order[r["composite_id"]] for r in records]]
    sha = hashlib.sha256(Path(emb_path).read_bytes()).hexdigest()
    report.append(f"외부 dense 결합: {emb_path} (sha256 {sha[:12]}…), cid 조인 {EXPECTED_POINTS}건")
    return dense, sha


def stage_offline(report, skip_dense=False):
    records, texts, dist = prepare()
    report.append(f"파서: {EXPECTED_TOTAL}문단 (감사기준 {EXPECTED_COUNTS['감사기준']} / "
                  f"회계기준 {EXPECTED_COUNTS['회계기준']} / 실무지침 {EXPECTED_COUNTS['실무지침']}) "
                  f"→ 분할 후 {len(records)}레코드")
    report.append(f"para_type 분포: {dict(sorted(dist.items(), key=lambda x: -x[1]))}")

    print("[F] 용어 사전 파생...")
    entries, gstats = build_glossary(records)
    INDEX_DIR.mkdir(exist_ok=True)
    with open(INDEX_DIR / "glossary.jsonl", "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    n1 = gstats["①정의조각"]
    n2 = sum(v for k, v in gstats.items() if k.startswith("②"))
    n3 = sum(v for k, v in gstats.items() if k.startswith("③"))
    assert n1 == EXPECTED_DEF, f"용어 사전 원천① {n1} != {EXPECTED_DEF}"
    report.append(f"용어 사전: 총 {len(entries)}건 = ①정의조각 {n1} + ②구기준 {n2} + ③감사기준 {n3}")
    report.append("  ②구기준 파일별: " + ", ".join(
        f"{k.split(':')[1]}:{v}" for k, v in sorted(gstats.items()) if k.startswith("②")))
    report.append("  ③감사기준 파일별: " + ", ".join(
        f"{k.split(':')[1]}:{v}" for k, v in sorted(gstats.items()) if k.startswith("③")))

    print("[E] sparse 벡터 (kiwipiepy + BM25 문서측 가중)...")
    vocab, sparse_vecs, smeta = build_sparse(texts)
    (INDEX_DIR / "vocab.json").write_text(
        json.dumps({"meta": smeta, "tokens": vocab}, ensure_ascii=False), encoding="utf-8")
    report.append(f"sparse: 어휘집 {smeta['vocab_size']:,} 토큰, avgdl {smeta['avgdl']}")

    if skip_dense:
        report.append("dense: 외부 임베딩 사용 (--embeddings)")
        return records, texts, None, sparse_vecs, smeta
    print("[D] dense 임베딩 (bge-m3, CPU)...")
    dense, fp = build_dense(texts)
    report.append(f"dense: {dense.shape[0]}×{dense.shape[1]}, 캐시 dense_{fp}.npy")
    return records, texts, dense, sparse_vecs, smeta


def stage_upsert(records, texts, dense, sparse_vecs, smeta, report, ext_meta=None):
    client = get_client()
    print(f"[G] Qdrant 적재: {COLLECTION}")
    ensure_collection(client, COLLECTION)
    upsert_all(client, records, dense, sparse_vecs)

    print("[H] 점검 + 스모크...")
    ok1 = run_checks(client, records, report)
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL_NAME)
    ok2 = run_smokes(client, records, model, report)

    # 매니페스트 — 재구축 계약서 (접속 정보 절대 미포함)
    # tarball 실행(Colab 등, .git 부재) 시 CORPUS_COMMIT 환경변수로 폴백
    corpus_commit = (os.popen(f"git -C {ROOT} rev-parse HEAD 2>/dev/null").read().strip()
                     or os.environ.get("CORPUS_COMMIT", "unknown"))
    try:
        rev = model._model_card_vars.get("base_model_revision")
    except Exception:
        rev = None
    embedding_info = {"model": MODEL_NAME, "revision": rev or "main",
                      "normalize_embeddings": True, "dim": DENSE_DIM,
                      "max_tokens": MAX_TOKENS, "library": "sentence-transformers"}
    if ext_meta:  # 외부(Colab) 임베딩 — 장비·런타임·해시 기록
        embedding_info.update(ext_meta)
    manifest = {
        "corpus_commit": corpus_commit,
        "collection": COLLECTION,
        "uuid5_namespace": NS_STRING,
        "embedding": embedding_info,
        "sparse": smeta,
        "paragraphs": EXPECTED_TOTAL, "points": EXPECTED_POINTS,
        "para_type_expected": {"정의": EXPECTED_DEF, "참조": EXPECTED_REF},
        "split": {"KSA::240::부록1": {"parts": 2, "boundary": SPLIT_MARKER}},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (INDEX_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    report.append(f"매니페스트 기록: index/manifest.json (코퍼스 커밋 {corpus_commit[:7]})")
    return ok1 and ok2


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", choices=["export", "offline", "upsert", "all"], default="all")
    ap.add_argument("--embeddings", help="외부 dense 임베딩 .npy (Colab 산출)")
    ap.add_argument("--cids", help="외부 임베딩의 복합 ID 목록 .json (npy 행 순서와 일치)")
    args = ap.parse_args()
    if bool(args.embeddings) != bool(args.cids):
        ap.error("--embeddings와 --cids는 함께 지정해야 합니다")
    if args.stage == "export":
        stage_export()
        return
    report = [f"# 2단계 적재 리포트 ({datetime.now(timezone.utc).isoformat()})"]

    records, texts, dense, sparse_vecs, smeta = stage_offline(report, skip_dense=bool(args.embeddings))
    ext_meta = None
    if args.embeddings:
        dense, sha = load_external_dense(records, args.embeddings, args.cids, report)
        ext_meta = {"embeddings_sha256": sha}
        meta_file = INDEX_DIR / "embed_meta.json"
        if meta_file.exists():
            ext_meta.update(json.loads(meta_file.read_text(encoding="utf-8")))
        ext_meta.setdefault("embedding_device", "unknown")
        ext_meta.setdefault("embedding_runtime", "unknown")
    all_ok = True
    if args.stage in ("upsert", "all"):
        all_ok = stage_upsert(records, texts, dense, sparse_vecs, smeta, report, ext_meta)
    else:
        report.append("(offline 단계만 수행 — Qdrant 적재·점검·스모크 미실행)")

    text = "\n".join(report)
    (INDEX_DIR / "build_report.md").write_text(text + "\n", encoding="utf-8")
    print("\n" + text)
    if args.stage in ("upsert", "all"):
        print("\n" + ("전체 통과" if all_ok else "실패 항목 있음 — 리포트 확인"))
        sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
