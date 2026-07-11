"""MCP 서버 (지시서 v1.1 5장): 도구 3종 — standards_get_paragraph · standards_search ·
standards_define_terms. 기동 시 contracts.validate() 통과 못 하면 기동 거부.

실행: python -m server.mcp_server  (QDRANT_URL/QDRANT_API_KEY는 .env 또는 환경변수)

전송은 기본 stdio. MCP_TRANSPORT=http면 HTTP로 서빙하며(원격 공유용 — colab/ 노트북 참조),
이때 MCP_AUTH_TOKEN(Bearer 정적 토큰)이 없으면 기동 거부한다 — Qdrant 키를 쥔 서버를
무인증으로 공인망에 노출하지 않기 위함. MCP_HOST/MCP_PORT로 바인딩 조정(기본 127.0.0.1:8000).
"""

import os
import sys
import threading
from typing import Annotated, Optional

from pydantic import Field

from server import contracts
from server.core import COLLECTION, Gateway

ANNOTATIONS = {"readOnlyHint": True, "destructiveHint": False,
               "idempotentHint": True, "openWorldHint": False}
ENCODER_WAIT_SEC = 300

_gateway = None                      # 기동 검증 후 주입
_encoder_ready = threading.Event()   # bge-m3 지연 로드 완료 신호 (search만 대기)


def _wrap(payload):
    """모든 도구 출력 최상위에 collection 포함 (인용 판본 고정 — 규약 3.1)."""
    return {"collection": COLLECTION, **payload}


def _guard(fn, *args, **kwargs):
    from qdrant_client.http.exceptions import ApiException, ResponseHandlingException
    try:
        return _wrap(fn(*args, **kwargs))
    except (ApiException, ResponseHandlingException, ConnectionError) as e:
        return _wrap({"error": {"code": "UPSTREAM_UNAVAILABLE",
                                "message": f"Qdrant 호출 실패: {e}",
                                "hint": "네트워크·클러스터 상태 확인 후 재시도 (무료 티어 휴면이면 콘솔에서 재개)"}})


def _static_token_auth(token):
    """HTTP 전송용 정적 Bearer 토큰 검증기 — 상수시간 비교, 불일치는 401."""
    import hmac
    from fastmcp.server.auth import AccessToken, TokenVerifier

    class _StaticTokenVerifier(TokenVerifier):
        async def verify_token(self, presented):
            if hmac.compare_digest(presented, token):
                return AccessToken(token=presented, client_id="auditpaper-remote", scopes=[])
            return None

    return _StaticTokenVerifier()


def build_app(auth=None):
    from fastmcp import FastMCP
    mcp = FastMCP("auditpaper-standards", auth=auth)

    @mcp.tool(annotations=ANNOTATIONS)
    def standards_get_paragraph(
        cid: Annotated[str, Field(description="복합 ID(논리), '#순번' 없이. 예: KIFRS::1115::31")],
        context: Annotated[int, Field(0, ge=0, le=3, description="같은 기준서 seq ±N 이웃 문단 포함(0~3)")] = 0,
    ) -> dict:
        """기준서 문단 직조회·인용 검증. 복합 ID로 원문 실물을 확인하고, 분할 문단은
        재조립해 돌려준다. context=N이면 같은 기준서의 이웃 문단(seq ±N)을 함께 반환한다.
        해석 보고서의 인용 cid 검증(제출 전 최소 3건 재조회)에 이 도구를 사용할 것."""
        return _guard(_gateway.get_paragraph, cid, context)

    @mcp.tool(annotations=ANNOTATIONS)
    def standards_search(
        query: Annotated[str, Field(min_length=1, max_length=500, description="자연어 질의(1~500자)")],
        standard_no: Annotated[Optional[list[str]], Field(
            description="기준서 번호 필터. 조서에서 명시 참조를 발견했을 때의 라우팅 필터 (예: ['1115'])")] = None,
        source_type: Annotated[Optional[list[str]], Field(
            description="감사기준 | 회계기준 | 실무지침")] = None,
        para_type: Annotated[Optional[str], Field(
            description="문단 성격 필터: 정의·참조·부록·요구사항·적용지침·본문")] = None,
        top_k: Annotated[int, Field(8, ge=1, le=20)] = 8,
        include_examples: Annotated[bool, Field(
            False, description="예시류(부록) 포함 스위치 — 문안·예시 작성 작업이면 true")] = False,
    ) -> dict:
        """기준서 하이브리드 검색(dense+sparse, 서버 RRF) + 관련 용어 정의 주입.
        기본적으로 예시류(부록)는 제외된다 — 감사보고서 문안·예시가 필요하면
        include_examples=true. 히트 문단의 이웃 확장은 이 도구가 아니라
        standards_get_paragraph(cid, context=N)을 사용할 것."""
        if not _encoder_ready.wait(timeout=ENCODER_WAIT_SEC):
            return _wrap({"error": {"code": "UPSTREAM_UNAVAILABLE",
                                    "message": "질의측 인코더(bge-m3) 로드가 아직 끝나지 않음",
                                    "hint": "잠시 후 재시도 — 기동 직후 1분 내 정상화"}})
        return _guard(_gateway.search, query, standard_no, source_type,
                      para_type, top_k, include_examples)

    @mcp.tool(annotations=ANNOTATIONS)
    def standards_define_terms(
        terms: Annotated[list[str], Field(min_length=1, max_length=10,
                                          description="정의를 찾을 용어 1~10건")],
        context_standard: Annotated[Optional[str], Field(
            description="문맥 기준서 번호 — 복수 정의 충돌 시 이 기준서의 정의를 대표로 선택")] = None,
    ) -> dict:
        """용어 사전(코퍼스 정의 절 파생 664건) 직조회. 원문 일치 → 공백 제거 일치 순으로
        매칭하고, 복수 정의는 대표 1건 본문 + 나머지 alternates 포인터로 돌려준다."""
        return _guard(_gateway.define_terms, terms, context_standard)

    return mcp


def _load_encoder_background(manifest, log):
    """bge-m3 지연 로드 (검사 ①차원·⑥프로브 완결). 실패 시 프로세스 종료 — 기동 거부 보존."""
    try:
        _gateway.encoder = contracts.validate_encoder(manifest, log=log)
        _encoder_ready.set()
        log("[mcp_server] 인코더 준비 완료 — standards_search 활성")
    except Exception as e:
        log(f"[기동 거부] 인코더 계약 검증 실패: {e}")
        os._exit(1)


def main():
    global _gateway
    log = lambda m: print(m, file=sys.stderr)  # noqa: E731
    try:
        # 모델 비의존 계약(sparse·토크나이저·컬렉션·glossary)은 즉시 검증 — 위반 시 즉시 거부.
        # 모델 의존 검사(①차원·⑥프로브)는 백그라운드로 완결한다: bge-m3 로드(~1분)를
        # 기다리면 MCP initialize 응답이 클라이언트 기동 타임아웃을 넘기기 때문.
        manifest, vtokens, glossary, client, _ = contracts.validate(
            log=log, defer_encoder=True)
    except contracts.ContractMismatch as e:
        print(f"[기동 거부] {e}", file=sys.stderr)
        sys.exit(1)
    log("[mcp_server] Gateway 초기화 (본문·용어사전·vocab: Qdrant 단일 소스)...")
    _gateway = Gateway(client, None, vtokens, glossary)
    threading.Thread(target=_load_encoder_background, args=(manifest, log), daemon=True).start()
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "http":
        token = os.environ.get("MCP_AUTH_TOKEN", "")
        if len(token) < 16:
            print("[기동 거부] HTTP 전송은 MCP_AUTH_TOKEN(16자 이상) 필수 — 무인증 공개 노출 방지",
                  file=sys.stderr)
            sys.exit(1)
        host, port = os.environ.get("MCP_HOST", "127.0.0.1"), int(os.environ.get("MCP_PORT", "8000"))
        # FastMCP의 Host 헤더 가드(DNS rebinding 방어)는 기본 localhost만 허용해 프록시·터널
        # 뒤에서는 공개 도메인 Host에 421을 낸다. 접근 통제는 Bearer 토큰이 담당하므로
        # (브라우저는 교차 출처로 Authorization 헤더를 못 실어 rebinding 실익 없음)
        # 기본을 전체 허용으로 열고, 좁히려면 MCP_ALLOWED_HOSTS(쉼표 구분)로 지정한다.
        allowed = [h.strip() for h in os.environ.get("MCP_ALLOWED_HOSTS", "*").split(",") if h.strip()]
        app = build_app(auth=_static_token_auth(token))
        log(f"[mcp_server] auditpaper-standards 기동 (http {host}:{port}/mcp, Bearer 인증, "
            f"허용 호스트 {allowed}) — 인코더는 백그라운드 로드 중")
        app.run(transport="http", host=host, port=port, path="/mcp", allowed_hosts=allowed)
    else:
        app = build_app()
        log("[mcp_server] auditpaper-standards 기동 (stdio) — 인코더는 백그라운드 로드 중")
        app.run()  # stdio


if __name__ == "__main__":
    main()
