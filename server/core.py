"""3단계 코어: Gateway(도구 3종의 실질 구현) + 정책·주입 — MCP 비의존.

지시서: docs/workorders/03_MCP_콜드해석_v1.1.md 4·5장.
텍스트 출처: 컬렉션 payload의 document(적재기 upsert_all 참조) — Qdrant가 랭킹·필터·
본문·이웃·재조립을 모두 담당하는 단일 소스다(지시서 v1.1 '본문은 로컬 파스' 결정에서
이탈 — server/README.md 기록). 서버는 코퍼스 저장소 없이 접속 정보만으로 동작하며,
payload 본문과 파서 산출의 일치는 2단계 S9(왕복+본문 전수 대조)가 보증한다.
"""

import re
import sys
import uuid
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from build_index import (  # noqa: E402
    COLLECTION, KEEP_TAGS, PROJECT_NS, TYPE_CODE,
)
from normalize_corpus import gloss_norm  # noqa: E402

CODE_TO_TYPE = {v: k for k, v in TYPE_CODE.items()}
SOURCE_TYPES = set(TYPE_CODE)
PARA_TYPES = {"정의", "참조", "부록", "요구사항", "적용지침", "본문", "결론도출근거", "적용사례"}
# 검색 기본 노출 옵트인 2축 (규약 2.2, 4차 개정): include_examples = 예시류,
# include_bc = 근거·연혁. para_type 명시 요청은 옵트아웃보다 우선 (D-18 일반화)
EXAMPLE_TYPES = {"부록", "적용사례"}
BC_TYPES = {"결론도출근거"}
FUSION_DESC = "server-RRF (dense+sparse, prefetch 각 50)"
PREFETCH_LIMIT = 50
REF_NOTE = "발췌 대조표 — 원전 문단 우선 인용"
MAX_DEFINITIONS = 5


def err(code, message, hint):
    """행동 지향 오류 봉투 (지시서 4장). collection은 호출측에서 얹는다."""
    return {"error": {"code": code, "message": message, "hint": hint}}


class Gateway:
    """도구 3종의 구현체. client/encoder/vocab/glossary는 contracts.validate()가 검증 후 주입."""

    def __init__(self, client, encoder, vocab_tokens, glossary):
        self.client = client
        self.encoder = encoder
        self.vocab = vocab_tokens
        from kiwipiepy import Kiwi
        self.kiwi = Kiwi()

        # 용어 사전: term_norm → [항목…] (파일 순서 보존 — 대표 선정 폴백 순서)
        self.gloss_by_norm = defaultdict(list)
        for e in glossary:
            self.gloss_by_norm[e["term_norm"]].append(e)
        # 주입 후보 스캔용 (2자 미만 노이즈 제외, 긴 용어 우선)
        self.gloss_norms = sorted(
            (n for n in self.gloss_by_norm if len(n) >= 2), key=len, reverse=True)

    # ── 공통: 컬렉션 조회 + 논리 ID 재조립 ────────────────────────────

    def _fetch_logical(self, source_type, standard_no, para_no):
        """논리 문단 → payload 목록 (분할이면 part_no 순 조각들, 미존재면 빈 목록).

        (source_type, standard_no, para_no) 등가 조회 — 분할 조각들이 para_no를
        공유하므로 일반·분할 문단을 한 호출로 처리한다."""
        from qdrant_client import models as qm
        flt = qm.Filter(must=[
            qm.FieldCondition(key="source_type", match=qm.MatchValue(value=source_type)),
            qm.FieldCondition(key="standard_no", match=qm.MatchValue(value=standard_no)),
            qm.FieldCondition(key="para_no", match=qm.MatchValue(value=para_no))])
        pts, _ = self.client.scroll(COLLECTION, scroll_filter=flt, limit=10, with_payload=True)
        return sorted((p.payload for p in pts), key=lambda r: r.get("part_no", 0))

    def _sibling_payloads(self, payload):
        """검색 히트 payload → 논리 문단 전체 조각 (분할이 아니면 그대로 1건).

        분할 조각의 물리 ID가 결정적(uuid5)이므로 형제 조각은 직조회로 얻는다."""
        if "part_no" not in payload:
            return [payload]
        logical = payload["composite_id"].split("#")[0]
        ids = [str(uuid.uuid5(PROJECT_NS, f"{logical}#{n}"))
               for n in range(1, payload["part_total"] + 1)]
        got = self.client.retrieve(COLLECTION, ids=ids, with_payload=True)
        return sorted((p.payload for p in got), key=lambda r: r["part_no"])

    def _assemble(self, recs, is_context=False):
        """레코드(들) → 출력 문단 dict + note. 분할 조각은 part_no 순으로 본문 결합."""
        first = recs[0]
        text = "\n".join(r["document"] for r in recs)
        note = f"분할 문단 {len(recs)}조각 재조립" if len(recs) > 1 else None
        para = {
            "cid": first["composite_id"].split("#")[0],
            "source_type": first["source_type"],
            "standard_no": first["standard_no"],
            "standard_title": first["standard_title"],
            "para_no": first["para_no"],
            "para_type": first["para_type"],
            "section_path": first["section_path"],
            "seq": first["seq"],
            "text": text,
            "is_context": is_context,
        }
        return para, note

    # ── 도구 1: standards_get_paragraph ──────────────────────────────

    def get_paragraph(self, cid, context=0):
        if "#" in cid:
            return err("INVALID_INPUT", f"'{cid}'는 분할 조각의 물리 ID입니다.",
                       "논리 ID로 조회 — '#' 앞부분 사용")
        seg = cid.split("::")
        if len(seg) != 3 or seg[0] not in CODE_TO_TYPE:
            return err("INVALID_INPUT", f"복합 ID 형식 오류: '{cid}'",
                       "형식: KSA::<번호>::<문단> | KIFRS::… | GUIDE::… (예: KIFRS::1115::31)")
        if not 0 <= context <= 3:
            return err("INVALID_INPUT", f"context={context}", "0~3 범위로 지정")

        recs = self._fetch_logical(CODE_TO_TYPE[seg[0]], seg[1], seg[2])
        if not recs:
            return err("NOT_FOUND", f"'{cid}' 문단이 존재하지 않습니다.",
                       "standards_search로 탐색")

        notes = []
        target, note = self._assemble(recs)
        if note:
            notes.append(note)
        paragraphs = [target]
        if context:
            # 이웃 문단: 같은 원본 파일에서 seq ±context 범위 조회 (분할 조각은 seq 공유).
            # 스코프가 기준서가 아닌 source_file인 이유(규약 2.2, 4차 개정): 한 기준서가
            # 정본·BC·IE 여러 파일로 나뉘어 seq가 파일마다 1부터 시작 — 기준서 스코프로
            # 조회하면 정본 문단 옆에 갈래 문단이 끼어든다.
            from qdrant_client import models as qm
            flt = qm.Filter(must=[
                qm.FieldCondition(key="source_file", match=qm.MatchValue(value=recs[0]["source_file"])),
                qm.FieldCondition(key="seq", range=qm.Range(gte=target["seq"] - context,
                                                            lte=target["seq"] + context))])
            pts, _ = self.client.scroll(COLLECTION, scroll_filter=flt, limit=64, with_payload=True)
            group = defaultdict(list)
            for p in pts:
                group[p.payload["seq"]].append(p.payload)
            for s in sorted(group):
                if s == target["seq"]:
                    continue
                para, n = self._assemble(
                    sorted(group[s], key=lambda r: r.get("part_no", 0)), is_context=True)
                paragraphs.append(para)
                if n:
                    notes.append(f"{para['cid']}: {n}")
            paragraphs.sort(key=lambda p: p["seq"])
        return {"found": True, "paragraphs": paragraphs, "notes": notes}

    # ── 도구 2: standards_search ─────────────────────────────────────

    def _sparse_query(self, text):
        """질의측 sparse: 형태소 KEEP_TAGS → vocab 매핑, 값 1.0. 어휘 밖 토큰은 무시·기록."""
        toks = [t.form.lower() if t.tag == "SL" else t.form
                for t in self.kiwi.tokenize(text) if t.tag in KEEP_TAGS]
        idx, oov, seen = set(), [], set()
        for t in toks:
            if t in self.vocab:
                idx.add(self.vocab[t])
            elif t not in seen:
                oov.append(t)
                seen.add(t)
        return sorted(idx), oov

    def _build_filter(self, standard_no, source_type, para_type, exclude_types=()):
        from qdrant_client import models as qm
        must, must_not = [], []
        if standard_no:
            must.append(qm.FieldCondition(key="standard_no", match=qm.MatchAny(any=list(standard_no))))
        if source_type:
            must.append(qm.FieldCondition(key="source_type", match=qm.MatchAny(any=list(source_type))))
        if para_type:
            must.append(qm.FieldCondition(key="para_type", match=qm.MatchValue(value=para_type)))
        if exclude_types:
            must_not.append(qm.FieldCondition(
                key="para_type", match=qm.MatchAny(any=sorted(exclude_types))))
        if not must and not must_not:
            return None
        return qm.Filter(must=must or None, must_not=must_not or None)

    def _fused_query(self, dense_vec, sparse_idx, flt, limit):
        from qdrant_client import models as qm
        prefetch = [qm.Prefetch(query=dense_vec, using="dense", limit=PREFETCH_LIMIT, filter=flt)]
        if sparse_idx:
            prefetch.append(qm.Prefetch(
                query=qm.SparseVector(indices=sparse_idx, values=[1.0] * len(sparse_idx)),
                using="sparse", limit=PREFETCH_LIMIT, filter=flt))
        return self.client.query_points(
            COLLECTION, prefetch=prefetch, query=qm.FusionQuery(fusion=qm.Fusion.RRF),
            limit=limit, with_payload=True).points

    def search(self, query, standard_no=None, source_type=None, para_type=None,
               top_k=8, include_examples=False, include_bc=False):
        if not query or not 1 <= len(query) <= 500:
            return err("INVALID_INPUT", f"query 길이 {len(query or '')}", "1~500자로 지정")
        if not 1 <= top_k <= 20:
            return err("INVALID_INPUT", f"top_k={top_k}", "1~20 범위로 지정")
        if source_type and (bad := set(source_type) - SOURCE_TYPES):
            return err("INVALID_INPUT", f"source_type 미지원: {sorted(bad)}",
                       f"허용값: {sorted(SOURCE_TYPES)}")
        if para_type and para_type not in PARA_TYPES:
            return err("INVALID_INPUT", f"para_type 미지원: '{para_type}'",
                       f"허용값: {sorted(PARA_TYPES)}")
        applied_notes = []
        # 옵트인 2축: 기본 검색은 정본만 — 예시류(부록·적용사례)는 include_examples,
        # 결론도출근거는 include_bc. 명시적 para_type 요청은 옵트아웃보다 우선한다.
        exclude_types = set()
        if not include_examples:
            exclude_types |= EXAMPLE_TYPES
        if not include_bc:
            exclude_types |= BC_TYPES
        if para_type in exclude_types:
            exclude_types.discard(para_type)
            applied_notes.append(
                f"para_type='{para_type}' 명시 요청 — 옵트아웃보다 우선 적용")

        dense_vec = self.encoder.encode([query], normalize_embeddings=True)[0].tolist()
        sparse_idx, oov = self._sparse_query(query)
        if not sparse_idx:
            applied_notes.append("sparse 질의 토큰 전무(전부 어휘집 밖) — dense 단독 검색")

        flt = self._build_filter(standard_no, source_type, para_type, exclude_types)
        points = self._fused_query(dense_vec, sparse_idx, flt, top_k + 3)  # 분할 중복 여유분

        examples_excluded = bc_excluded = 0
        if exclude_types:
            base_flt = self._build_filter(standard_no, source_type, para_type, ())
            base_pts = self._fused_query(dense_vec, sparse_idx, base_flt, top_k)
            excl = Counter(p.payload["para_type"] for p in base_pts
                           if p.payload["para_type"] in exclude_types)
            examples_excluded = sum(excl[t] for t in EXAMPLE_TYPES)
            bc_excluded = sum(excl[t] for t in BC_TYPES)
            if examples_excluded:
                applied_notes.append(
                    f"예시류(부록·적용사례) {examples_excluded}건 제외 — 문안·예시 작업이면 "
                    "include_examples=true로 재호출")
            if bc_excluded:
                applied_notes.append(
                    f"결론도출근거 {bc_excluded}건 제외 — 기준 제정 근거·연혁 질의면 "
                    "include_bc=true로 재호출")

        results, seen = [], set()
        for p in points:
            logical = p.payload["composite_id"].split("#")[0]
            if logical in seen:
                continue
            seen.add(logical)
            recs = self._sibling_payloads(p.payload)
            para, note = self._assemble(recs)
            notes = [note] if note else []
            if para["para_type"] == "참조":
                notes.append(REF_NOTE)
            results.append({
                "cid": logical, "score": p.score,
                "standard_no": para["standard_no"], "standard_title": para["standard_title"],
                "para_no": para["para_no"], "para_type": para["para_type"],
                "section_path": para["section_path"], "text": para["text"], "notes": notes})
            if len(results) >= top_k:
                break

        definitions = self._inject_definitions(query, results, standard_no)
        filters_echo = {}
        if standard_no:
            filters_echo["standard_no"] = list(standard_no)
        if source_type:
            filters_echo["source_type"] = list(source_type)
        if para_type:
            filters_echo["para_type"] = para_type
        return {"results": results, "definitions": definitions,
                "applied": {"filters": filters_echo, "fusion": FUSION_DESC,
                            "oov_query_tokens": oov,
                            "examples_excluded": examples_excluded,
                            "bc_excluded": bc_excluded, "notes": applied_notes}}

    def _inject_definitions(self, query, results, filter_stds):
        """정의 주입 (지시서 5.2): 질의 어휘 일치 최우선 → 결과 텍스트 빈도순, 캡 5.

        질의 일치 용어는 사용자가 명시적으로 물은 것이므로 동일 cid가 results에 있어도
        주입한다(A5). '동일 cid 생략' 규칙은 결과 파생(빈도순) 용어에만 적용 — README 기록.
        """
        qnorm = re.sub(r"\s+", "", query)
        picked, covered = [], set()
        for norm in self.gloss_norms:                      # 긴 용어 우선
            if norm in qnorm and not any(norm in c for c in covered):
                picked.append((norm, True))
                covered.add(norm)
        rtext = re.sub(r"\s+", "", " ".join(r["text"] for r in results))
        freq = [(n, rtext.count(n)) for n in self.gloss_norms
                if n not in covered and not any(n in c for c in covered)]
        for norm, _cnt in sorted((f for f in freq if f[1] > 0), key=lambda x: -x[1]):
            picked.append((norm, False))
            covered.add(norm)
            if len(picked) >= MAX_DEFINITIONS * 2:         # 대표 선정 전 여유분
                break

        # 문맥 기준서: 필터 → 결과 최빈. 필터는 강한 신호(명시 라우팅), 최빈은 약한 신호.
        ctx_std, ctx_from_filter = None, False
        if filter_stds:
            ctx_std, ctx_from_filter = filter_stds[0], True
        elif results:
            ctx_std = Counter(r["standard_no"] for r in results).most_common(1)[0][0]

        result_cids = {r["cid"] for r in results}
        out = []
        for norm, from_query in picked:
            entries = self.gloss_by_norm[norm]
            entry = self._pick_entry(entries, ctx_std, ctx_from_filter)
            if not from_query and entry["source_id"] in result_cids:
                continue  # 결과 파생 용어는 동일 cid 중복 주입 생략
            out.append({"term": entry["term"], "source_cid": entry["source_id"],
                        "standard_no": entry["standard_no"], "text": entry["definition"]})
            if len(out) >= MAX_DEFINITIONS:
                break
        return out

    @staticmethod
    def _pick_entry(entries, ctx_std, ctx_from_filter):
        """복수 정의 충돌 해소. 지시서 규칙(문맥 기준서 자체 정의 → 정의 보유 기준서)의
        구체화: 명시 필터 문맥은 그대로 우선하되, 결과 최빈은 약한 신호이므로 전용 정의
        조각(::정의- — 부록A 용어집 원전)을 산문 발췌보다 우선한다 (README 이탈 기록)."""
        ctx = [e for e in entries if e["standard_no"] == ctx_std]
        if ctx_from_filter and ctx:
            entries = ctx
        dedicated = [e for e in entries if "::정의-" in e["source_id"]]
        if dedicated:
            return next((e for e in dedicated if e["standard_no"] == ctx_std), dedicated[0])
        return ctx[0] if ctx else entries[0]

    # ── 도구 3: standards_define_terms ───────────────────────────────

    def define_terms(self, terms, context_standard=None):
        if not terms or not 1 <= len(terms) <= 10:
            return err("INVALID_INPUT", f"terms {len(terms or [])}건", "1~10건으로 지정")
        definitions, not_found = [], []
        for raw in terms:
            # 매칭 3단계: 원문 정확 일치 → 공백 제거(term_norm) 일치 → 포함 일치 폴백.
            # 폴백은 사전 표제가 수식어를 동반하는 실측('지배력' → '피투자자에 대한 지배력')
            # 때문의 구체화 — 가장 짧은(가장 근접한) 표제를 고르고 matched로 노출한다.
            entries = [e for es in self.gloss_by_norm.values() for e in es if e["term"] == raw]
            if not entries:
                norm = gloss_norm(raw)
                entries = self.gloss_by_norm.get(norm, [])
                if not entries and len(norm) >= 2:
                    # 어절 일치('피투자자에 대한 지배력'의 어절 '지배력')를
                    # 합성어 부분 문자열('공동지배력')보다 우선
                    word_hits = [n for n, es in self.gloss_by_norm.items()
                                 if any(gloss_norm(w) == norm for w in es[0]["term"].split())]
                    pool = (sorted(word_hits, key=len)
                            or sorted((n for n in self.gloss_by_norm if norm in n), key=len))
                    if pool:
                        entries = self.gloss_by_norm[pool[0]]
            if not entries:
                not_found.append(raw)
                continue
            rep = next((e for e in entries if e["standard_no"] == context_standard), entries[0])
            alternates = [{"source_cid": e["source_id"], "standard_no": e["standard_no"]}
                          for e in entries if e is not rep]
            definitions.append({
                "term": raw, "matched": rep["term"],
                "source_cid": rep["source_id"], "standard_no": rep["standard_no"],
                "source_type": rep["source_type"], "text": rep["definition"],
                "alternates": alternates})
        return {"definitions": definitions, "not_found": not_found}
