from __future__ import annotations

import json
import re
import sqlite3
from typing import Optional

SESSION_SUBJECT_QUOTAS = {
    1: [
        ("의료관계법규", 20),
        ("구강해부학", 7),
        ("치아형태학", 7),
        ("구강조직발생학", 7),
        ("구강병리학", 7),
        ("구강생리학", 7),
        ("구강미생물학", 5),
        ("지역사회구강보건학", 12),
        ("구강보건행정학", 10),
        ("구강보건통계학", 8),
        ("구강보건교육학", 10),
    ],
    2: [
        ("예방치과학", 18),
        ("치면세마론", 20),
        ("치과방사선학", 20),
        ("치과보존학", 6),
        ("치과보철학", 6),
        ("치과교정학", 6),
        ("소아치과학", 6),
        ("구강악안면외과학", 6),
        ("치주학", 6),
        ("치과재료학", 6),
    ],
}


def _quota_total(session: int) -> int:
    return sum(n for _, n in SESSION_SUBJECT_QUOTAS[session])


def init_mock_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ai_mock_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            session_no INTEGER NOT NULL,
            total_questions INTEGER NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id)
        );

        CREATE TABLE IF NOT EXISTS ai_mock_questions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            question_index INTEGER NOT NULL,
            session_no INTEGER NOT NULL,
            subject TEXT NOT NULL,
            question_text TEXT NOT NULL,
            choices_json TEXT NOT NULL,
            correct_choice INTEGER NOT NULL,
            explanation TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES ai_mock_sessions(id),
            UNIQUE(session_id, question_index)
        );

        CREATE TABLE IF NOT EXISTS ai_mock_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            mock_question_id INTEGER NOT NULL,
            user_id INTEGER,
            selected_choice INTEGER,
            correct_choice INTEGER NOT NULL,
            is_correct INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES ai_mock_sessions(id),
            FOREIGN KEY (mock_question_id) REFERENCES ai_mock_questions(id),
            FOREIGN KEY (user_id) REFERENCES users(id)
        );
        """
    )
    conn.commit()


def subject_for_index(session: int, index: int) -> str:
    if session not in SESSION_SUBJECT_QUOTAS:
        raise ValueError("교시는 1 또는 2여야 합니다.")
    if not 1 <= index <= _quota_total(session):
        raise ValueError(f"문제 번호는 1~{_quota_total(session)}입니다.")
    cursor = 0
    for subject, count in SESSION_SUBJECT_QUOTAS[session]:
        if cursor < index <= cursor + count:
            return subject
        cursor += count
    raise ValueError("과목 배정 오류")


def _gemini_generate(prompt: str) -> str:
    import os
    from google import genai

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY가 설정되어 있지 않습니다.")
    model = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
    client = genai.Client(api_key=api_key)
    interaction = client.interactions.create(model=model, input=prompt)
    output = (getattr(interaction, "output_text", None) or "").strip()
    if not output:
        raise RuntimeError("Gemini가 빈 응답을 반환했습니다.")
    return output


def _json_from_text(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    match = re.search(r"\{.*\}", text, re.S)
    if not match:
        raise ValueError("AI 응답에서 JSON을 찾지 못했습니다.")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("AI 응답 형식이 올바르지 않습니다.")
    return data


def generate_question(conn: sqlite3.Connection, session: int, index: int, session_id: Optional[int] = None) -> dict:
    subject = subject_for_index(session, index)

    used_texts = set()
    if session_id is not None:
        rows = conn.execute('SELECT question_text FROM ai_mock_questions WHERE session_id=?', (session_id,)).fetchall()
        used_texts = {str(row['question_text']) for row in rows}

    candidates = []

    exam_rows = conn.execute(
        'SELECT q.id, q.question_text, q.answer FROM exam_questions q WHERE q.subject=? ORDER BY RANDOM()',
        (subject,),
    ).fetchall()

    for row in exam_rows:
        if row['question_text'] not in used_texts:
            candidates.append({
                'source': 'exam',
                'id': row['id'],
                'question_text': row['question_text'],
                'answer': int(row['answer']),
            })

    expected_rows = conn.execute(
        'SELECT id, question_text, answer, explanation FROM expected_questions WHERE subject=? ORDER BY RANDOM()',
        (subject,),
    ).fetchall()

    for row in expected_rows:
        if row['question_text'] not in used_texts:
            candidates.append({
                'source': 'expected',
                'id': row['id'],
                'question_text': row['question_text'],
                'answer': int(row['answer']),
                'explanation': str(row['explanation'] or ''),
            })

    if not candidates:
        raise ValueError(f'{subject} 과목에서 출제할 수 있는 문제가 없습니다.')

    import random
    selected = random.choice(candidates)

    if selected['source'] == 'exam':
        choice_rows = conn.execute(
            'SELECT choice_number, choice_text FROM question_choices WHERE question_id=? ORDER BY choice_number',
            (selected['id'],),
        ).fetchall()
        choices = [str(row['choice_text']).strip() for row in choice_rows]
        explanation = ''
    else:
        choice_rows = conn.execute(
            'SELECT choice_number, choice_text FROM expected_choices WHERE question_id=? ORDER BY choice_number',
            (selected['id'],),
        ).fetchall()
        choices = [str(row['choice_text']).strip() for row in choice_rows]
        explanation = selected['explanation']

    if len(choices) != 5:
        raise ValueError(f'{subject} 과목의 출제 문제에 선택지 5개가 필요합니다.')
    if any(not choice for choice in choices):
        raise ValueError('빈 선택지가 포함된 문제가 있습니다.')

    return {
        'subject': subject,
        'question_text': str(selected['question_text']).strip(),
        'choices': choices,
        'answer': selected['answer'],
        'explanation': explanation,
        'source': selected['source'],
    }


def start_session(conn: sqlite3.Connection, session: int, user_id: Optional[int]) -> dict:
    init_mock_tables(conn)
    if session not in SESSION_SUBJECT_QUOTAS:
        raise ValueError("교시는 1 또는 2여야 합니다.")
    total = _quota_total(session)
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ai_mock_sessions(user_id,session_no,total_questions) VALUES(?,?,?)",
        (user_id, session, total),
    )
    session_id = cur.lastrowid
    conn.commit()
    return {"session_id": session_id, "session": session, "total_questions": total}
