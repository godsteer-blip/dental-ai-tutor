치위생 AI 튜터 - V4 패치

1. 주의
이 패치는 기존 main.py를 자동 백업한 후 수정합니다.
2. 메인/과목 화면
개념 슬롯 제거
시험문제 유지
AI 문제 -> 예상문제로 변경
오답노트 -> 오답정리로 변경
랜덤 기출 -> AI 모의고사로 연결
3. 예상문제
과목별 DOCX 문제은행에서 랜덤 출제
정답/해설은 DOCX에 작성한 값을 사용
4. AI 모의고사
1교시 100문제, 2교시 100문제
각 과목의 국가시험 문항 수를 그대로 배분
5. 문제은행
expected_uploads 폴더에 DOCX를 넣고
python import_expected_docx.py
python sync_expected_to_deploy.py
실행 후 GitHub에 push하면 Render가 자동 배포합니다.
6. GitHub에 원본 DOCX는 올리지 않는 것을 권장합니다.
expected_uploads/는 .gitignore에 추가됩니다.
