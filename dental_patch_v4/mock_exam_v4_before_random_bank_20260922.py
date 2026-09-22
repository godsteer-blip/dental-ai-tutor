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


def generate_question(conn: sqlite3.Connection, session: int, index: int) -> dict:
    subject = subject_for_index(session, index)
    examples = conn.execute(
        """
        SELECT q.question_text, q.answer,
               GROUP_CONCAT((qc.choice_number || '. ' || qc.choice_text), ' | ') choices
        FROM exam_questions q
        LEFT JOIN question_choices qc ON qc.question_id=q.id
        WHERE q.subject=?
        GROUP BY q.id, q.question_text, q.answer
        ORDER BY RANDOM()
        LIMIT 3
        """,
        (subject,),
    ).fetchall()
    examples_text = "\n".join(
        f"- {row['question_text']} / 정답 {row['answer']} / {row['choices']}"
        for row in examples
    )

    summary_rows = []
    try:
        summary_rows = conn.execute(
            "SELECT text FROM subject_summary_chunks WHERE subject=? ORDER BY RANDOM() LIMIT 3",
            (subject,),
        ).fetchall()
    except sqlite3.OperationalError:
        pass
    summary_text = "\n\n".join(row[0] for row in summary_rows if row[0])

    prompt = f"""
너는 치위생학과 국가시험 대비 AI 출제자다.
아래 과목의 새 5지선다 문제 1개를 만든다.
교시: {session}교시
과목: {subject}
문제번호: {index}

조건:
1. 한국어로 작성한다.
2. 보기 5개를 만든다.
3. 정답은 1~5 중 하나다.
4. 기존 예시 문제를 그대로 복사하지 않고 새 문항을 만든다.
5. 제공된 요약/기출 근거의 범위를 벗어난 세부사항을 임의로 추가하지 않는다.
6. 해설은 왜 정답인지 간결하게 설명한다.
7. 반드시 JSON만 반환한다.

JSON 형식:
{{
  "question_text": "...",
  "choices": ["...", "...", "...", "...", "..."],
  "answer": 1,
  "explanation": "..."
}}

[기존 기출 예시]
{examples_text or '(없음)'}

[과목 요약 근거]
{summary_text or '(없음)'}
""".strip()

    result = _json_from_text(_gemini_generate(prompt))
    qtext = str(result.get("question_text", "")).strip()
    choices = result.get("choices")
    answer = int(result.get("answer", 0))
    explanation = str(result.get("explanation", "")).strip()
    if not qtext or not isinstance(choices, list) or len(choices) != 5 or not 1 <= answer <= 5:
        raise ValueError("AI가 5지선다 문제 형식을 지키지 않았습니다.")
    choices = [str(x).strip() for x in choices]
    if any(not x for x in choices):
        raise ValueError("AI가 빈 선택지를 생성했습니다.")
    return {
        "subject": subject,
        "question_text": qtext,
        "choices": choices,
        "answer": answer,
        "explanation": explanation,
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
