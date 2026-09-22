from __future__ import annotations

import sqlite3
from pathlib import Path

from expected_bank_v4 import import_docx_to_db

ROOT = Path(__file__).resolve().parent.parent
UPLOADS = ROOT / "expected_uploads"
DB = ROOT / "data" / "dental_tutor.db"

UPLOADS.mkdir(exist_ok=True)
DB.parent.mkdir(exist_ok=True)

files = sorted(UPLOADS.glob("*.docx"))
if not files:
    print("expected_uploads 폴더에 DOCX가 없습니다.")
    print(f"폴더: {UPLOADS}")
    raise SystemExit(0)

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
try:
    for path in files:
        print("=" * 70)
        print("파일:", path.name)
        result = import_docx_to_db(conn, path.read_bytes(), path.name)
        print(result["message"])
finally:
    conn.close()

print("=" * 70)
print("예상문제 가져오기 완료")
print("중복 파일은 자동으로 건너뜁니다.")
