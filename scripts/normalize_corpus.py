#!/usr/bin/env python3
"""기존 코퍼스(auditstandard_md/, ifrs_md/, Conceptual_framework_md/)를
벡터저장소 규약(guidelines_raw/벡터저장소_스키마_및_마크다운_작성규약.md 4장) 형식으로
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
# 한?[A-Z]{0,4}\.?\d: BA.1(1109 부록 B 말미) 같은 '영문자+점+숫자' 계열 포함
HEAD_RE = re.compile(
    r"^(부록-사례\d+|부록-?[0-9A-Za-z()]+|보론\d*-\d+|사례[A-Z]|참조\d+|한?[A-Z]{0,4}\.?\d[0-9A-Za-z.-]*)\.\s"
)

# 원본에 para 주석 없이 놓인 정본 문단의 무마침표 행머리 (예: 'A1<TAB>본문', 'D1 ', 'BA.1')
NOPERIOD_HEAD = re.compile(r"^(?:\*\*)?(한?[A-Z]{1,4}\.?\d[0-9A-Za-z.]*)[ \t]+(\S.*)$")
SERIES_START = re.compile(r"한?[A-Z]{1,4}\.?1")

# 의결 문구(위원 명단·의결 사실 기재)는 정본 문단이 아니므로 제거
BOILER_HEAD = re.compile(r"^회계기준위원회 위원\s*:")
BOILER_VOTE = re.compile(r"회계기준위원회\S*\s*위원\s*\d+인.*의결하였다")


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
# ID는 규약 4.3의 가상 번호 — 부록N(부록 서두·목록형 부록), 부록-사례N(예시 보고서 한 건)
EX_SPLIT_FILES = {"700", "705", "720", "1200", "240", "710", "570", "1100", "600"}
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
    appendix_ord = 0     # 파일 내 부록(section: appendix) 순번
    in_ex_app = False    # 예시문 부록 구역 진입 여부
    pending_app = None   # 부록 서두 조각의 가상 번호 (첫 내용 행에서 방출, 사례가 먼저 오면 폐기)
    case_next = 1        # 다음에 나와야 할 사례 번호 (본문 속 '사례 N' 언급 오인 방지)

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
            title = next(
                (t for t in boron_titles if t.startswith(f"보론 {n}" if n else "보론:")), s
            )
            em.set_section(title)
            pending_num = None
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
            i += 1
            continue

        # 예시문 부록 구역: 사례 경계 절단 + 부록 서두 조각
        if in_ex_app:
            s2 = s.lstrip(">").strip() if s.startswith(">") else s
            mc = CASE_HEAD.match(s2)
            if mc and int(mc.group(1)) == case_next and (
                not s.startswith(">") or re.match(r"사례\s*\d+\s*[-–—::]", s2)
            ):
                em.para(f"부록-사례{case_next}.", s2)
                case_next += 1
                pending_app = None
                i += 1
                continue
            if pending_app is not None:
                em.para(pending_app + ".", s)
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
    dropped_warn = []
    promoted = []      # para 주석 없이 행머리 패턴으로 승격한 문단 (검수 보고용)
    n_boiler = 0
    ps2_app = False    # PS2 부록(개념체계와 기준서 참조) 구역
    ref_n = 0          # PS2 부록의 발췌 단위 순번 → 참조N

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
            elif "para" in c and not include and (bc_seen or ie_seen):
                if not str(c["para"]).startswith(("BC", "IE", "CU")):
                    dropped_warn.append(c["para"])
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
            continue

        if not include or not body_started:
            continue
        s = ln.rstrip()
        if not s.strip():
            in_toc_table = False
            continue
        # 의결 문구(위원 명단 등)는 정본 문단이 아님 — 제거
        if BOILER_HEAD.match(s.strip()) or BOILER_VOTE.search(s):
            n_boiler += 1
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

    if dropped_warn:
        print(f"  [경고] {path.name[:40]}: 결론도출근거/적용사례 이후 비BC·IE 문단 제외됨 {dropped_warn[:8]}")
    if promoted:
        print(f"  [복원] kifrs_{token}: 무주석 문단 {len(promoted)}개 승격 — {promoted[:6]}{'…' if len(promoted) > 6 else ''}")
    write_output(
        f"kifrs_{token}.md",
        [("source_type", "회계기준"), ("standard_no", std_no),
         ("standard_title", title),
         ("origin", str(path.relative_to(ROOT)))],
        em, expected,
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
    "ksa_1200.md": ["부록1", "부록2"] + [f"부록-사례{n}" for n in range(1, 11)],
    "ksa_240.md": ["부록1", "부록2", "부록3"],
    "ksa_710.md": [f"부록-사례{n}" for n in range(1, 5)],
    "ksa_570.md": ["부록1"] + [f"부록-사례{n}" for n in range(1, 4)],
    "ksa_1100.md": ["부록1"] + [f"부록-사례{n}" for n in range(1, 4)],
    # 600의 부록-51~ 번호 문단은 원본 유래라 기대 목록에서 제외 (부록N 서두 조각만 검사)
}

# PS2(중요성 실무서) 분리 기대: 사례 상자 A~T 20개 + 부록 발췌 참조1~4
PS2_EXPECT = {
    "사례": [f"사례{chr(c)}" for c in range(ord("A"), ord("T") + 1)],
    "참조": [f"참조{n}" for n in range(1, 5)],
}

# 6,000자 초과 잔존 허용 문단 — 이 2건(통짜 표·목록)으로 수렴해야 완료 (수정 6 완료 판정)
WARN_ALLOW = {("ksa_1200.md", "부록1"), ("ksa_240.md", "부록1")}

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
                    if (fname, cur) not in WARN_ALLOW:
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

        # 예시문 분리 조각의 기대값 대조
        if fname in KSA_SPLIT_EXPECT:
            got_pseudo = [p for p in got if p.startswith("부록")]
            if got_pseudo != KSA_SPLIT_EXPECT[fname]:
                problems.append(
                    f"{fname}: 예시문 분리 불일치 exp={KSA_SPLIT_EXPECT[fname]} got={got_pseudo}"
                )
        if fname == "ksa_600.md":
            got_intro = [p for p in got if re.fullmatch(r"부록\d", p)]
            if got_intro != [f"부록{n}" for n in range(1, 6)]:
                problems.append(f"{fname}: 보론 서두 조각 불일치 got={got_intro}")
        if fname == "kifrs_ps2.md":
            for kind, exp_ids in PS2_EXPECT.items():
                got_k = [p for p in got if p.startswith(kind)]
                if got_k != exp_ids:
                    problems.append(f"{fname}: {kind} 조각 불일치 exp={exp_ids} got={got_k}")

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
