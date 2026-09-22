# ==================== GEMINI_INTERACTIONS_FIX_V3 ====================

from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "dental_tutor.db"

FALLBACK_TEXT = "해당 문제의 상세 해설은 교과서·요약집을 참조하세요."

DEFAULT_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.6-flash",
)


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_cache_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ai_explanations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question_id INTEGER NOT NULL UNIQUE,
            evidence_fingerprint TEXT NOT NULL,
            model_name TEXT NOT NULL,
            mode TEXT NOT NULL,
            explanation TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )


def _load_question_and_evidence(question_id: int):
    conn = _connect()

    try:
        _ensure_cache_table(conn)

        question = conn.execute(
            """
            SELECT id, subject, question_text, answer
            FROM exam_questions
            WHERE id = ?
            """,
            (question_id,),
        ).fetchone()

        if question is None:
            return None, [], None

        choices = conn.execute(
            """
            SELECT choice_number, choice_text
            FROM question_choices
            WHERE question_id = ?
            ORDER BY choice_number
            """,
            (question_id,),
        ).fetchall()

        evidence = conn.execute(
            """
            SELECT
                qsl.summary_chunk_id,
                qsl.score,
                qsl.link_rank,
                ssc.subject AS summary_subject,
                ssc.chunk_number,
                ssc.text
            FROM question_summary_links AS qsl
            JOIN subject_summary_chunks AS ssc
              ON ssc.id = qsl.summary_chunk_id
            WHERE qsl.question_id = ?
            ORDER BY qsl.link_rank
            LIMIT 5
            """,
            (question_id,),
        ).fetchall()

        return question, choices, evidence

    finally:
        conn.close()


def _fingerprint(question, evidence):
    raw = (
        str(question["id"])
        + "|"
        + str(question["question_text"] or "")
        + "|"
        + "|".join(
            f'{row["summary_chunk_id"]}:{row["score"]}:{row["text"]}'
            for row in evidence
        )
    )

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def _cached(question_id, fingerprint):
    conn = _connect()

    try:
        _ensure_cache_table(conn)

        row = conn.execute(
            """
            SELECT mode, explanation, model_name, created_at,
                   evidence_fingerprint
            FROM ai_explanations
            WHERE question_id = ?
            """,
            (question_id,),
        ).fetchone()

        if row is None:
            return None

        if row["evidence_fingerprint"] != fingerprint:
            return None

        return {
            "mode": row["mode"],
            "explanation": row["explanation"],
            "model_name": row["model_name"],
            "created_at": row["created_at"],
            "cached": True,
        }

    finally:
        conn.close()


def _save_cache(
    question_id,
    fingerprint,
    mode,
    explanation,
    model_name,
):
    conn = _connect()

    try:
        _ensure_cache_table(conn)

        conn.execute(
            """
            INSERT INTO ai_explanations
                (
                    question_id,
                    evidence_fingerprint,
                    model_name,
                    mode,
                    explanation,
                    created_at
                )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(question_id)
            DO UPDATE SET
                evidence_fingerprint = excluded.evidence_fingerprint,
                model_name = excluded.model_name,
                mode = excluded.mode,
                explanation = excluded.explanation,
                created_at = excluded.created_at
            """,
            (
                question_id,
                fingerprint,
                model_name,
                mode,
                explanation,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )

        conn.commit()

    finally:
        conn.close()


def _build_prompt(question, choices, evidence):
    choice_text = "\n".join(
        f'{row["choice_number"]}. {row["choice_text"] or ""}'
        for row in choices
    )

    evidence_text = "\n\n".join(
        f"[근거 {index}]\n{row['text'] or ''}"
        for index, row in enumerate(evidence, start=1)
    )

    return f"""
너는 치위생학과 국가고시 학습을 돕는 AI 튜터다.

아래 문제와 요약본 근거만 사용하여 한국어로 문제 해설을 작성한다.

[반드시 지켜야 하는 규칙]
1. 제공된 요약본 근거로 확인할 수 있는 내용만 설명한다.
2. 네 일반 지식이나 추측으로 빈칸을 채우지 않는다.
3. 정답 번호는 제공된 정답값을 사용한다.
4. 근거가 충분하면 왜 정답인지 1~3문장으로 설명한다.
5. 제공된 근거가 허용할 때만 오답 선택지도 간단히 설명한다.
6. 새로운 수치, 기준, 법령, 진단정보를 임의로 추가하지 않는다.
7. 요약본 근거가 부족하거나 서로 충돌하면 아래 문장만 정확히 출력한다.
   "{FALLBACK_TEXT}"
8. 파일명, 청크 번호, 데이터베이스 ID, 내부 점수는 출력하지 않는다.
9. 시험공부용으로 간결하고 정확하게 작성한다.

[문제]
{question["question_text"] or ""}

[보기]
{choice_text}

[정답 번호]
{question["answer"]}

[요약본 근거]
{evidence_text}
""".strip()


def _call_gemini(prompt):
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        return None, "GEMINI_API_KEY 미설정"

    try:
        from google import genai

        client = genai.Client(
            api_key=api_key,
        )

        interaction = client.interactions.create(
            model=DEFAULT_MODEL,
            input=prompt,
        )

        text = (
            getattr(interaction, "output_text", None)
            or ""
        ).strip()

        if not text:
            return None, "AI 응답 없음"

        return text, None

    except Exception as error:
        return None, type(error).__name__


def get_ai_explanation(question_id: int):
    question, choices, evidence = (
        _load_question_and_evidence(question_id)
    )

    if question is None:
        return {
            "success": False,
            "mode": "fallback",
            "explanation": FALLBACK_TEXT,
            "evidence_count": 0,
        }

    fingerprint = _fingerprint(
        question,
        evidence,
    )

    cached = _cached(
        question_id,
        fingerprint,
    )

    if cached is not None:
        return {
            "success": True,
            "mode": cached["mode"],
            "explanation": cached["explanation"],
            "evidence_count": len(evidence),
            "cached": True,
        }

    if not evidence:
        return {
            "success": True,
            "mode": "fallback",
            "explanation": FALLBACK_TEXT,
            "evidence_count": 0,
            "cached": False,
        }

    prompt = _build_prompt(
        question,
        choices,
        evidence,
    )

    explanation, error_reason = _call_gemini(
        prompt
    )

    if (
        not explanation
        or explanation.strip() == FALLBACK_TEXT
    ):
        mode = "fallback"
        final_text = FALLBACK_TEXT
    else:
        mode = "ai"
        final_text = explanation.strip()

    if mode == "ai":
        _save_cache(
            question_id,
            fingerprint,
            mode,
            final_text,
            DEFAULT_MODEL,
        )

    return {
        "success": True,
        "mode": mode,
        "explanation": final_text,
        "evidence_count": len(evidence),
        "cached": False,
        "ai_error": error_reason,
    }