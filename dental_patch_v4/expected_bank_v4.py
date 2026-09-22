from __future__ import annotations

import io
import re
import sqlite3
import unicodedata
from typing import Optional

ALL_SUBJECTS = [
    "의료관계법규", "구강해부학", "치아형태학", "구강조직발생학", "구강병리학",
    "구강생리학", "구강미생물학", "지역사회구강보건학", "구강보건행정학",
    "구강보건통계학", "구강보건교육학", "예방치과학", "치면세마론", "치과방사선학",
    "치과보존학", "치과보철학", "치과교정학", "소아치과학", "구강악안면외과학",
    "치주학", "치과재료학",
]

SUBJECT_ALIASES = {
    "지역사회 구강보건학": "지역사회구강보건학",
    "지역사회보건학": "지역사회구강보건학",
    "구강보건 행정학": "구강보건행정학",
    "구강보건 통계학": "구강보건통계학",
    "구강보건 교육학": "구강보건교육학",
}


def normalize_subject(value: str) -> Optional[str]:
    value = unicodedata.normalize("NFKC", value or "").strip()
    value = re.sub(r"[\s_\-]+", "", value)
    for subject in ALL_SUBJECTS:
        if re.sub(r"[\s_\-]+", "", subject) == value:
            return subject
    for alias, subject in SUBJECT_ALIASES.items():
        if re.sub(r"[\s_\-]+", "", alias) == value:
            return subject
    return None


def detect_subject(filename: str, text: str = "") -> Optional[str]:
    source = f"{filename}\n{text[:1000]}"
    for subject in ALL_SUBJECTS:
        if subject in source:
            return subject
        compact = re.sub(r"[\s_\-]+", "", source)
        if re.sub(r"[\s_\-]+", "", subject) in compact:
            return subject
    for alias, subject in SUBJECT_ALIASES.items():
        if alias in source or re.sub(r"[\s_\-]+", "", alias) in re.sub(r"[\s_\-]+", "", source):
            return subject
    return None


def init_expected_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
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
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (set_id) REFERENCES expected_question_sets(id),
            UNIQUE(set_id, question_number)
        );

        CREATE TABLE IF NOT EXISTS expected_choices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL,
            choice_number INTEGER NOT NULL,
            choice_text TEXT NOT NULL,
            FOREIGN KEY (question_id) REFERENCES expected_questions(id),
            UNIQUE(question_id, choice_number)
        );

        CREATE TABLE IF NOT EXISTS expected_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            question_id INTEGER NOT NULL,
            selected_choice INTEGER,
            correct_choice INTEGER NOT NULL,
            is_correct INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id),
            FOREIGN KEY (question_id) REFERENCES expected_questions(id)
        );

        CREATE INDEX IF NOT EXISTS idx_expected_questions_subject
        ON expected_questions(subject);

        CREATE INDEX IF NOT EXISTS idx_expected_attempts_user_created
        ON expected_attempts(user_id, created_at);
        """
    )
    conn.commit()


def _clean_line(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _read_docx_lines(data: bytes) -> list[str]:
    from docx import Document

    doc = Document(io.BytesIO(data))
    lines: list[str] = []
    for p in doc.paragraphs:
        t = _clean_line(p.text)
        if t:
            lines.append(t)
    # Also support simple tables, preserving row/cell order.
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                t = _clean_line(cell.text)
                if t:
                    lines.append(t)
    return lines


QUESTION_RE = re.compile(r"^(\d+)\s*[\.)]\s*(.+)$")
CHOICE_RE = re.compile(r"^(?:[①②③④⑤]|[1-5])\s*[\.)]?\s*(.*)$")
ANSWER_RE = re.compile(r"^정답\s*[:：]?\s*([①②③④⑤1-5])\s*$")
EXPL_RE = re.compile(r"^해설\s*[:：]?\s*(.*)$")
DIGIT_TO_NUM = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5}


def _parse_answer(value: str) -> int:
    value = value.strip()
    if value in DIGIT_TO_NUM:
        return DIGIT_TO_NUM[value]
    if value.isdigit() and 1 <= int(value) <= 5:
        return int(value)
    raise ValueError(f"정답 형식을 읽을 수 없습니다: {value}")


def parse_docx(data: bytes, filename: str, subject: Optional[str] = None) -> dict:
    lines = _read_docx_lines(data)
    detected_subject = normalize_subject(subject) if subject else None
    if detected_subject is None:
        detected_subject = detect_subject(filename, "\n".join(lines))
    if detected_subject is None:
        raise ValueError("과목을 자동 인식하지 못했습니다. 파일명에 과목명을 넣거나 업로드 화면에서 과목을 선택하세요.")

    questions: list[dict] = []
    current: Optional[dict] = None
    state = None

    def finalize() -> None:
        nonlocal current
        if current is None:
            return
        if len(current["choices"]) != 5:
            raise ValueError(f"{current['number']}번: 선택지가 {len(current['choices'])}개입니다. 5개가 필요합니다.")
        if current.get("answer") is None:
            raise ValueError(f"{current['number']}번: 정답을 찾지 못했습니다.")
        current["question_text"] = current["question_text"].strip()
        current["explanation"] = current.get("explanation", "").strip()
        questions.append(current)
        current = None

    for line in lines:
        m = QUESTION_RE.match(line)
        if m and not line.startswith("정답"):
            # Only treat it as a question number when it is outside an active explanation.
            if current is not None and state == "explanation":
                # A new numbered line ends the previous explanation.
                finalize()
            elif current is not None and current.get("answer") is not None:
                finalize()
            current = {
                "number": int(m.group(1)),
                "question_text": m.group(2).strip(),
                "choices": [],
                "answer": None,
                "explanation": "",
            }
            state = "question"
            continue

        if current is None:
            continue

        m = CHOICE_RE.match(line)
        if m and len(current["choices"]) < 5:
            current["choices"].append(m.group(1).strip())
            state = "choices"
            continue

        m = ANSWER_RE.match(line)
        if m:
            current["answer"] = _parse_answer(m.group(1))
            state = "answer"
            continue

        m = EXPL_RE.match(line)
        if m:
            current["explanation"] = m.group(1).strip()
            state = "explanation"
            continue

        if state == "question":
            current["question_text"] += " " + line
        elif state == "choices" and current["choices"]:
            current["choices"][-1] += " " + line
        elif state == "explanation":
            current["explanation"] += " " + line

    finalize()

    if not questions:
        raise ValueError("문항을 찾지 못했습니다. 예: '1. 문제', '① 보기', '정답: ②', '해설: ...' 형식을 사용하세요.")

    return {"filename": filename, "subject": detected_subject, "questions": questions}


def import_docx_to_db(conn: sqlite3.Connection, data: bytes, filename: str, subject: Optional[str] = None) -> dict:
    init_expected_tables(conn)
    parsed = parse_docx(data, filename, subject)

    existing = conn.execute("SELECT id FROM expected_question_sets WHERE filename=?", (filename,)).fetchone()
    if existing:
        return {"success": True, "skipped": True, "inserted": 0, "subject": parsed["subject"], "message": f"{filename}: 이미 등록된 파일이라 건너뛰었습니다."}

    cur = conn.cursor()
    cur.execute(
        "INSERT INTO expected_question_sets(filename,subject) VALUES(?,?)",
        (filename, parsed["subject"]),
    )
    set_id = cur.lastrowid

    for q in parsed["questions"]:
        cur.execute(
            "INSERT INTO expected_questions(set_id,subject,question_number,question_text,answer,explanation) VALUES(?,?,?,?,?,?)",
            (set_id, parsed["subject"], q["number"], q["question_text"], q["answer"], q["explanation"]),
        )
        qid = cur.lastrowid
        for idx, choice in enumerate(q["choices"], start=1):
            cur.execute(
                "INSERT INTO expected_choices(question_id,choice_number,choice_text) VALUES(?,?,?)",
                (qid, idx, choice),
            )

    conn.commit()
    return {
        "success": True,
        "skipped": False,
        "inserted": len(parsed["questions"]),
        "subject": parsed["subject"],
        "message": f"{filename}: {len(parsed['questions'])}문제 등록 완료 · {parsed['subject']}",
    }


def expected_stats(conn: sqlite3.Connection) -> list[dict]:
    init_expected_tables(conn)
    rows = conn.execute(
        "SELECT subject, COUNT(*) total FROM expected_questions GROUP BY subject"
    ).fetchall()
    counts = {row[0]: row[1] for row in rows}
    return [{"subject": s, "total": counts.get(s, 0)} for s in ALL_SUBJECTS]


def get_random_expected(conn: sqlite3.Connection, subject: str) -> Optional[dict]:
    init_expected_tables(conn)
    q = conn.execute(
        """
        SELECT id, subject, question_number, question_text, answer, explanation
        FROM expected_questions
        WHERE subject=?
        ORDER BY RANDOM()
        LIMIT 1
        """,
        (subject,),
    ).fetchone()
    if not q:
        return None
    choices = conn.execute(
        "SELECT choice_number, choice_text FROM expected_choices WHERE question_id=? ORDER BY choice_number",
        (q["id"],),
    ).fetchall()
    return {
        "id": q["id"],
        "subject": q["subject"],
        "question_number": q["question_number"],
        "question_text": q["question_text"],
        "answer": q["answer"],
        "explanation": q["explanation"],
        "choices": [{"number": c["choice_number"], "text": c["choice_text"]} for c in choices],
    }
