#!/usr/bin/env python3
"""기존 코퍼스(auditstandard_md/, ifrs_md/, Conceptual_framework_md/)를
벡터저장소 규약(docs/규약_벡터저장소_스키마.md 4장) 형식으로
corpus_md/에 변환한다. 원본 파일은 수정하지 않는다.

규약 요약: frontmatter 3필드(source_type/standard_no/standard_title), 절 제목 `##`만,
행 머리 `번호.` 문단 절단, 표는 문단에 통합, 목차 제거, 파일명 유형_번호.md
"""

import json
import re
import sys
import zipfile
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "corpus_md"
ISQM_DOCX = Path(
    "/home/shin/Project/_AuditStandard_parsing/raw/3. 품질관리기준서1(2018년 제정)_국어전문.docx"
)
# DOCX가 없는 환경에서도 재생성이 가능하도록 파싱 결과를 저장소에 동봉한다
ISQM_ROWS_JSON = Path(__file__).resolve().parent / "isqm1_rows.json"

# 출력 문단 행머리 판정 (파서와 동일해야 함)
# 부록-사례N: 감사기준 예시 보고서의 사례 단위 가상 번호 (규약 4.3)
# 사례[A-Z]: 실무서2(PS2)의 사례 상자 A~T / 참조N: PS2 부록 '개념체계와 기준서 참조' 발췌 단위
# 정의-{용어}: 부록 A 용어정의 표의 행(용어) 단위 조각 (규약 4.3 용어집 갈래, 수정 8)
# IE사례N-*/IE부록X-*/IE부록N/BC-*: 비정본 첨부물(BC·IE) 갈래의 합성 번호 (규약 4.3 다섯째 갈래)
# 한?[A-Z]{0,4}\.?\d: BA.1(1109 부록 B 말미) 같은 '영문자+점+숫자' 계열 포함 (BC1·IE3도 이 계열)
HEAD_RE = re.compile(
    r"^(부록-사례\d+|부록-?[0-9A-Za-z()]+|보론\d*-\d+|사례[A-Z]|참조\d+|정의-[가-힣A-Za-z0-9()·-]+"
    r"|IE사례\d+-[0-9A-Za-z.]+|IE부록[A-Z]-\d[0-9A-Za-z.]*|IE부록\d+|BC부록\d+|BC-[0-9A-Za-z.]+"
    r"|한?[A-Z]{0,4}\.?\d[0-9A-Za-z.-]*)\.\s"
)

# 원본에 para 주석 없이 놓인 정본 문단의 무마침표 행머리 (예: 'A1<TAB>본문', 'D1 ', 'BA.1')
NOPERIOD_HEAD = re.compile(r"^(?:\*\*)?(한?[A-Z]{1,4}\.?\d[0-9A-Za-z.]*)[ \t]+(\S.*)$")
SERIES_START = re.compile(r"한?[A-Z]{1,4}\.?1")

# 의결 문구(위원 명단·의결 사실 기재)는 정본 문단이 아니므로 제거
BOILER_HEAD = re.compile(r"^회계기준위원회 위원\s*:")
BOILER_VOTE = re.compile(r"회계기준위원회\S*\s*위원\s*\d+인.*의결하였다")

# ── BC·IE 갈래 (규약 4.3 다섯째 갈래) ────────────────────────────────
# 구역 상투문(제공 갈음 안내·비정본 선언·제목 반복)은 조각 본문이 아니므로 제거
Z_BOILER = [
    re.compile(r"^\*{0,2}한국회계기준원은 한국채택국제회계기준 제정시"),
    re.compile(r"^\*{0,2}다만,\s*한국회계기준원이"),
    re.compile(r"^\*이\s*(결론도출근거|적용사례|실무적용지침)"),
    re.compile(r"^\*\*(결론도출근거|적용사례|실무적용지침)\*\*$"),
    re.compile(r"^[·•]?\s*실무적용지침\s*$"),
    re.compile(r"^\*\*(기업회계기준서?|국제회계기준|한국채택국제회계기준|K-IFRS|IFRS|IAS|SIC|IFRIC).{0,70}\*\*$"),
]
# 구역 내부의 볼드 사례 헤더 (예: '**사례 5: 계약변경―재화**') — section_path 세그먼트로 편입
Z_CASE_BOLD = re.compile(r"^\*\*\s*(사례\s*\d+[^*]*?)\s*\*\*$")
# 합성 대상 원번호: 정수·소수점 계열(1, 1.2)과 부록 문자 계열(A1, B36, C9A)
Z_NUM = re.compile(r"^\d+[0-9A-Za-z.]*$")
Z_APPX = re.compile(r"^([A-Z])(\d+[A-Z]?)$")
Z_SECT_APPX = re.compile(r"^부록\s*([A-Z])\b")
# 갈래 내 무주석 문단머리 폴백 (BC1<TAB>본문 / IE3<TAB>본문 / IG2<TAB>본문 — para 주석이
# 안 따라올 때만). IG = 실무적용지침(Implementation Guidance) 원번호 — 1001·1101·1102·
# 1107·1108의 IE 구역이 para 주석 없이 IG 리터럴 행머리만 갖는다.
# BC[A-Z]{0,2}\.? — 1109 결론도출근거의 BCE.238·BCG.1 같은 파생 계열(주석 일부 부재)
Z_HEAD_FALLBACK = re.compile(r"^(?:\*\*)?((?:BC[A-Z]{0,2}\.?|IE|IG)\d[0-9A-Za-z.]*)[ \t]+(\S.*)$")
# 부록 문자 계열 리터럴 행머리 (1034 부록 A의 'A1<TAB>본문' — 절 제목 문자와 일치할 때만)
Z_APPX_HEAD = re.compile(r"^([A-Z])(\d+[A-Z]?)[\t ]+(\S.*)$")
# 갈래 내 리터럴 점번호 머리 (1008 '1.1.<TAB>본문' — 사례 스코프 합성 대상)
Z_DOTTED_HEAD = re.compile(r"^(\d+\.\d+)[.．][\t ]+(\S.*)$")

# ── 부록 A 용어정의(용어집 갈래, 수정 8) ─────────────────────────────
# 앵커: '**부록 A**'형 굵은 제목 + "용어의 정의" + 정본 선언문의 근접 출현 (제목 표기가 파일마다 다름)
GLOSS_ANCHOR = re.compile(r"^\*\*\s*부록\s*A[.．]?\s*(용어의\s*정의)?\s*\*\*\s*$")
GLOSS_DECL = re.compile(r"^\*이 부록은\s*(이\s*)?기준서의 일부를 구성한다")
# 원본 표에서 용어 셀이 줄넘김으로 별도 표 행에 떨어진 곳 — (기준서, 조각 텍스트, 직전 용어행 첫 셀)
# 자동 판정 불가(예: 1109 '손실충당금'은 정의문이 목록으로 시작하는 독립 용어)라 명시 목록으로 고정
GLOSS_WRAP = {
    ("1101", "회계기준 재무상태표", "개시 한국채택국제"),
    ("1101", "회계기준 보고기간", "최초 한국채택국제"),
    ("1101", "회계기준 재무제표", "최초 한국채택국제"),
    ("1101", "기준", "한국채택국제회계"),
    ("1101", "기준 전환일", "한국채택국제회계"),
    ("1102", "(주식옵션)", "주식선택권"),
    ("1109", "매도", "정형화된 매입 또는"),
    ("1113", "투입변수", "관측할 수 있는"),
    ("1116", "증분차입이자율", "리스이용자의"),
    ("1117", "보험계약", "직접참가특성이 있는"),
    ("1117", "없는 보험계약", "직접참가특성이"),
    ("1117", "포트폴리오", "보험계약"),
}
# 감사기준 쪽 용어집: (기준서, 부록 순번) — 1200 부록1(용어 정의 표 65행)이 유일
GLOSS_ISA = {("1200", 1)}


def gloss_disp(term):
    """용어의 표시 표기: 각주 참조([^N]·한N))와 굵은 글씨 마커 제거, 공백 정리"""
    t = re.sub(r"\[\^\d+\]", "", term)
    t = re.sub(r"한\d+\)\s*$", "", t.strip())
    return re.sub(r"\s+", " ", t.replace("*", "")).strip()


def gloss_norm(term):
    """para_no용 용어명 정규화: 표시 표기에서 공백만 제거 (괄호·가운뎃점·하이픈 보존)"""
    return re.sub(r"\s+", "", gloss_disp(term))


def para_series(p):
    """문단번호의 영문자 계열(한 접두 제외). 'A12'→'A', 'D8A'→'D', 'BA.2'→'BA', '한C1.1'→'C'"""
    m = re.match(r"한?([A-Z]+)", p)
    return m.group(1) if m else None

# ── 감사기준 원본의 국소 번호 오류 보정 ─────────────────────────────
# 원본 DOCX 자동번호 재시작으로 추출 번호가 실제 기준서 번호와 어긋난 곳.
# 근거: 적용자료 절 제목의 "(문단 N 참조)" 및 국제감사기준 대조. (파일명, idx) → 올바른 번호
ISA_CORRECTIONS = {
    ("ISA-250.md", 1615): "15.",
    ("ISA-260.md", 1823): "16.",
    ("ISA-260.md", 1832): "17.",
    ("ISA-300.md", 2238): "8.",
    ("ISA-300.md", 2244): "9.",
    ("ISA-300.md", 2251): "12.",
    ("ISA-300.md", 2256): "13.",
    # ISA-701: 두 번째 문단4부터 끝까지 원본 번호가 1씩 작게 추출됨
    ("ISA-701.md", 8427): "5.", ("ISA-701.md", 8429): "6.", ("ISA-701.md", 8432): "7.",
    ("ISA-701.md", 8434): "8.", ("ISA-701.md", 8438): "9.", ("ISA-701.md", 8442): "10.",
    ("ISA-701.md", 8444): "11.", ("ISA-701.md", 8448): "12.", ("ISA-701.md", 8450): "13.",
    ("ISA-701.md", 8454): "14.", ("ISA-701.md", 8458): "15.", ("ISA-701.md", 8462): "16.",
    ("ISA-701.md", 8464): "17.", ("ISA-701.md", 8468): "18.",
}

# frontmatter에 standard_title이 없는 감사기준 파일의 제목
ISA_TITLE_FALLBACK = {
    "ISQM-1": "품질관리기준서 1",
    "FRMK-1": "인증업무개념체계",
    "ASSR-3000": "역사적 재무정보에 대한 감사 및 검토 이외의 인증업무기준",
}

COMMENT_RE = re.compile(r"^\s*<!--\s*(.*?)\s*-->\s*$")


def parse_comment(line):
    m = COMMENT_RE.match(line)
    if not m:
        return None
    fields = {}
    for part in m.group(1).split("|"):
        part = part.strip()
        if ":" in part:
            k, v = part.split(":", 1)
            fields[k.strip()] = v.strip()
        elif part:
            fields[part] = True
    return fields


def read_frontmatter(text):
    parts = text.split("---\n")
    fm = {}
    for line in parts[1].split("\n"):
        m = re.match(r"^(\w+):\s*(.+)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip('"')
    return fm, "---\n".join(parts[2:])


class Emitter:
    """출력 조립기: 절 경로 추적, 문단 행머리 기록, 위험한 이어지는 행 들여쓰기."""

    def __init__(self):
        self.lines = []
        self.paras = []           # 방출한 문단번호(마침표 제외) 순서 기록
        self.pending_section = None
        self.cur_section = None

    def set_section(self, path):
        if path != self.cur_section:
            self.pending_section = path

    def _flush_section(self):
        if self.pending_section is not None:
            self.lines += ["", f"## {self.pending_section}"]
            self.cur_section = self.pending_section
            self.pending_section = None

    def para(self, num_with_dot, text):
        """문단 시작. num_with_dot 예: '31.' 'A124.' '한2.1.' '부록-51.'"""
        self._flush_section()
        self.lines += ["", f"{num_with_dot}\t{text}".rstrip()]
        self.paras.append(num_with_dot.rstrip("."))

    def cont(self, line):
        """문단에 이어지는 행. 행머리 번호로 오인될 행은 탭 들여쓰기."""
        self._flush_section()
        if HEAD_RE.match(line):
            line = "\t" + line
        self.lines.append(line.rstrip())

    def render(self, fm_lines):
        body = "\n".join(self.lines)
        body = re.sub(r"\n{3,}", "\n\n", body).strip("\n")
        return "---\n" + "\n".join(fm_lines) + "\n---\n\n" + body + "\n"


def write_output(fname, fm_pairs, em, expected_registry):
    fm_lines = []
    for k, v in fm_pairs:
        if k == "standard_no":
            v = f'"{v}"'
        fm_lines.append(f"{k}: {v}")
    (OUT / fname).write_text(em.render(fm_lines), encoding="utf-8")
    expected_registry[fname] = em.paras


# ══════════════════════════════ 감사기준 (auditstandard_md/) ══════════════════════════════

# 번호 없는 예시문 부록이 직전 실문단에 병합되는 파일들: 부록/사례 단위로 절단한다.
# ID는 규약 4.3의 가상 번호 — 부록N(부록 서두·목록형 부록), 부록-사례N(예시 보고서 한 건),
# 참조N(다른 기준서를 가리키는 목록형 보론 — para_type "참조", 검색 기본 포함)
EX_SPLIT_FILES = {
    "700", "705", "720", "1200", "240", "710", "570", "1100", "600",
    "706", "580", "510", "300", "210", "230", "260", "315", "530", "540", "620",
}
# (파일, 보론 순번) → 참조N: 다른 기준서의 요구사항 위치를 나열하는 목록형 보론
REF_APPS = {("706", 1), ("706", 2), ("580", 1), ("230", 1), ("260", 1)}
CASE_APPS = {("580", 2)}                         # 단일 예시 보론 전체 = 부록-사례N
SUBCUT_FILES = {"300"}                           # 보론 하위 절마다 부록N 절단 (고려사항 목록)
CASE_HEAD = re.compile(r"사례\s*(\d+)([\s\-–—::].*)?$")


def convert_isa_file(path, expected):
    name = path.name
    text = path.read_text(encoding="utf-8")
    fm, body = read_frontmatter(text)
    std_id = fm.get("standard_id", "")
    std_no = std_id.replace("ISA-", "") if std_id.startswith("ISA-") else std_id
    title = fm.get("standard_title") or ISA_TITLE_FALLBACK.get(std_id, "")

    em = Emitter()
    lines = body.split("\n")

    # FRMK-1: 머리의 목차형 제목(##+range)을 절 매핑으로 회수
    range_map = {}      # 시작문단번호 → 절 제목
    boron_titles = []   # 보론 절 제목 (본문에서 문자열 일치로 탐지)
    if std_id == "FRMK-1":
        for i, ln in enumerate(lines):
            if ln.startswith("## "):
                c = parse_comment(lines[i + 1]) if i + 1 < len(lines) else None
                t = ln[3:].strip()
                if c and "range" in c:
                    start = int(str(c["range"]).split("-")[0])
                    range_map[start] = t
                elif t.startswith("보론"):
                    boron_titles.append(t)

    seen_first_h2 = False
    levels = {}          # 제목 레벨 → 텍스트
    pending_num = None   # FRMK/ASSR 번호 단독 행
    number_only = re.compile(r"^(한?\d+[A-Z]?|한?A\d+(-\d+)?)$")
    boron = None         # 보론 진입 후 문단번호 접두 ('보론2-' 등). 보론은 자체 번호가 1부터 재시작함

    ex_split = std_no in EX_SPLIT_FILES
    appendix_ord = 0     # 파일 내 부록(section: appendix) 순번 (300은 하위 절도 순번 소비)
    in_ex_app = False    # 예시문 부록 구역 진입 여부
    pending_app = None   # 부록 서두 조각의 가상 번호 (첫 내용 행에서 방출, 사례가 먼저 오면 폐기)
    case_next = 1        # 다음에 나와야 할 사례 번호 (본문 속 '사례 N' 언급 오인 방지)
    ref_ord = 0          # 목록형 보론(참조N) 순번
    absorb_sect = None   # ASSR: 보론 표지 직후의 짧은 참조·제목 행을 절 제목에 흡수

    i = 0
    while i < len(lines):
        ln = lines[i]
        c = parse_comment(ln)
        if c is not None:
            i += 1
            continue
        s = ln.strip()
        if not s:
            i += 1
            continue

        # 제목 처리
        hm = re.match(r"(#{1,6})\s+(.*)", ln)
        if hm:
            lvl = len(hm.group(1))
            if lvl == 1:
                i += 1
                continue  # 문서 제목은 frontmatter가 담당
            if std_id == "FRMK-1":
                i += 1
                continue  # 목차형 제목은 range_map으로 재배치
            seen_first_h2 = True
            htext = hm.group(2).strip()
            if ex_split:
                # 부록 내부의 '#### 사례 N - …' 제목(ISA-1100)은 절이 아니라 사례 절단선
                mc = CASE_HEAD.match(htext)
                if in_ex_app and mc and int(mc.group(1)) == case_next:
                    em.para(f"부록-사례{case_next}.", htext)
                    case_next += 1
                    pending_app = None
                    i += 1
                    continue
                # 부록 제목 감지: 제목행 (+ 제목 이어행) 뒤에 section: appendix 주석
                nxt1 = lines[i + 1] if i + 1 < len(lines) else ""
                nxt2 = lines[i + 2] if i + 2 < len(lines) else ""
                c1, c2 = parse_comment(nxt1), parse_comment(nxt2)
                is_app = False
                if c1 and c1.get("section") == "appendix":
                    is_app = True
                elif c1 is None and nxt1.strip() and c2 and c2.get("section") == "appendix":
                    htext += " — " + nxt1.strip()  # 제목 이어행을 절 제목에 흡수
                    is_app = True
                    i += 1
                if is_app:
                    appendix_ord += 1
                    in_ex_app = True
                    if (std_no, appendix_ord) in REF_APPS:
                        ref_ord += 1
                        pending_app = f"참조{ref_ord}"
                    elif (std_no, appendix_ord) in CASE_APPS:
                        pending_app = f"부록-사례{case_next}"
                    else:
                        pending_app = f"부록{appendix_ord}"
                elif in_ex_app and std_no in SUBCUT_FILES:
                    # 보론 내부의 하위 절 제목 → 새 부록 조각 (고려사항 목록의 절 단위 절단)
                    appendix_ord += 1
                    pending_app = f"부록{appendix_ord}"
            levels = {k: v for k, v in levels.items() if k < lvl}
            levels[lvl] = htext
            em.set_section(" > ".join(levels[k] for k in sorted(levels)))
            i += 1
            continue

        # 다음 주석에서 이 블록의 종류 파악
        info = {}
        for j in range(i + 1, min(i + 2, len(lines))):
            nc = parse_comment(lines[j])
            if nc:
                info = nc

        kind = info.get("kind", "")
        if kind == "toc_entry":
            i += 1
            continue

        # 첫 절 제목 이전의 잡동사니(목차/문단번호 등) 제거 — 단 일러두기 인용문은 보존
        if not seen_first_h2 and std_id not in ("FRMK-1", "ASSR-3000"):
            if s.startswith(">"):
                em.set_section("일러두기")
                em.cont(s)
            i += 1
            continue

        # FRMK/ASSR: 보론 진입 표지 (자체 번호가 1부터 재시작하므로 접두 부여)
        if std_id in ("FRMK-1", "ASSR-3000") and kind == "paragraph_body" \
                and re.fullmatch(r"보론\s*(\d*)", s):
            n = re.fullmatch(r"보론\s*(\d*)", s).group(1)
            boron = f"보론{n}-" if n else "보론-"
            # 주의: 파일 제목 변수 title과 별개의 지역명을 쓸 것 — 같은 이름을 쓰면
            # 마지막 보론 제목이 frontmatter standard_title을 덮어쓴다 (FRMK/ASSR 오염 버그)
            sect_title = next(
                (t for t in boron_titles if t.startswith(f"보론 {n}" if n else "보론:")), s
            )
            em.set_section(sect_title)
            pending_num = None
            if std_id == "FRMK-1":
                # FRMK 보론의 무번호 서두(보론 1은 전체가 도표)는 부록N 조각으로
                in_ex_app = True
                pending_app = f"부록{n or 1}"
            else:
                absorb_sect = sect_title
            i += 1
            continue

        # FRMK/ASSR: 번호 단독 행 → 다음 본문 행과 병합
        if std_id in ("FRMK-1", "ASSR-3000") and number_only.fullmatch(s):
            pending_num = s
            i += 1
            continue

        # para 필드가 있는 블록 (요구사항/적용지침/부록 등)
        para = info.get("para")
        if para and kind in ("requirement", "application_guidance"):
            idx = int(info.get("idx", -1))
            para = ISA_CORRECTIONS.get((name, idx), para)
            if not para.endswith("."):
                para += "."
            # 행 머리의 원래 번호 제거
            body_text = re.sub(r"^\s*\S+?[.．]?[\t ]+", "", ln, count=1) if re.match(
                r"^\s*(부록-)?[0-9A-Za-z한.]+[.．]?[\t]", ln
            ) else ln.strip()
            em.para(para, body_text)
            pending_num = None
            pending_app = None   # 번호 문단이 있는 보론은 서두 조각 불필요
            absorb_sect = None
            i += 1
            continue

        # FRMK/ASSR 병합 문단
        if pending_num is not None and kind in ("paragraph_body", ""):
            num = pending_num
            pending_num = None
            if boron:
                num = boron + num
            elif std_id == "FRMK-1":
                pnum = re.sub(r"\D", "", num)
                if pnum and int(pnum) in range_map:
                    em.set_section(range_map[int(pnum)])
            em.para(num + ".", s)
            pending_app = None
            absorb_sect = None
            i += 1
            continue

        # ASSR: 보론 표지 직후의 짧은 참조·제목 행은 절 제목에 흡수
        if absorb_sect is not None:
            if len(s) <= 60 and not number_only.fullmatch(s) \
                    and not s.startswith(("|", ">", "•")) and not ln.startswith("\t"):
                absorb_sect = f"{absorb_sect} {s}" if s.startswith("(") else f"{absorb_sect} — {s}"
                em.set_section(absorb_sect)
                i += 1
                continue
            absorb_sect = None

        # 용어집 부록(1200 부록1): 표의 행 = 용어 하나 = 정의-{용어} 조각 (수정 8)
        if in_ex_app and (std_no, appendix_ord) in GLOSS_ISA and s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if all(re.fullmatch(r"-*", c) for c in cells) or cells[0] == "용어":
                i += 1
                continue  # 구분행·헤더행
            if cells[0]:
                term = gloss_disp(cells[0])
                em.para(f"정의-{gloss_norm(cells[0])}.", f"{term}: {cells[1] if len(cells) > 1 else ''}")
                pending_app = None
            elif len(cells) > 1 and cells[1]:
                em.cont("\t" + cells[1])
            i += 1
            continue

        # 예시문 부록 구역: 사례 경계 절단 + 부록 서두 조각
        if in_ex_app:
            s2 = s.lstrip(">").strip() if s.startswith(">") else s
            mc = CASE_HEAD.match(s2)
            if mc and int(mc.group(1)) == case_next and (
                not s.startswith(">") or re.match(r"사례\s*\d+\s*([-–—::]|$)", s2)
            ):
                em.para(f"부록-사례{case_next}.", s2)
                case_next += 1
                pending_app = None
                i += 1
                continue
            if pending_app is not None:
                em.para(pending_app + ".", s)
                if pending_app.startswith("부록-사례"):
                    case_next += 1
                pending_app = None
                i += 1
                continue

        # ASSR: 절 제목 후보 (짧은 독립 행)
        if std_id in ("ASSR-3000",) and kind == "paragraph_body" and len(s) <= 26 \
                and not re.search(r"[.다함음됨임,)\]:;]$", s) \
                and not s.startswith(("|", ">", "•", "[", "(", "*")) \
                and not ln.startswith(("\t", " ")):
            em.set_section(s)
            i += 1
            continue

        # FRMK: 보론 절 제목
        if std_id == "FRMK-1" and any(s == t or (len(s) <= 60 and t.startswith(s)) for t in boron_titles):
            em.set_section(next(t for t in boron_titles if s == t or t.startswith(s)))
            i += 1
            continue

        # 그 밖의 모든 블록: 이어지는 내용으로 방출
        if kind == "unknown_numbering":
            s = re.sub(r"^\[\?\]", "-", s)
            em.cont("\t" + s)
        elif ln.startswith(("\t", " ", "|", ">")):
            em.cont(ln)
        else:
            em.cont(s)
        i += 1

    write_output(
        f"ksa_{std_no.lower()}.md",
        [("source_type", "감사기준"), ("standard_no", std_no),
         ("standard_title", title), ("origin", f"auditstandard_md/{name}")],
        em, expected,
    )


def isqm_rows_from_docx():
    """원본 DOCX의 2열 표(열0=번호, 열1=본문)에서 방출 이벤트 목록을 만든다."""
    W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    z = zipfile.ZipFile(ISQM_DOCX)
    root = ET.fromstring(z.read("word/document.xml"))

    def ptext(p):
        return "".join(t.text or "" for t in p.iter(W + "t")).strip()

    def direct_rows(t):
        return [tr for tr in t if tr.tag == W + "tr"]

    # 본문 표는 바깥 표 안에 중첩되어 있음 — 직접 자식 행이 가장 많은 표 선택
    body_tbl = max(root.iter(W + "tbl"), key=lambda t: len(direct_rows(t)))

    rows = []
    for tr in direct_rows(body_tbl):
        tcs = [tc for tc in tr if tc.tag == W + "tc"]
        if len(tcs) == 1:
            t = ptext(tcs[0])
            if t:
                rows.append(["section", t])
            continue
        if len(tcs) != 2:
            continue
        num = ptext(tcs[0])
        paras = [ptext(p) for p in tcs[1].iter(W + "p")]
        paras = [p for p in paras if p]
        if not paras:
            continue
        if not num:
            # 제목 행 (본문 열에 절 제목만 있음)
            if len(paras) == 1 and len(paras[0]) <= 40:
                rows.append(["section", paras[0]])
            else:
                for p in paras:
                    rows.append(["cont", p])
            continue
        rows.append(["para", num, paras[0]])
        for p in paras[1:]:
            rows.append(["cont", "\t" + p])
    return rows


def convert_isqm(expected):
    """ISQM-1: md에 문단번호가 소실되어 원본 DOCX에서 복원.
    DOCX가 없는 환경에서는 저장소에 동봉한 isqm1_rows.json(직전 파싱 결과)을 쓴다."""
    if ISQM_DOCX.exists():
        rows = isqm_rows_from_docx()
        ISQM_ROWS_JSON.write_text(
            json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    elif ISQM_ROWS_JSON.exists():
        print(f"  [알림] ISQM-1 원본 DOCX 없음 — {ISQM_ROWS_JSON.name}에서 복원")
        rows = json.loads(ISQM_ROWS_JSON.read_text(encoding="utf-8"))
    else:
        sys.exit("ISQM-1 변환 불가: 원본 DOCX와 isqm1_rows.json 둘 다 없음")

    em = Emitter()
    # 표 시작 전의 절 제목(서론 > 범위)은 바깥 표에 있어 직접 지정
    em.set_section("이 품질관리기준서의 범위")
    for row in rows:
        if row[0] == "section":
            em.set_section(row[1])
        elif row[0] == "para":
            em.para(row[1] + ".", row[2])
        else:
            em.cont(row[1])

    write_output(
        "ksa_isqm-1.md",
        [("source_type", "감사기준"), ("standard_no", "ISQM-1"),
         ("standard_title",
          "품질관리기준서 1: 재무제표 감사와 검토, 그리고 기타 인증 및 관련 서비스 업무를 수행하는 회계법인의 품질관리"),
         ("origin", "auditstandard_md/ISQM-1.md (문단번호는 원본 DOCX 표에서 복원)")],
        em, expected,
    )


# ══════════════════════════════ 회계기준 (ifrs_md/, Conceptual_framework_md/) ══════════════════════════════

CF_MAP = {  # standard_id → (standard_no, 파일명 토큰)
    "재무보고 개념체계": ("CF", "cf"),
    "경영진설명서 개념체계": ("MC", "mc"),
    "실무서 2 중요성": ("PS2", "ps2"),
}


def convert_ifrs_file(path, expected):
    text = path.read_text(encoding="utf-8")
    fm, body = read_frontmatter(text)
    is_cf = fm.get("standard_family") in ("CF", "PS")

    if is_cf:
        std_no, token = CF_MAP[fm["standard_id"]]
        title = fm.get("title", fm["standard_id"])
    else:
        std_no = fm["standard_number"]
        token = std_no
        title = fm["title"]

    lines = body.split("\n")

    # 1033: 파일 전체가 두 번 반복 수록됨 → 두 번째 '## 본 문'부터 잘라냄
    if std_no == "1033":
        h2_positions = [i for i, ln in enumerate(lines) if ln.startswith("## 본 문")]
        if len(h2_positions) > 1:
            lines = lines[: h2_positions[1]]

    em = Emitter()
    comp_include = True
    bc_seen = False    # '## 결론도출근거' 이후의 component 태그는 신뢰 불가(오태깅) — 전부 제외
    ie_seen = False    # '## 적용사례' 이후도 동일 (1109에서 IE 구역 내 authority 1 오태깅 확인)
    h2_force = False   # 제목 규칙에 의한 강제 제외 (뒤따르는 component 주석보다 우선)
    body_started = False  # 첫 절 제목 이전의 표지 표 등은 제거
    in_toc_table = False
    h2 = h3 = None
    last_content_idx = None  # em.lines에서 마지막 본문 행 위치
    promoted = []      # para 주석 없이 행머리 패턴으로 승격한 문단 (검수 보고용)
    n_boiler = 0

    # BC·IE 갈래 상태 (규약 4.3 다섯째 갈래) — 정본 배제 래치(bc_seen/ie_seen)와 별개로,
    # 구역 내용을 별도 Emitter에 담아 kifrs_{토큰}_bc.md / _ie.md 로 낸다. C-07 원칙 승계:
    # 구역 내 component/authority 태그는 갈래 소속 판정에 쓰지 않는다(h2 래치만 신뢰).
    z_em = {"bc": Emitter(), "ie": Emitter()}
    zone = None              # 현재 구역: None | 'bc' | 'ie'  (h2 도달마다 갱신)
    z_base = {"bc": "결론도출근거", "ie": "적용사례"}
    z_h2 = z_h3 = z_case = None   # 구역 내부 절 구조 (사례 볼드 헤더 포함)
    z_scope = 0              # 합성 ID의 사례 스코프 (내부 절 헤더마다 증가, 사례N은 N에 정렬)
    z_last_idx = {"bc": None, "ie": None}  # 구역별 마지막 본문 행 (para 주석 승격용)
    z_chunk = 0              # 무주석 구역의 절 조각 순번 (IE부록N)
    z_pending = None         # 무주석 구역의 대기 조각 ID
    z_toc = False            # 구역 내 목차 표 제거
    z_unmapped = []          # 합성 불가로 승격을 건너뛴 para (검수 보고용)

    # 프리스캔: 각 구역에 para 주석이 존재하는가(없으면 절 단위 조각 모드) +
    # 고유 번호 체계(BC/IE/IG)를 갖는가(있으면 잔여 정수는 표 각주 후보 — 1036)
    z_has_para = {"bc": False, "ie": False}
    z_native = {"bc": False, "ie": False}
    if not is_cf:
        zz = None
        for ln in lines:
            cc = parse_comment(ln)
            if cc is not None:
                if zz and "para" in cc:
                    z_has_para[zz] = True
                    if str(cc["para"]).startswith(("BC", "IE", "IG")):
                        z_native[zz] = True
                continue
            hh = re.match(r"##\s+(.*)", ln)
            if hh:
                tt = hh.group(1).strip()
                if tt.startswith("결론도출근거"):
                    zz = "bc"
                elif tt.startswith("적용사례"):
                    zz = "ie"

    def z_sect():
        parts = [z_base[zone]]
        if z_h2:
            parts.append(z_h2)
        if z_h3:
            parts.append(z_h3)
        if z_case:
            parts.append(z_case)
        z_em[zone].set_section(" > ".join(parts))

    z_syn_last = [None]   # 직전 합성 원번호 튜플 — 헤더 없는 번호 재시작(역행) 감지용
    z_syn_warn = []       # 역행 자동 스코프 증가 기록 (검수 보고용)
    z_footnote = []       # 표 각주로 판별해 이어행으로 남긴 정수 para (검수 보고용)
    z_fn_run = False      # 각주 연속 구간 (표 뒤 각주 1,2,3…이 줄지어 오는 경우)

    def z_synth(p):
        """구역 para의 방출 번호 합성. None 반환 = 승격하지 않음(이어행으로 남김)."""
        nonlocal z_scope
        if p.startswith(("BC", "IE", "IG")):
            return p
        if p.startswith("CU"):
            return None  # 화폐단위 예시 표기 — 문단번호 아님
        if zone == "bc":
            # 정수(2032 등 구식 무접두)와 문자 계열(1040·1041의 B1~ — 구식 BC 원번호)
            return f"BC-{p}" if Z_NUM.match(p) or Z_APPX.match(p) else None
        am = Z_APPX.match(p)
        if am:  # 1034 부록 A/B/C의 A1·B36·C9 — 부록 문자 계열
            return f"IE부록{am.group(1)}-{am.group(2)}"
        if Z_NUM.match(p):
            sm = Z_SECT_APPX.match(z_h2 or "")
            if sm:  # 1007 부록 A/B/C 아래의 재시작 정수
                return f"IE부록{sm.group(1)}-{p}"
            # 절 헤더 없는 번호 재시작(역행) → 사례 스코프 자동 증가 (1012·1036)
            tup = tuple(int(x) for x in re.findall(r"\d+", p))
            if z_syn_last[0] is not None and tup <= z_syn_last[0]:
                z_scope += 1
                z_syn_warn.append(f"사례{z_scope}@{p}")
            z_syn_last[0] = tup
            return f"IE사례{max(z_scope, 1)}-{p}"
        return None
    ps2_app = False    # PS2 부록(개념체계와 기준서 참조) 구역
    ref_n = 0          # 참조N 순번 (PS2 부록 발췌 단위 / 부록 A의 타 기준서 참조 블록)
    gloss = False          # 부록 A 용어집 구역 (수정 8)
    gloss_prev_raw = None  # 직전 용어 시작 행의 원문 첫 셀 (GLOSS_WRAP 판정 키)
    gloss_last_idx = None  # 마지막 정의 조각의 em.lines 위치 (wrap 결합용)
    gloss_last_disp = None

    # 각 행 뒤(빈 행 건너뜀)에 para 주석이 오는지 미리 계산 — 주석이 확정할 행은 승격하지 않는다
    follows_para = [False] * len(lines)
    for i, _ in enumerate(lines):
        for j in range(i + 1, min(i + 4, len(lines))):
            if not lines[j].strip():
                continue
            cc = parse_comment(lines[j])
            follows_para[i] = bool(cc and "para" in cc)
            break

    for i, ln in enumerate(lines):
        include = comp_include and not bc_seen and not ie_seen and not h2_force
        c = parse_comment(ln)
        if c is not None:
            if "component" in c:
                comp, auth = c.get("component"), c.get("authority")
                if is_cf:
                    # 개념체계·실무서는 문서 전체가 참고문헌 성격 — 결론도출근거만 제외.
                    # (경영진설명서·실무서2는 본문이 ie로 오태깅되어 있어 ie를 버리면 안 됨)
                    comp_include = comp != "bc"
                else:
                    comp_include = auth == "1"
            elif "authority_declaration" in c and not is_cf:
                # 원본이 부록 서두에 스스로 선언한 정본 여부 — component 태그와 동급으로 반영
                comp_include = c["authority_declaration"] == "authoritative"
            elif "para" in c and zone is not None and not is_cf:
                # BC·IE 갈래 승격: 직전 본문 행을 합성 번호 문단으로 (정본 승격 메커니즘과 동형)
                if z_last_idx[zone] is not None:
                    p = str(c["para"])
                    # 고유 번호(BC/IE/IG) 파일의 잔여 정수가 표 바로 뒤에 오면 표 각주다
                    # (1036 — '1 경영진의 예산에 반영된 … 제외한다'). 각주는 1,2,3…으로
                    # 줄지어 오기도 하므로(z_fn_run) 연속 구간 전체를 이어행으로 남긴다.
                    if Z_NUM.match(p) and z_native[zone]:
                        prev = next((l for l in reversed(
                            z_em[zone].lines[:z_last_idx[zone]]) if l.strip()), "")
                        if prev.lstrip("\t").startswith("|") or z_fn_run:
                            z_footnote.append(p)
                            z_fn_run = True
                            z_last_idx[zone] = None
                            continue
                    z_fn_run = False
                    head = z_synth(p)
                    if head is None:
                        if not p.startswith("CU"):
                            z_unmapped.append(p)
                    else:
                        zem = z_em[zone]
                        raw = zem.lines[z_last_idx[zone]]
                        stripped = raw.lstrip("\t")
                        bold = stripped.startswith("**")
                        if bold:
                            stripped = stripped[2:]
                        m = re.match(re.escape(p) + r"[.．]?[\t ]+", stripped)
                        rest = stripped[m.end():] if m else stripped
                        zem.lines[z_last_idx[zone]] = (
                            f"{head}.\t" + ("**" + rest if bold else rest)
                        ).rstrip()
                        zem.paras.append(head)
                    z_last_idx[zone] = None
            elif "para" in c and include and last_content_idx is not None:
                para = c["para"]
                raw = em.lines[last_content_idx]
                bold = raw.lstrip("\t").startswith("**")
                stripped = raw.lstrip("\t")
                if bold:
                    stripped = stripped[2:]
                # 행 머리의 원래 번호 제거 (번호+탭/공백)
                pat = re.escape(para) + r"[.．]?[\t ]+"
                m = re.match(pat, stripped)
                rest = stripped[m.end():] if m else stripped
                new = f"{para}.\t" + ("**" + rest if bold else rest)
                em.lines[last_content_idx] = new.rstrip()
                em.paras.append(para)
                last_content_idx = None
            continue

        hm = re.match(r"(#{1,3})\s+(.*)", ln)
        if hm:
            lvl = len(hm.group(1))
            if lvl == 1:
                continue
            t = hm.group(2).strip().replace("본 문", "본문")
            if is_cf and t == "적용사례":
                t = "본문"  # 경영진설명서·실무서2는 본문이 '적용사례'로 오태깅됨
            body_started = True
            gloss = False  # 절 제목 도달 = 부록 A 구역 종료 (1106 '결론도출근거', 1108 '적용사례')
            if lvl == 2:
                h2, h3 = t, None
                if t.startswith("결론도출근거"):
                    bc_seen = True
                if not is_cf and t.startswith("적용사례"):
                    ie_seen = True
                # 1007: 예시 성격의 부록 A/B/C가 authority 1로 잘못 태깅됨 → 제외
                h2_force = std_no == "1007" and t.startswith("부록")
            else:
                h3 = t
            em.set_section(h2 if not h3 else f"{h2} > {h3}")
            # BC·IE 갈래의 구역 전환·내부 절 추적 (정본 래치와 별개)
            if not is_cf:
                if lvl == 2 and t.startswith("결론도출근거"):
                    zone = "bc"
                    z_h2 = z_h3 = z_case = None
                    z_scope = 0
                    z_pending = None
                    z_last_idx["bc"] = None
                    z_sect()
                elif lvl == 2 and t.startswith("적용사례"):
                    zone = "ie"
                    z_h2 = z_h3 = z_case = None
                    z_scope = 0
                    z_pending = None
                    z_last_idx["ie"] = None
                    z_sect()
                elif zone is not None:
                    if lvl == 2:
                        z_h2, z_h3, z_case = t, None, None
                    else:
                        z_h3, z_case = t, None
                    sm = re.search(r"사례\s*(\d+)", t)
                    z_scope = int(sm.group(1)) if sm else z_scope + 1
                    z_syn_last[0] = None
                    z_fn_run = False
                    z_last_idx[zone] = None
                    if not z_has_para[zone]:  # 무주석 구역: 절 경계 = 새 조각
                        z_chunk += 1
                        z_pending = ("IE부록" if zone == "ie" else "BC부록") + str(z_chunk)
                    z_sect()
            continue

        if not include or not body_started:
            # ── BC·IE 갈래 내용 라우팅 ── (구역 밖의 배제 행은 종전대로 버림)
            if zone is not None and not is_cf:
                zs = ln.rstrip()
                zt = zs.strip()
                zem = z_em[zone]
                if not zt:
                    z_toc = False
                    continue
                # 볼드 사례 헤더 → section_path 세그먼트 (para 주석이 따라오면 본문으로 취급)
                zcm = Z_CASE_BOLD.match(zt)
                if zcm and not follows_para[i]:
                    z_case = zcm.group(1).strip()
                    sm = re.search(r"사례\s*(\d+)", z_case)
                    z_scope = int(sm.group(1)) if sm else z_scope + 1
                    z_syn_last[0] = None
                    z_last_idx[zone] = None
                    if not z_has_para[zone]:
                        z_chunk += 1
                        z_pending = ("IE부록" if zone == "ie" else "BC부록") + str(z_chunk)
                    z_sect()
                    continue
                # 상투문(갈음 안내·비정본 선언·제목 반복)·목차 표 제거
                if any(b.match(zt) for b in Z_BOILER) and not follows_para[i]:
                    continue
                if zt.startswith("|") and re.search(r"목\s*차", zt):
                    z_toc = True
                    continue
                if z_toc and zt.startswith("|"):
                    continue
                z_toc = False
                if not z_has_para[zone]:
                    # 무주석 구역: IG/BC/IE 리터럴 행머리는 원번호 문단으로 승격,
                    # 그 밖은 절 단위 조각(IE부록N) 모드 — 규약 4.3-5 ③
                    fb = Z_HEAD_FALLBACK.match(zt)
                    if fb:
                        head = fb.group(1).rstrip(".")
                        bold = zt.startswith("**")
                        zem.para(head + ".", ("**" if bold else "") + fb.group(2))
                        z_pending = None
                        continue
                    if z_pending is None and not zem.paras:
                        z_chunk += 1
                        z_pending = ("IE부록" if zone == "ie" else "BC부록") + str(z_chunk)
                    if z_pending is not None:
                        zem.para(z_pending + ".", zt)
                        z_pending = None
                    else:
                        zem.cont(zs if zs.startswith(("\t", " ", "|", ">")) else zt)
                    continue
                # para 주석 구역: 무주석 행머리 폴백 (계열 연속성 검사)
                if not follows_para[i]:
                    fb = Z_HEAD_FALLBACK.match(zt)
                    if fb:
                        head = fb.group(1).rstrip(".")
                        if head in ("BC1", "IE1", "IG1") or (
                                zem.paras and zem.paras[-1][:2] == head[:2]):
                            bold = zt.startswith("**")
                            zem.para(head + ".", ("**" if bold else "") + fb.group(2))
                            z_last_idx[zone] = None
                            continue
                    if zone == "ie":
                        dt = Z_DOTTED_HEAD.match(zt)
                        if dt:  # 1008 '1.1.' 리터럴 점번호 — 사례 스코프 합성
                            zem.para(z_synth(dt.group(1)) + ".", dt.group(2))
                            z_last_idx[zone] = None
                            continue
                        ah = Z_APPX_HEAD.match(zt)
                        sm2 = Z_SECT_APPX.match(z_h2 or "")
                        if ah and sm2 and ah.group(1) == sm2.group(1):
                            # 1034 부록 A의 무주석 A1·A2 — 절 제목 문자와 일치할 때만 승격
                            zem.para(f"IE부록{ah.group(1)}-{ah.group(2)}.", ah.group(3))
                            z_last_idx[zone] = None
                            continue
                    if not zem.paras:
                        continue  # 첫 문단 이전의 서두 잔여물(표지 표 등)은 버림
                out = zs if zs.startswith(("\t", " ", "|", ">")) else zt
                if NOPD_OUT.match(out):
                    out = "\t" + out  # 무마침표 행머리 오인 방지 (이어행 표식)
                zem.cont(out)
                z_last_idx[zone] = len(zem.lines) - 1
            continue
        s = ln.rstrip()
        if not s.strip():
            in_toc_table = False
            continue
        # 의결 문구(위원 명단 등)는 정본 문단이 아님 — 제거
        if BOILER_HEAD.match(s.strip()) or BOILER_VOTE.search(s):
            n_boiler += 1
            gloss = False  # 의결 문구 도달 = 부록 A 구역 종료 (1106·1108: 부록 B 없이 끝남)
            continue
        # 부록 A 용어정의(용어집) 진입 — 앵커 3요소(제목·'용어의 정의'·정본 선언문) 근접 확인 (수정 8)
        if not is_cf and not gloss:
            am = GLOSS_ANCHOR.match(s.strip())
            if am:
                near = [lines[j].strip() for j in range(i + 1, min(i + 7, len(lines)))]
                titled = bool(am.group(1)) or "**용어의 정의**" in near
                if titled and any(GLOSS_DECL.match(x) for x in near):
                    gloss = True
                    gloss_prev_raw = gloss_last_idx = gloss_last_disp = None
                    em.set_section("부록 A 용어의 정의")
                    last_content_idx = None
                    continue
        if gloss:
            t = s.strip()
            if t == "**용어의 정의**" or GLOSS_DECL.match(t):
                continue  # 제목 이어행·정본 선언문 — 절 선언에 흡수
            if t.startswith("**부록"):
                gloss = False  # 다음 부록 제목 — 기존 흐름으로 흘려보냄
            elif t.startswith("|"):
                cells = [x.strip() for x in t.strip("|").split("|")]
                if all(re.fullmatch(r"-*", x) for x in cells):
                    continue
                first, rest = cells[0], (cells[1] if len(cells) > 1 else "")
                if first and (std_no, first, gloss_prev_raw) in GLOSS_WRAP:
                    # 줄넘김으로 별도 행에 떨어진 용어 셀 — 직전 조각의 용어명에 결합
                    new_disp = f"{gloss_last_disp} {gloss_disp(first)}"
                    defn = em.lines[gloss_last_idx].split("\t", 1)[1][len(gloss_last_disp) + 2:]
                    new_no = "정의-" + re.sub(r"\s+", "", new_disp)
                    em.lines[gloss_last_idx] = f"{new_no}.\t{new_disp}: {defn}".rstrip()
                    em.paras[-1] = new_no
                    gloss_last_disp = new_disp
                    if rest:
                        em.cont("\t" + rest)
                elif first:
                    term = gloss_disp(first)
                    em.para(f"정의-{gloss_norm(first)}.", f"{term}: {rest}")
                    gloss_prev_raw = first
                    gloss_last_idx = len(em.lines) - 1
                    gloss_last_disp = term
                elif rest:
                    em.cont("\t" + rest)  # 빈 첫 셀 = 직전 정의문의 이어지는 행
                last_content_idx = None
                continue
            elif re.fullmatch(r"\*\*.+\*\*", t):
                # 1116: '다른 기준서에서 정의하고 …' 굵은 소제목 → 하위 절 (정의 표가 이어짐)
                em.set_section("부록 A 용어의 정의 > " + t.strip("*").strip())
                continue
            elif re.search(r"다음\S*\s*용어는", t):
                # 타 기준서 정의를 가리키는 안내문 + 용어 나열 → 참조N 조각 (para_type "참조")
                ref_n += 1
                em.para(f"참조{ref_n}.", t)
                last_content_idx = None
                continue
            else:
                em.cont(t)  # 용어 나열·각주 텍스트 등 — 직전 조각(참조N/정의)에 귀속
                last_content_idx = None
                continue
        # 목차 표 제거 (규약 4.2 제거 대상)
        if s.lstrip().startswith("|") and re.search(r"목\s*차", s):
            in_toc_table = True
        if in_toc_table:
            if s.lstrip().startswith("|"):
                continue
            in_toc_table = False
        # 개념체계 서문(SP 문단)은 para 주석이 없어 직접 절단
        spm = re.match(r"^(SP\d+\.\d+)[\t ]+(.*)", s)
        if is_cf and spm:
            em.para(spm.group(1) + ".", spm.group(2))
            last_content_idx = None
            continue
        # PS2(중요성 실무서) 특례 — 사례 상자(표 형식)와 부록 발췌 단위 절단
        if std_no == "PS2":
            # 사례 A~T 상자: '| 사례 X—제목 |' 행이 상자 시작
            pcm = re.match(r"\|\s*사례\s*([A-T])\s*[—–-]", s)
            if pcm:
                em.para(f"사례{pcm.group(1)}.", s.strip())
                last_content_idx = None
                continue
            # 부록 '개념체계와 기준서 참조': 발췌 제목(굵은 글씨) 경계로 참조N 절단
            if s.strip() == "**부록**":
                ps2_app = True
                em.set_section("부록 — 재무보고를 위한 개념체계와 기업회계기준서 참조")
                continue
            if ps2_app:
                if s.strip() == "**재무보고를 위한 개념체계와 기업회계기준서 참조**":
                    continue  # 부록 제목 이어행 — 절 제목에 흡수됨
                rm = re.fullmatch(r"\*\*(.+?에서 발췌)\*\*", s.strip())
                if rm:
                    ref_n += 1
                    em.para(f"참조{ref_n}.", rm.group(1))
                    last_content_idx = None
                    continue
        # 정본 문단 행머리 복원: 원본이 para 주석 없이 둔 부록 문단(D1, A1, BA.1, C20BA 등).
        # 오탐 방지 — 직전 문단과 같은 영문자 계열이거나 계열 시작(X1)일 때만 승격.
        if not s.startswith(("\t", " ", "|", ">")) and not follows_para[i]:
            nm = NOPERIOD_HEAD.match(s)
            if nm:
                head = nm.group(1)
                prev = em.paras[-1] if em.paras else None
                if SERIES_START.fullmatch(head) or (
                    prev and para_series(prev) and para_series(prev) == para_series(head)
                ):
                    bold = s.startswith("**")
                    em.para(head + ".", ("**" if bold else "") + nm.group(2))
                    promoted.append(head)
                    last_content_idx = None
                    continue
        # 이어지는 행 또는 문단 첫 행(뒤따르는 para 주석이 번호를 확정)
        em.cont(s if s.startswith(("\t", " ", "|", ">")) else s.strip())
        last_content_idx = len(em.lines) - 1

    if z_unmapped:
        print(f"  [경고] kifrs_{token}: BC/IE 구역 내 합성 불가 para {len(z_unmapped)}건 {z_unmapped[:8]}")
    if z_syn_warn:
        print(f"  [합성] kifrs_{token}: 헤더 없는 번호 재시작 → 스코프 자동 증가 {len(z_syn_warn)}건 {z_syn_warn[:6]}")
    if z_footnote:
        print(f"  [각주] kifrs_{token}: BC/IE 구역 표 각주 {len(z_footnote)}건 이어행 유지 {z_footnote[:8]}")
    if promoted:
        print(f"  [복원] kifrs_{token}: 무주석 문단 {len(promoted)}개 승격 — {promoted[:6]}{'…' if len(promoted) > 6 else ''}")
    write_output(
        f"kifrs_{token}.md",
        [("source_type", "회계기준"), ("standard_no", std_no),
         ("standard_title", title),
         ("origin", str(path.relative_to(ROOT)))],
        em, expected,
    )
    # BC·IE 갈래 파일 (규약 4.3-5·4.4) — 정본 파일과 분리 방출이 정본 불변 게이트의 전제
    for zk, suffix, label in (("bc", "_bc", "결론도출근거"), ("ie", "_ie", "적용사례")):
        if z_em[zk].paras:
            write_output(
                f"kifrs_{token}{suffix}.md",
                [("source_type", "회계기준"), ("standard_no", std_no),
                 ("standard_title", title),
                 ("origin", f"{path.relative_to(ROOT)} ({label} 갈래 — 규약 4.3-5)")],
                z_em[zk], expected,
            )


# ══════════════════════════════ 검증 ══════════════════════════════

# 정수 문단 건너뜀 허용목록 — 원문 삭제 문단으로 소명 완료된 구멍 (그 외 신규 구멍은 실패)
INT_GAP_ALLOW = {
    "kifrs_1012.md": {(51, 53)},
    "kifrs_1032.md": {(4, 8), (50, 96)},
    "kifrs_1034.md": {(16, 19)},
    "kifrs_1036.md": {(90, 96)},
    "kifrs_1039.md": {(2, 8), (9, 71), (102, 104)},
    "kifrs_1041.md": {(16, 22), (46, 49)},
    "kifrs_1107.md": {(11, 13), (26, 28)},
    "kifrs_2114.md": {(24, 27)},
    "ksa_assr-3000.md": {(45, 50)},  # 문단 46~49는 2열 대비표 — 문서화된 한계
}

# 감사기준 예시문 분리 결과의 기대 조각 (검수 고정값)
KSA_SPLIT_EXPECT = {
    "ksa_700.md": ["부록1"] + [f"부록-사례{n}" for n in range(1, 5)],
    "ksa_705.md": ["부록1"] + [f"부록-사례{n}" for n in range(1, 6)],
    "ksa_720.md": ["부록1", "부록2"] + [f"부록-사례{n}" for n in range(1, 8)],
    "ksa_1200.md": ["부록2"] + [f"부록-사례{n}" for n in range(1, 11)],  # 부록1은 정의-{용어} 65개로 분해(수정 8)
    "ksa_240.md": ["부록1", "부록2", "부록3"],
    "ksa_710.md": [f"부록-사례{n}" for n in range(1, 5)],
    "ksa_570.md": ["부록1"] + [f"부록-사례{n}" for n in range(1, 4)],
    "ksa_1100.md": ["부록1"] + [f"부록-사례{n}" for n in range(1, 4)],
    "ksa_600.md": [f"부록{n}" for n in range(1, 6)],     # 보론 1~5 서두 (부록-51~은 원본 유래)
    "ksa_706.md": ["참조1", "참조2", "부록3", "부록4"],  # 보론1·2=목록형, 3·4=예시 보고서
    "ksa_580.md": ["참조1", "부록-사례1"],               # 보론1=기준서 목록, 보론2=진술서 예시
    "ksa_510.md": ["부록1", "부록-사례1", "부록-사례2"], # 부록1=예시 서두
    "ksa_300.md": [f"부록{n}" for n in range(1, 6)],     # 보론 서두 + 하위 절 4개
    "ksa_210.md": ["부록1", "부록2"],                    # 보론1=계약서 예시, 보론2 서두 (부록-1~5는 원본 유래)
    "ksa_230.md": ["참조1"],                             # 문서화 요구사항 목록
    "ksa_260.md": ["참조1", "부록2"],                    # 보론1=요구사항 목록, 보론2=질적측면 고려사항
    "ksa_620.md": ["부록1"],                             # 합의 고려사항 목록
    "ksa_315.md": ["부록1", "부록2", "부록4", "부록5", "부록6"],  # 보론3은 서두 없이 번호 문단 직행
    "ksa_530.md": [f"부록{n}" for n in range(1, 5)],     # 보론 1~4 서두 (부록-N은 원본 유래)
    "ksa_540.md": [],                                    # 보론 제목 이어행은 절 제목에 흡수, 조각 없음
    "ksa_frmk-1.md": [f"부록{n}" for n in range(1, 5)],  # 보론1=도표 전체, 2~4=무번호 서두
}

# PS2(중요성 실무서) 분리 기대: 사례 상자 A~T 20개 + 부록 발췌 참조1~4
PS2_EXPECT = {
    "사례": [f"사례{chr(c)}" for c in range(ord("A"), ord("T") + 1)],
    "참조": [f"참조{n}" for n in range(1, 5)],
}

# 6,000자 초과 잔존 허용 문단 — 이 1건(통짜 목록)으로 수렴해야 완료
# (수정 8: ksa_1200 부록1은 정의-{용어} 65개로 분해되어 목록에서 제외)
WARN_ALLOW = {("ksa_240.md", "부록1")}

# BC·IE 갈래 파일 판별 — 6,000자 검사는 경고만 (분할은 적재기 SPLIT_TARGETS + 토크나이저
# 전수 검사가 담당 — 원문 장문 비중이 높아 파일 단위 허용목록이 실익 없음)
GALLEY_RE = re.compile(r"_(bc|ie)\.md$")

# BC·IE 갈래 파일별 기대 문단 수 — 최초 산출(2026-07-12)을 고정 (GLOSS_EXPECT 전례).
# 비어 있으면 검사 생략(최초 생성 모드), 채워지면 등록 외 갈래 파일·수 불일치 모두 실패.
# 검산 근거: 원본 인벤토리(para 주석 BC 7,849 + 폴백 승격분)와 파일별 대조 — 1115_bc 652·
# 1117_bc 573·1102_bc 458(주석 수와 정확 일치), 1109_bc 1,605(주석 1,312 + BCE/BCZ 계열
# 폴백 승격 293), IE 합계 1,752 (주석 1,399 + IG 승격 + 합성 - 각주 10)
BCIE_EXPECT = {
    "kifrs_1001_bc.md": 234,
    "kifrs_1002_bc.md": 23,
    "kifrs_1007_bc.md": 46,
    "kifrs_1008_bc.md": 60,
    "kifrs_1010_bc.md": 5,
    "kifrs_1012_bc.md": 118,
    "kifrs_1016_bc.md": 164,
    "kifrs_1019_bc.md": 314,
    "kifrs_1020_bc.md": 5,
    "kifrs_1021_bc.md": 71,
    "kifrs_1023_bc.md": 33,
    "kifrs_1024_bc.md": 52,
    "kifrs_1027_bc.md": 42,
    "kifrs_1028_bc.md": 84,
    "kifrs_1029_bc.md": 2,
    "kifrs_1032_bc.md": 124,
    "kifrs_1033_bc.md": 15,
    "kifrs_1034_bc.md": 12,
    "kifrs_1036_bc.md": 263,
    "kifrs_1037_bc.md": 21,
    "kifrs_1038_bc.md": 138,
    "kifrs_1039_bc.md": 285,
    "kifrs_1040_bc.md": 90,
    "kifrs_1041_bc.md": 98,
    "kifrs_1101_bc.md": 175,
    "kifrs_1102_bc.md": 458,
    "kifrs_1103_bc.md": 509,
    "kifrs_1105_bc.md": 109,
    "kifrs_1106_bc.md": 69,
    "kifrs_1107_bc.md": 289,
    "kifrs_1108_bc.md": 66,
    "kifrs_1109_bc.md": 1743,
    "kifrs_1110_bc.md": 337,
    "kifrs_1111_bc.md": 99,
    "kifrs_1112_bc.md": 149,
    "kifrs_1113_bc.md": 248,
    "kifrs_1114_bc.md": 79,
    "kifrs_1115_bc.md": 652,
    "kifrs_1116_bc.md": 340,
    "kifrs_1117_bc.md": 573,
    "kifrs_2010_bc.md": 1,
    "kifrs_2025_bc.md": 5,
    "kifrs_2029_bc.md": 3,
    "kifrs_2032_bc.md": 9,
    "kifrs_2101_bc.md": 33,
    "kifrs_2102_bc.md": 25,
    "kifrs_2105_bc.md": 28,
    "kifrs_2106_bc.md": 10,
    "kifrs_2107_bc.md": 25,
    "kifrs_2110_bc.md": 12,
    "kifrs_2112_bc.md": 77,
    "kifrs_2114_bc.md": 44,
    "kifrs_2116_bc.md": 45,
    "kifrs_2117_bc.md": 66,
    "kifrs_2119_bc.md": 34,
    "kifrs_2120_bc.md": 21,
    "kifrs_2121_bc.md": 30,
    "kifrs_2122_bc.md": 33,
    "kifrs_2123_bc.md": 26,
    "kifrs_1001_ie.md": 12,
    "kifrs_1007_ie.md": 7,
    "kifrs_1008_ie.md": 16,
    "kifrs_1012_ie.md": 39,
    "kifrs_1019_ie.md": 1,
    "kifrs_1021_ie.md": 18,
    "kifrs_1024_ie.md": 26,
    "kifrs_1027_ie.md": 1,
    "kifrs_1028_ie.md": 1,
    "kifrs_1032_ie.md": 50,
    "kifrs_1033_ie.md": 14,
    "kifrs_1034_ie.md": 47,
    "kifrs_1036_ie.md": 100,
    "kifrs_1037_ie.md": 20,
    "kifrs_1038_ie.md": 10,
    "kifrs_1039_ie.md": 31,
    "kifrs_1041_ie.md": 4,
    "kifrs_1101_ie.md": 77,
    "kifrs_1102_ie.md": 34,
    "kifrs_1103_ie.md": 136,
    "kifrs_1105_ie.md": 19,
    "kifrs_1107_ie.md": 41,
    "kifrs_1108_ie.md": 7,
    "kifrs_1109_ie.md": 159,
    "kifrs_1110_ie.md": 15,
    "kifrs_1111_ie.md": 73,
    "kifrs_1113_ie.md": 66,
    "kifrs_1114_ie.md": 5,
    "kifrs_1115_ie.md": 368,
    "kifrs_1116_ie.md": 12,
    "kifrs_1117_ie.md": 215,
    "kifrs_2032_ie.md": 1,
    "kifrs_2101_ie.md": 18,
    "kifrs_2107_ie.md": 6,
    "kifrs_2112_ie.md": 38,
    "kifrs_2114_ie.md": 27,
    "kifrs_2116_ie.md": 5,
    "kifrs_2117_ie.md": 4,
    "kifrs_2121_ie.md": 1,
    "kifrs_2122_ie.md": 19,
    "kifrs_2123_ie.md": 10,
}

# 부록 A 용어정의 존재 검사(양성) — 파일별 기대 (정의 조각 수, 참조 조각 수). 수정 8
# '있어야 할 것의 부재'형 결함(최초 변환부터의 용어집 탈락)의 재발을 구조적으로 차단한다.
# 원본 표 행수에서 산출: 표 행수 − 용어 셀 줄넘김(GLOSS_WRAP) 결합분
GLOSS_EXPECT = {
    "kifrs_1101.md": (10, 0),   # 15행 − wrap 5
    "kifrs_1102.md": (20, 0),   # 21행 − wrap 1
    "kifrs_1103.md": (14, 0),
    "kifrs_1105.md": (13, 0),
    "kifrs_1106.md": (3, 0),
    "kifrs_1107.md": (8, 1),    # 참조1 = 1032·1039·1109·1113 정의 용어 목록
    "kifrs_1108.md": (1, 0),    # '영업부문' 단일 용어
    "kifrs_1109.md": (28, 1),   # 29행 − wrap 1 / 참조1 = 1032·1107·1113 정의 용어 목록
    "kifrs_1110.md": (12, 1),
    "kifrs_1111.md": (8, 1),
    "kifrs_1112.md": (3, 1),
    "kifrs_1113.md": (25, 0),   # 26행 − wrap 1
    "kifrs_1114.md": (7, 0),
    "kifrs_1115.md": (9, 0),
    "kifrs_1116.md": (32, 0),   # 33행 − wrap 1. 타 기준서 용어는 하위 절의 정의 조각
    "kifrs_1117.md": (22, 0),   # 25행 − wrap 3
    "ksa_1200.md": (65, None),  # 감사기준 유일의 용어표 (참조 검사는 감사기준 체계와 무관 — 제외)
}

# 출력에서 무마침표 문단머리로 의심되는 행 (잔존 시 실패)
NOPD_OUT = re.compile(r"^(한?[A-Z]{1,4}\.?\d[0-9A-Za-z.]*)[ \t]+\S")


def validate(expected):
    problems = []
    warnings = []
    all_ids = Counter()
    for fname, exp in sorted(expected.items()):
        out = (OUT / fname).read_text(encoding="utf-8")
        body = out.split("---\n", 2)[2]
        got = [HEAD_RE.match(l).group(1) for l in body.split("\n") if HEAD_RE.match(l)]
        if got != exp:
            # 첫 불일치 지점 리포트
            k = next((j for j, (a, b) in enumerate(zip(exp, got)) if a != b), min(len(exp), len(got)))
            problems.append(
                f"{fname}: 문단 시퀀스 불일치 exp={len(exp)} got={len(got)} "
                f"@{k}: exp={exp[k:k+3]} got={got[k:k+3]}"
            )
        dup = [p for p, n in Counter(got).items() if n > 1]
        if dup:
            problems.append(f"{fname}: 파일 내 중복 문단번호 {dup[:5]}")
        std_no = re.search(r'standard_no: "(.+?)"', out).group(1)
        for p in got:
            all_ids[(std_no, p)] += 1
        if "<!--" in body:
            problems.append(f"{fname}: HTML 주석 잔존")

        # 무마침표 문단머리 의심 행 잔존 검사
        suspects = [
            l[:40] for l in body.split("\n")
            if not l.startswith("\t") and not HEAD_RE.match(l) and NOPD_OUT.match(l)
        ]
        if suspects:
            problems.append(f"{fname}: 무마침표 문단머리 의심 행 {len(suspects)}건 {suspects[:3]}")

        # 문단 크기 측정 — 6,000자 초과는 WARN_ALLOW의 통짜 표·목록 2건으로 수렴해야 함
        cur, size = None, 0
        for l in body.split("\n") + [None]:
            if l is None or HEAD_RE.match(l):
                if cur and size > 6000:
                    warnings.append(f"{fname}: {cur} = {size:,}자")
                    if (fname, cur) not in WARN_ALLOW and not GALLEY_RE.search(fname):
                        problems.append(f"{fname}: 허용 외 6,000자 초과 문단 {cur} ({size:,}자)")
                if l is not None:
                    cur, size = HEAD_RE.match(l).group(1), len(l)
            else:
                size += len(l)

        # 정수 문단 건너뜀 검사 (허용목록 외 신규 구멍은 실패)
        ints = sorted({int(p) for p in got if p.isdigit()})
        gaps = [(a, b) for a, b in zip(ints, ints[1:]) if b > a + 1]
        new_gaps = [g for g in gaps if g not in INT_GAP_ALLOW.get(fname, set())]
        if new_gaps:
            problems.append(f"{fname}: 허용목록 외 정수 문단 건너뜀 {new_gaps[:6]}")

        # 예시문 분리 조각의 기대값 대조 (가상 번호만 — 부록-51 같은 원본 유래 번호는 제외)
        if fname in KSA_SPLIT_EXPECT:
            got_pseudo = [p for p in got if re.fullmatch(r"부록\d+|부록-사례\d+|참조\d+", p)]
            if got_pseudo != KSA_SPLIT_EXPECT[fname]:
                problems.append(
                    f"{fname}: 예시문 분리 불일치 exp={KSA_SPLIT_EXPECT[fname]} got={got_pseudo}"
                )

        # 부록 A 용어정의 존재 검사(양성): 기대 파일은 정의-/참조 조각 수 일치, 그 외 파일은 0
        n_def = sum(1 for p in got if p.startswith("정의-"))
        exp_def, exp_ref = GLOSS_EXPECT.get(fname, (0, 0 if fname != "kifrs_ps2.md" else None))
        if n_def != exp_def:
            problems.append(f"{fname}: 정의 조각 수 불일치 exp={exp_def} got={n_def}")
        if exp_ref is not None and fname.startswith("kifrs_"):
            n_ref = sum(1 for p in got if re.fullmatch(r"참조\d+", p))
            if n_ref != exp_ref:
                problems.append(f"{fname}: 참조 조각 수 불일치 exp={exp_ref} got={n_ref}")

        # 상설 구조 검사: 정규 번호 문단 블록이 '보론' 절 제목(##)을 가로지르면 병합 잔존
        # (부록/보론/사례/참조/정의 가상 번호 조각은 자기 하위 절을 품을 수 있으므로 제외)
        cur_head, boron_pend = None, False
        for l in body.split("\n"):
            hm2 = HEAD_RE.match(l)
            if hm2:
                cur_head, boron_pend = hm2.group(1), False
            elif l.startswith("## ") and "보론" in l:
                boron_pend = cur_head is not None and not cur_head.startswith(
                    ("부록", "보론", "사례", "참조", "정의")
                )
            elif boron_pend and l.strip() and not l.startswith("## "):
                problems.append(f"{fname}: {cur_head} 문단이 보론 절 제목을 가로지름 (병합 잔존)")
                boron_pend = False
        if fname == "kifrs_ps2.md":
            for kind, exp_ids in PS2_EXPECT.items():
                got_k = [p for p in got if p.startswith(kind)]
                if got_k != exp_ids:
                    problems.append(f"{fname}: {kind} 조각 불일치 exp={exp_ids} got={got_k}")

        # BC·IE 갈래 문단 수 고정 검사 (3단계 검수 후 활성화)
        if BCIE_EXPECT and GALLEY_RE.search(fname):
            exp_n = BCIE_EXPECT.get(fname)
            if exp_n is None:
                problems.append(f"{fname}: BCIE_EXPECT에 미등록 갈래 파일")
            elif len(got) != exp_n:
                problems.append(f"{fname}: 갈래 문단 수 불일치 exp={exp_n} got={len(got)}")

    if BCIE_EXPECT:
        missing = sorted(set(BCIE_EXPECT) - set(expected))
        if missing:  # 갈래 파일 통째 탈락(있어야 할 것의 부재) 검출 — C-13 전례
            problems.append(f"BCIE_EXPECT 등록 파일 미생성: {missing[:6]}")

    gdup = [k for k, n in all_ids.items() if n > 1]
    if gdup:
        problems.append(f"전역 ID 중복: {gdup[:8]}")
    return problems, warnings, len(all_ids)


def main():
    OUT.mkdir(exist_ok=True)
    expected = {}

    for p in sorted((ROOT / "auditstandard_md").glob("*.md")):
        if p.name == "00_전문.md":
            continue  # 목차 문서 — 규약 4.2 제거 대상
        if p.name == "ISQM-1.md":
            convert_isqm(expected)
        else:
            convert_isa_file(p, expected)

    ifrs_files = sorted((ROOT / "ifrs_md").glob("**/*.md")) + sorted(
        (ROOT / "Conceptual_framework_md").glob("*.md")
    )
    for p in ifrs_files:
        convert_ifrs_file(p, expected)

    problems, warnings, n_ids = validate(expected)
    n_para = sum(len(v) for v in expected.values())
    print(f"변환 완료: {len(expected)}개 파일, 문단 {n_para}개, 고유 ID {n_ids}개")
    g_bc = {f: len(v) for f, v in expected.items() if f.endswith("_bc.md")}
    g_ie = {f: len(v) for f, v in expected.items() if f.endswith("_ie.md")}
    if g_bc or g_ie:
        print(f"  BC·IE 갈래: bc {len(g_bc)}파일 {sum(g_bc.values()):,}문단 / "
              f"ie {len(g_ie)}파일 {sum(g_ie.values()):,}문단")
    if warnings:
        print(f"\n[경고] 6,000자 초과 문단 {len(warnings)}건 (적재기 분할 정책 대상):")
        for w in warnings:
            print(" -", w)
    if problems:
        print(f"\n검증 실패 {len(problems)}건:")
        for pr in problems:
            print(" -", pr)
        sys.exit(1)
    print("검증 통과: 시퀀스 일치, ID 유일, 주석 제거, 무마침표 잔존 0, 정수 건너뜀 허용목록 내")


if __name__ == "__main__":
    main()
