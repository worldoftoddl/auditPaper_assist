#!/usr/bin/env python3
"""검색 품질 평가 (품질 감사 2부, 접합 J2·J3): routing_gold 102조서 → Gateway.search
→ 기준서 라우팅 recall@k·MRR. 신구 컬렉션 A/B 비교가 절체 게이트다.

사용법:
  .venv/bin/python eval/audit_retrieval.py --collection standards_20250829_bgem3 \
      --out eval/audit_out/retrieval_v1.json            # 구(v1) 기준선
  .venv/bin/python eval/audit_retrieval.py --out eval/audit_out/retrieval_v2.json
      # 컬렉션 미지정 = build_index.COLLECTION (현행 코드 상수 = v2)

- 트랙 R: 조서 title 원문 질의 (콜드 해석기의 제목 질의 실사용 근사)
- 트랙 C: 정답 누설 패턴("(기준서 210)"·"K-IFRS 제1115호" 류) 제거 질의 (의미 검색 실력)
- gold: worksheets[id]의 KSA∪KIFRS∪GUIDE → {"KSA:315", ...} 집합. 공집합 조서는 제외·보고.
- 지표: recall@5/10/20 (마이크로·매크로), MRR(첫 gold 기준서의 결과 순위 역수).
- 검색은 기본 옵트아웃(정본만) — BC·IE 유입이 정본 검색을 밀어내지 않는지가 J2의 관심사.
- routing_gold.json은 채점 전용(서버 런타임 로드 금지 — 평가 스크립트 사용은 정상 용도).
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))

# title의 정답 누설 패턴 (트랙 C에서 제거)
LEAK_RE = re.compile(
    r"\(\s*(?:기준서|지침|감사기준서?|품질관리기준서?)[^)]*\)"
    r"|K-IFRS\s*제?\s*\d+호?|기업회계기준서\s*제\s*\d+호|회계감사실무지침\s*\d{4}-\d"
    r"|ISA\s*\d+|ISQM\s*-?\s*\d")

KS = [5, 10, 20]


def load_gold():
    gold = json.loads((ROOT / "eval" / "routing_gold.json").read_text(encoding="utf-8"))
    out = {}
    for wid, w in gold["worksheets"].items():
        std = {f"{code}:{no}" for code in ("KSA", "KIFRS", "GUIDE") for no in w.get(code, [])}
        out[wid] = {"title": w["title"], "gold": std}
    return out


def evaluate(gw, worksheets, track, top_k=20):
    rows, skipped = [], 0
    for wid, w in sorted(worksheets.items()):
        if not w["gold"]:
            skipped += 1
            continue
        q = w["title"]
        if track == "C":
            q = re.sub(r"\s{2,}", " ", LEAK_RE.sub(" ", q)).strip() or w["title"]
        res = gw.search(q, top_k=top_k)
        if "error" in res:
            raise SystemExit(f"[중단] {wid} 검색 오류: {res['error']}")
        hits = [f"{r['cid'].split('::')[0]}:{r['standard_no']}" for r in res["results"]]
        row = {"id": wid, "query": q, "gold": sorted(w["gold"]), "hits": hits}
        for k in KS:
            found = set(hits[:k]) & w["gold"]
            row[f"recall@{k}"] = len(found) / len(w["gold"])
            row[f"found@{k}"] = len(found)
        rank = next((i + 1 for i, h in enumerate(hits) if h in w["gold"]), None)
        row["rr"] = 1.0 / rank if rank else 0.0
        rows.append(row)
    agg = {"n": len(rows), "skipped_empty_gold": skipped}
    for k in KS:
        agg[f"macro_recall@{k}"] = round(sum(r[f"recall@{k}"] for r in rows) / len(rows), 4)
        agg[f"micro_recall@{k}"] = round(
            sum(r[f"found@{k}"] for r in rows) / sum(len(r["gold"]) for r in rows), 4)
    agg["mrr"] = round(sum(r["rr"] for r in rows) / len(rows), 4)
    agg["zero_recall@20"] = sorted(r["id"] for r in rows if r["recall@20"] == 0)
    return {"aggregate": agg, "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", help="평가 대상 컬렉션 (기본: build_index.COLLECTION)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--include-bc", action="store_true", help="민감도 분석용 — 기본 미사용")
    args = ap.parse_args()

    import build_index
    from server import contracts
    from server import core
    collection = args.collection or build_index.COLLECTION
    # 컬렉션 오버라이드: contracts·core가 import 시 바인딩한 모듈 속성을 함께 패치
    for mod in (build_index, contracts, core):
        mod.COLLECTION = collection
    contracts.META_COLLECTION = collection + "_meta"

    manifest, vtokens, glossary, client, encoder = contracts.validate(log=lambda m: None)
    gw = core.Gateway(client, encoder, vtokens, glossary)
    worksheets = load_gold()

    result = {"collection": collection, "corpus_commit": manifest.get("corpus_commit"),
              "points": manifest.get("points"), "tracks": {}}
    for track in ("R", "C"):
        print(f"트랙 {track} 평가 중 ({len(worksheets)}조서)...")
        result["tracks"][track] = evaluate(gw, worksheets, track)
        a = result["tracks"][track]["aggregate"]
        print(f"  트랙 {track}: n={a['n']} macro_r@10={a['macro_recall@10']} "
              f"micro_r@10={a['micro_recall@10']} MRR={a['mrr']}")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
