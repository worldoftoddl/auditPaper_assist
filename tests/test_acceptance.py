"""인수 테스트 — MCP 계층 A1~A10 (지시서 v1.1 8장).

실행: .venv/bin/pytest tests/test_acceptance.py -v
전제: .env의 QDRANT_URL/QDRANT_API_KEY, index/ 산출물, bge-m3 로컬 캐시.
세션당 1회 기동 검증 + Gateway 초기화(모델 로드 포함 ~1분) 후 전 테스트가 공유한다.

A7은 지시서 원문(자유 검색)에서 조정: 해당 질의로는 참조 문단이 top20에 미도달(실측)이라
para_type='참조' 필터를 병용해 가드 note를 결정적으로 검증한다 — index/README.md 이탈 기록.
A9는 기동 경로의 단위인 contracts.validate()에 변조한 메타(vocab k1)를 주입해 호출한다.
"""

from types import SimpleNamespace

import pytest


@pytest.fixture(scope="session")
def ctx():
    from server import contracts
    from server.core import Gateway
    try:
        manifest, vtokens, glossary, client, encoder = contracts.validate(log=lambda m: None)
    except contracts.ContractMismatch as e:
        pytest.fail(f"기동 검증 실패 — 인수 테스트 전제 미충족: {e}")
    gw = Gateway(client, encoder, vtokens, glossary)
    return SimpleNamespace(gw=gw, client=client, encoder=encoder)


# ── A1 (P1 승격): 정의 주입이 초장문 정의 문단의 검색 미스를 커버 ──
def test_a1_definition_injection_covers_p1(ctx):
    out = ctx.gw.search("전문가적 의구심의 정의")
    cids = {d["source_cid"] for d in out["definitions"]}
    assert cids & {"KSA::200::13", "KSA::1200::정의-전문가적의구심"}, cids


# ── A2: 문단 직조회 ──
def test_a2_get_paragraph_1115_31(ctx):
    out = ctx.gw.get_paragraph("KIFRS::1115::31")
    assert out["found"]
    assert "수행의무를 이행할 때" in out["paragraphs"][0]["text"]


# ── A3: 분할 문단 재조립 ──
def test_a3_split_reassembly(ctx):
    out = ctx.gw.get_paragraph("KSA::240::부록1")
    assert any("재조립" in n for n in out["notes"]), out["notes"]
    text = out["paragraphs"][0]["text"]
    assert "부정한 재무보고로 인한 왜곡표시" in text          # 조각 #1
    assert "자산의 횡령으로 인한 왜곡표시" in text            # 조각 #2 (절단선)


# ── A4: 하이브리드 + source_type 필터 ──
def test_a4_kam_top5(ctx):
    out = ctx.gw.search("핵심감사사항", source_type=["감사기준"], top_k=5)
    tops = [r["cid"] for r in out["results"]]
    assert any(c.startswith("KSA::701::") for c in tops), tops


# ── A5: 라우팅 필터 + 정의 주입 동시 ──
def test_a5_performance_obligation(ctx):
    out = ctx.gw.search("수행의무의 정의", standard_no=["1115"], top_k=5)
    tops = [r["cid"] for r in out["results"]]
    assert "KIFRS::1115::정의-수행의무" in tops, tops
    assert any(d["source_cid"] == "KIFRS::1115::정의-수행의무" for d in out["definitions"])


# ── A6: 예시류(부록) 옵트인 양방향 ──
def test_a6_examples_opt_in(ctx):
    on = ctx.gw.search("감사보고서 예시 문안", standard_no=["700"],
                       include_examples=True, top_k=8)
    assert any(r["para_type"] == "부록" for r in on["results"]), \
        [r["cid"] for r in on["results"]]
    off = ctx.gw.search("감사보고서 예시 문안", standard_no=["700"],
                        include_examples=False, top_k=8)
    assert all(r["para_type"] != "부록" for r in off["results"])
    assert off["applied"]["examples_excluded"] > 0
    assert any("include_examples=true" in n for n in off["applied"]["notes"])


# ── A7 (조정): 참조 갈래 가드 note ──
def test_a7_reference_guard_note(ctx):
    out = ctx.gw.search("중요성 판단 개념체계 참조", standard_no=["PS2"],
                        para_type="참조", top_k=8)
    assert out["results"], "PS2 참조 문단 미검색"
    for r in out["results"]:
        assert r["para_type"] == "참조"
        assert any("원전 문단 우선 인용" in n for n in r["notes"]), r


# ── A8: 용어 정의 직조회 (포함 일치 폴백 + 문맥 기준서 대표) ──
def test_a8_define_terms(ctx):
    out = ctx.gw.define_terms(["지배력"])
    d = out["definitions"][0]
    assert d["source_cid"].startswith("KIFRS::1110::"), d
    assert d["matched"] == "피투자자에 대한 지배력"

    out2 = ctx.gw.define_terms(["전문가적 의구심"], context_standard="200")
    d2 = out2["definitions"][0]
    assert d2["source_cid"] == "KSA::200::13", d2
    assert d2["alternates"], "복수 정의인데 alternates 없음"


# ── A9 (부정): vocab k1 변조 → CONTRACT_MISMATCH 기동 거부 ──
def test_a9_contract_mismatch_refuses_start(ctx):
    from server import contracts
    manifest, vocab_doc, glossary = contracts.load_meta(ctx.client)
    tampered = {"meta": dict(vocab_doc["meta"], k1=9.9), "tokens": vocab_doc["tokens"]}  # 변조
    with pytest.raises(contracts.ContractMismatch) as ei:
        contracts.validate(client=ctx.client, encoder=ctx.encoder,
                           meta=(manifest, tampered, glossary), log=lambda m: None)
    assert ei.value.code == "CONTRACT_MISMATCH"
    assert ei.value.item == "sparse.k1"
    assert ei.value.hint  # 조치 힌트 필수


# ── A10: 물리 조각 ID 거부 ──
def test_a10_fragment_id_rejected(ctx):
    out = ctx.gw.get_paragraph("KSA::240::부록1#1")
    assert out["error"]["code"] == "INVALID_INPUT"
    assert "논리 ID" in out["error"]["hint"]


# ── A11: BC(결론도출근거) 옵트인 양방향 (규약 4.3-5·include_bc) ──
def test_a11_bc_opt_in(ctx):
    off = ctx.gw.search("리스 인식 면제 결론도출근거", standard_no=["1116"], top_k=8)
    assert all(r["para_type"] != "결론도출근거" for r in off["results"]), \
        [(r["cid"], r["para_type"]) for r in off["results"]]
    assert off["applied"]["bc_excluded"] > 0
    assert any("include_bc=true" in n for n in off["applied"]["notes"])
    on = ctx.gw.search("리스 인식 면제 결론도출근거", standard_no=["1116"],
                       include_bc=True, top_k=8)
    assert any(r["cid"].startswith("KIFRS::1116::BC") for r in on["results"]), \
        [r["cid"] for r in on["results"]]


# ── A12: IE(적용사례)가 include_examples 축에 편입 ──
def test_a12_ie_opt_in(ctx):
    on = ctx.gw.search("계약변경 재화 회계처리 사례", standard_no=["1115"],
                       include_examples=True, top_k=8)
    assert any(r["para_type"] == "적용사례" for r in on["results"]), \
        [(r["cid"], r["para_type"]) for r in on["results"]]
    off = ctx.gw.search("계약변경 재화 회계처리 사례", standard_no=["1115"], top_k=8)
    assert all(r["para_type"] not in ("부록", "적용사례") for r in off["results"])


# ── A13: BC·IE 직조회 + 이웃의 갈래 순수성 (source_file 스코프, 규약 2.2) ──
def test_a13_galley_get_paragraph_context(ctx):
    out = ctx.gw.get_paragraph("KIFRS::1115::IE3", context=2)
    assert out["found"]
    assert all(p["para_type"] == "적용사례" for p in out["paragraphs"]), \
        [(p["cid"], p["para_type"]) for p in out["paragraphs"]]
    # 역방향: 정본 이웃 조회가 갈래 문단에 오염되지 않아야 한다
    out2 = ctx.gw.get_paragraph("KIFRS::1115::31", context=2)
    assert all(p["para_type"] not in ("결론도출근거", "적용사례")
               for p in out2["paragraphs"])


# ── A14: para_type 명시 요청이 옵트아웃보다 우선 (D-18 일반화) ──
def test_a14_explicit_para_type_overrides_optout(ctx):
    out = ctx.gw.search("금융부채 분류 근거", para_type="결론도출근거", top_k=5)
    assert out["results"] and all(r["para_type"] == "결론도출근거" for r in out["results"])
    assert any("명시 요청" in n for n in out["applied"]["notes"])
