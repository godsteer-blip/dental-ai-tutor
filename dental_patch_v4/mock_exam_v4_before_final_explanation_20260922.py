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



SUBJECT_SUMMARY_FILES = {
    "의료관계법규": "법규.docx",
    "구강해부학": "구강해부학.docx",
    "치아형태학": "치아형태학.docx",
    "구강조직발생학": "구강조직발생학.docx",
    "구강병리학": "구강병리학.docx",
    "구강생리학": "구강생리학.docx",
    "구강미생물학": "구강미생물학.docx",
    "지역사회구강보건학": "지역사회구강보건학.docx",
    "구강보건행정학": "구강보건행정학.docx",
    "구강보건통계학": "구강보건통계학.docx",
    "구강보건교육학": "구강보건교육학.docx",
    "예방치과학": "예방치과학.docx",
    "치면세마론": "치면세마.docx",
    "치과방사선학": "방사선학.docx",
    "치과보존학": "치과보존학.docx",
    "치과보철학": "치과보철학.docx",
    "치과교정학": "치과교정학.docx",
    "소아치과학": "소아치과학.docx",
    "구강악안면외과학": "구강악안면외과학.docx",
    "치주학": "치주학.docx",
    "치과재료학": "치과재료학.docx",
}


def _summary_path(subject: str):
    from pathlib import Path

    filename = SUBJECT_SUMMARY_FILES.get(subject)
    if not filename:
        return None

    return (
        Path(__file__).resolve().parent.parent
        / "data"
        / "subject_summaries"
        / filename
    )


def _summary_paragraphs(subject: str) -> list[str]:
    from docx import Document

    path = _summary_path(subject)
    if path is None or not path.exists():
        return []

    doc = Document(str(path))
    return [p.text.strip() for p in doc.paragraphs if p.text.strip()]


def _relevant_summary(
    question_text: str,
    choices: list[str],
    paragraphs: list[str],
    limit: int = 12,
) -> str:
    if not paragraphs:
        return ""

    text = question_text + " " + " ".join(choices)
    tokens = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", text.lower()))

    scored = []
    for i, paragraph in enumerate(paragraphs):
        p_tokens = set(re.findall(r"[가-힣A-Za-z0-9]{2,}", paragraph.lower()))
        score = len(tokens & p_tokens)
        if score:
            scored.append((score, i, paragraph))

    scored.sort(key=lambda x: (-x[0], x[1]))
    selected = [x[2] for x in scored[:limit]]
    return "\n".join(selected)


def _generate_exam_explanation(
    subject: str,
    question_text: str,
    choices: list[str],
    answer: int,
) -> str:
    paragraphs = _summary_paragraphs(subject)
    evidence = _relevant_summary(question_text, choices, paragraphs)

    if not evidence:
        return "요약 정리에서 이 문제와 직접 연결되는 내용을 찾지 못했습니다."

    prompt = f"""너는 치위생사 국가시험 학습 튜터이다.
아래 기출문제와 선택지, 그리고 해당 과목의 학생용 요약 정리만 근거로 해설을 작성하라.
새로운 사실을 임의로 추가하지 마라.
요약 정리에 근거가 없는 내용은 단정하지 마라.

과목: {subject}
문제: {question_text}

선택지:
""" + "\n".join(
        f"{i + 1}. {choice}" for i, choice in enumerate(choices)
    ) + f"""

정답: {answer}번

[요약 정리에서 찾은 관련 내용]
{evidence}

해설은 한국어로 3~6문장 정도로 작성하고,
왜 정답인지와 필요하면 핵심 오답 포인트를 설명하라.
"""

    return _gemini_generate(prompt)


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
        explanation = _generate_exam_explanation(subject, str(selected['question_text']).strip(), choices, int(selected['answer']))
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
