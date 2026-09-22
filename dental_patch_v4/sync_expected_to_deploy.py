from __future__ import annotations

import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / "data" / "dental_tutor.db"
DEPLOY = ROOT / "data" / "dental_tutor_deploy.db"

if not LOCAL.exists():
    raise SystemExit(f"로컬 DB가 없습니다: {LOCAL}")
if not DEPLOY.exists():
    raise SystemExit(f"배포용 DB가 없습니다: {DEPLOY}")

src = sqlite3.connect(LOCAL)
src.row_factory = sqlite3.Row
dst = sqlite3.connect(DEPLOY)
try:
    # 스키마 생성
    for db in (src, dst):
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS expected_question_sets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL UNIQUE,
                subject TEXT NOT NULL,
                imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS expected_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                set_id INTEGER NOT NULL,
                subject TEXT NOT NULL,
                question_number INTEGER NOT NULL,
                question_text TEXT NOT NULL,
                answer INTEGER NOT NULL,
                explanation TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS expected_choices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL,
                choice_number INTEGER NOT NULL,
                choice_text TEXT NOT NULL
            );
            """
        )
        db.commit()

    # seed DB에는 예상문제 콘텐츠만 동기화하고 학습기록은 복사하지 않는다.
    dst.execute("DELETE FROM expected_choices")
    dst.execute("DELETE FROM expected_questions")
    dst.execute("DELETE FROM expected_question_sets")

    sets = src.execute("SELECT * FROM expected_question_sets ORDER BY id").fetchall()
    set_map = {}
    for s in sets:
        cur = dst.execute(
            "INSERT INTO expected_question_sets(filename,subject,imported_at) VALUES(?,?,?)",
            (s["filename"], s["subject"], s["imported_at"]),
        )
        set_map[s["id"]] = cur.lastrowid

    questions = src.execute("SELECT * FROM expected_questions ORDER BY id").fetchall()
    q_map = {}
    for q in questions:
        cur = dst.execute(
            "INSERT INTO expected_questions(set_id,subject,question_number,question_text,answer,explanation,created_at) VALUES(?,?,?,?,?,?,?)",
            (
                set_map[q["set_id"]], q["subject"], q["question_number"], q["question_text"],
                q["answer"], q["explanation"], q["created_at"],
            ),
        )
        q_map[q["id"]] = cur.lastrowid

    choices = src.execute("SELECT * FROM expected_choices ORDER BY id").fetchall()
    for c in choices:
        dst.execute(
            "INSERT INTO expected_choices(question_id,choice_number,choice_text) VALUES(?,?,?)",
            (q_map[c["question_id"]], c["choice_number"], c["choice_text"]),
        )
    dst.commit()

    count = dst.execute("SELECT COUNT(*) FROM expected_questions").fetchone()[0]
finally:
    dst.close()
    src.close()

print(f"배포용 DB 예상문제 동기화 완료: {count}문제")
