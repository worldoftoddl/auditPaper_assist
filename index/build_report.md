# 2단계 적재 리포트 (2026-07-12T08:00:00.507651+00:00)
파서: 20536문단 (감사기준 3630 / 회계기준 16729 / 실무지침 177) → 분할 후 20552레코드
para_type 분포: {'결론도출근거': 8721, '적용지침': 4468, '본문': 3922, '적용사례': 1753, '요구사항': 1279, '정의': 280, '부록': 99, '참조': 14}
용어 사전: 총 664건 = ①정의조각 280 + ②구기준 187 + ③감사기준 197
  ②구기준 파일별: kifrs_1001.md:10, kifrs_1002.md:3, kifrs_1007.md:6, kifrs_1008.md:9, kifrs_1010.md:1, kifrs_1012.md:8, kifrs_1016.md:12, kifrs_1019.md:23, kifrs_1020.md:7, kifrs_1021.md:12, kifrs_1023.md:2, kifrs_1024.md:7, kifrs_1026.md:8, kifrs_1027.md:2, kifrs_1028.md:8, kifrs_1032.md:4, kifrs_1033.md:8, kifrs_1034.md:2, kifrs_1036.md:11, kifrs_1037.md:9, kifrs_1038.md:14, kifrs_1039.md:5, kifrs_1040.md:5, kifrs_1041.md:11
  ③감사기준 파일별: ksa_1100.md:6, ksa_200.md:18, ksa_210.md:1, ksa_220.md:16, ksa_230.md:3, ksa_240.md:2, ksa_250.md:1, ksa_260.md:2, ksa_265.md:2, ksa_315.md:13, ksa_320.md:1, ksa_330.md:2, ksa_402.md:9, ksa_450.md:2, ksa_500.md:6, ksa_505.md:5, ksa_510.md:3, ksa_520.md:1, ksa_530.md:10, ksa_540.md:6, ksa_550.md:2, ksa_560.md:4, ksa_580.md:1, ksa_600.md:13, ksa_610.md:2, ksa_620.md:3, ksa_700.md:3, ksa_701.md:1, ksa_705.md:2, ksa_706.md:2, ksa_710.md:1, ksa_720.md:3, ksa_assr-3000.md:32, ksa_isqm-1.md:19
sparse: 어휘집 9,717 토큰, avgdl 77.9364
dense: 외부 임베딩 사용 (--embeddings)
외부 dense 결합: index/embeddings.npy (sha256 4557cf8cb88c…), cid 조인 20552건
── 점검 1~7 (규약 5장) ──
  [PASS] 1 문단 수 포인트 20552 (기대 20552)
  [PASS] 2 seq 연속성(파일 스코프) 이상 []
  [PASS] 3 ID 유일성 복합 20552 / UUID 20552 / 포인트 20552
  [정보] 4 표 표본(사람 육안): KIFRS::1117::BC407, KIFRS::1116::IE2#1, KIFRS::1115::BC510#1, KIFRS::1108::BC62#1, KIFRS::1115::BC510#2
  [PASS] 5 벡터 dense 20552 / sparse 20552 / 이상 0
  [정보] 6 무마침표 — 코퍼스 검증기 승계 (매니페스트의 커밋 해시로 갈음)
  [PASS] 7 분할 정합 {('1001', 'BC106'): [(1, 2), (2, 2)], ('1001', 'IG6'): [(1, 2), (2, 2)], ('1012', 'IE사례3-4'): [(1, 3), (2, 3), (3, 3)], ('1108', 'BC62'): [(1, 3), (2, 3), (3, 3)], ('1109', 'BCG.2'): [(1, 4), (2, 4), (3, 4), (4, 4)], ('1109', 'IE159'): [(1, 3), (2, 3), (3, 3)], ('1115', 'BC510'): [(1, 4), (2, 4), (3, 4), (4, 4)], ('1116', 'IE2'): [(1, 2), (2, 2)], ('240', '부록1'): [(1, 2), (2, 2)]}
  [정보] 8 cross_refs 실재성 — v2 백로그, 보류
── 스모크 S1~S9 ──
  [PASS] S1 라우팅 315 351 = 351
  [PASS] S2 직조회 KIFRS::1115::31 
  [PASS] S3 dense '수행의무의 정의' top5=['KIFRS::1115::BC84', 'KIFRS::1115::정의-수행의무', 'KIFRS::1115::BC86', 'KIFRS::1115::BC113', 'KIFRS::1115::BC85']
  [PASS] S4 dense '지배력의 세 가지 요소' top5=['KIFRS::1110::BC41', 'KIFRS::1110::B80', 'KIFRS::1110::8', 'KIFRS::1110::BC62', 'KIFRS::1110::B85']
  [PASS] S5 하이브리드 '핵심감사사항' top5=['KSA::701::A11', 'KSA::701::A29', 'KSA::701::16', 'KSA::701::13', 'KSA::701::10']
  [PASS] S5' +감사기준 필터 top5=['KSA::701::A11', 'KSA::701::A29', 'KSA::701::16', 'KSA::701::13', 'KSA::701::10']
  [PASS] S6 정의×1200 65건
  [PASS] S7 KIFRS::1001::BC106 재조립 2건 part_no=[1, 2]
  [PASS] S7 KIFRS::1001::IG6 재조립 2건 part_no=[1, 2]
  [PASS] S7 KIFRS::1012::IE사례3-4 재조립 3건 part_no=[1, 2, 3]
  [PASS] S7 KIFRS::1108::BC62 재조립 3건 part_no=[1, 2, 3]
  [PASS] S7 KIFRS::1109::BCG.2 재조립 4건 part_no=[1, 2, 3, 4]
  [PASS] S7 KIFRS::1109::IE159 재조립 3건 part_no=[1, 2, 3]
  [PASS] S7 KIFRS::1115::BC510 재조립 4건 part_no=[1, 2, 3, 4]
  [PASS] S7 KIFRS::1116::IE2 재조립 2건 part_no=[1, 2]
  [PASS] S7 KSA::240::부록1 재조립 2건 part_no=[1, 2]
  [PASS] S8 참조 14건
  [PASS] S9 왕복+본문 전수 20552건 검사, 불일치 0
  [PASS] S10 직조회 KIFRS::1116::BC1 
  [PASS] S11 결론도출근거 DB 8730 = 로컬 8730
  [PASS] S11 적용사례 DB 1759 = 로컬 1759
── P1~P3 장문 프로브 (기록용: dense 순위 / 하이브리드 순위) ──
  [기록] P1 '전문가적 의구심의 정의' → KSA::200::13: dense >20위 / 하이브리드 >20위 (하이브리드 top3: ['KSA::ASSR-3000::A78', 'KSA::FRMK-1::53', 'KSA::540::A11'])
  [기록] P2 '일괄상계약정' → KIFRS::1032::50: dense 1위 / 하이브리드 1위 (하이브리드 top3: ['KIFRS::1032::50', 'KIFRS::1107::BC24U', 'KIFRS::1115::BC79'])
  [기록] P3 '재고자산 정의' → KIFRS::1002::6: dense 2위 / 하이브리드 5위 (하이브리드 top3: ['KIFRS::1002::9', 'KIFRS::1002::8', 'KIFRS::1002::26'])
메타 컬렉션 standards_20250829_bgem3_v2_meta: manifest 1 + vocab 1 + glossary 664 = 666포인트
매니페스트 기록: index/manifest.json (코퍼스 커밋 9f66864)
