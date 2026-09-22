import os
from pathlib import Path
import html
import json
import re
import sqlite3

import pymupdf
from fastapi import FastAPI, File, UploadFile, Request
from fastapi.responses import HTMLResponse, JSONResponse


# ============================================================
# 기본 설정
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

UPLOAD_DIR = BASE_DIR / "uploaded_pdfs"
DATA_DIR = BASE_DIR / "data"
CHUNK_DIR = DATA_DIR / "chunks"
DB_PATH = Path(os.getenv("DENTAL_DB_PATH", str(DATA_DIR / "dental_tutor.db")))
CONFIG = BASE_DIR / "firebase_config.js"

UPLOAD_DIR.mkdir(exist_ok=True)
DATA_DIR.mkdir(exist_ok=True)
CHUNK_DIR.mkdir(exist_ok=True)


# ==================== QUESTION_BANK_V4 ====================
from expected_bank_v4 import ALL_SUBJECTS, expected_stats, get_random_expected, import_docx_to_db, init_expected_tables
from mock_exam_v4 import SESSION_SUBJECT_QUOTAS, generate_question as generate_ai_mock_question, init_mock_tables, start_session as start_ai_mock_session, subject_for_index as mock_subject_for_index

app = FastAPI(
    title="치위생학과 AI 튜터",
    version="1.2.0",
)


# ============================================================
# DB
# ============================================================

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# 시험문제 데이터는 exam_questions / question_choices만 사용합니다.
# 별도 추가자료 테이블은 애플리케이션에서 사용하지 않습니다.


def escape_text(value):
    return html.escape("" if value is None else str(value))


# ============================================================
# 과목 구성
# ============================================================

SESSION_SUBJECTS = {
    1: [
        "의료관계법규",
        "구강해부학",
        "치아형태학",
        "구강조직발생학",
        "구강병리학",
        "구강생리학",
        "구강미생물학",
        "지역사회구강보건학",
        "구강보건행정학",
        "구강보건통계학",
        "구강보건교육학",
    ],
    2: [
        "예방치과학",
        "치면세마론",
        "치과방사선학",
        "치과보존학",
        "치과보철학",
        "치과교정학",
        "소아치과학",
        "구강악안면외과학",
        "치주학",
        "치과재료학",
    ],
}


# ============================================================
# 학습 자료 / 요약본 설정
# ============================================================

SUMMARY_RANGES = {
    "의료관계법규": (1, 12),
    "구강해부학": (13, 21),
    "치아형태학": (22, 25),
    "구강조직발생학": (26, 36),
    "구강병리학": (37, 44),
    "구강생리학": (45, 53),
    "구강미생물학": (54, 61),
    "지역사회구강보건학": (62, 69),
    "구강보건행정학": (70, 76),
    "구강보건통계학": (77, 82),
    "구강보건교육학": (83, 95),
    "예방치과학": (96, 109),
    "치면세마론": (110, 122),
    "치과방사선학": (123, 130),
    "구강악안면외과학": (131, 140),
    "치과보철학": (141, 149),
    "치과보존학": (150, 156),
    "소아치과학": (157, 165),
    "치주학": (166, 171),
    "치과교정학": (172, 180),
    "치과재료학": (181, 189),
}



def _table_columns(cursor, table_name):
    rows = cursor.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return {
        row[1]
        for row in rows
    }


def _first_existing(columns, candidates):
    for candidate in candidates:
        if candidate in columns:
            return candidate

    return None


def _summary_rows(subject):
    page_range = SUMMARY_RANGES.get(subject)

    if page_range is None:
        return []


    start_page, end_page = page_range

    conn = get_db()

    try:
        cursor = conn.cursor()

        # 먼저 페이지 테이블을 사용한다.
        if _table_columns(cursor, "summary_pages"):
            columns = _table_columns(
                cursor,
                "summary_pages"
            )

            page_col = _first_existing(
                columns,
                [
                    "page_number",
                    "page",
                    "source_page",
                ],
            )

            text_col = _first_existing(
                columns,
                [
                    "text",
                    "page_text",
                    "content",
                ],
            )

            if page_col and text_col:

                rows = cursor.execute(
                    f"""
                    SELECT
                        {page_col} AS page_no,
                        {text_col} AS content
                    FROM summary_pages
                    WHERE {page_col} BETWEEN ? AND ?
                    ORDER BY {page_col}
                    """,
                    (
                        start_page,
                        end_page,
                    ),
                ).fetchall()

                return [
                    {
                        "page": row["page_no"],
                        "text": row["content"] or "",
                    }
                    for row in rows
                    if row["content"]
                ]


        # 페이지 테이블 구조가 다른 경우 chunk 테이블을 사용한다.
        if _table_columns(cursor, "summary_chunks"):

            columns = _table_columns(
                cursor,
                "summary_chunks"
            )

            text_col = _first_existing(
                columns,
                [
                    "text",
                    "chunk_text",
                    "content",
                ],
            )

            page_col = _first_existing(
                columns,
                [
                    "page_number",
                    "page",
                    "source_page",
                ],
            )

            if text_col and page_col:

                rows = cursor.execute(
                    f"""
                    SELECT
                        {page_col} AS page_no,
                        {text_col} AS content
                    FROM summary_chunks
                    WHERE {page_col} BETWEEN ? AND ?
                    ORDER BY {page_col}
                    """,
                    (
                        start_page,
                        end_page,
                    ),
                ).fetchall()

                return [
                    {
                        "page": row["page_no"],
                        "text": row["content"] or "",
                    }
                    for row in rows
                    if row["content"]
                ]

        return []

    finally:
        conn.close()


def _search_summary(subject, query="", limit=8):
    rows = _summary_rows(subject)

    if not rows:
        return []


    query = (query or "").strip()

    if not query:
        selected = rows[:limit]

        return selected


    tokens = [
        token
        for token in re.findall(
            r"[가-힣A-Za-z0-9]{2,}",
            query,
        )
    ]


    if not tokens:
        return rows[:limit]


    scored = []

    for row in rows:

        text = row["text"]

        score = 0

        lowered = text.lower()

        for token in tokens:

            if token.lower() in lowered:
                score += 1

        if score > 0:

            scored.append(
                (
                    score,
                    row,
                )
            )


    scored.sort(
        key=lambda item: (
            -item[0],
            item[1]["page"],
        )
    )


    return [
        row
        for _, row in scored[:limit]
    ]


# ============================================================
# 과목 요약본 검색
# ============================================================

@app.get("/api/concepts/{subject}")
def get_concepts(
    subject: str,
    q: str = "",
    limit: int = 8,
):

    limit = max(
        1,
        min(limit, 20),
    )

    results = _search_summary(
        subject,
        q,
        limit,
    )

    return {
        "success": True,
        "subject": subject,
        "query": q,
        "results": results,
        "page_range": SUMMARY_RANGES.get(subject),
    }


# ============================================================
# 기출문제와 연결된 요약본 검색
# ============================================================

# ============================================================
# 국가고시 문제 ↔ 과목별 요약본 연결
# ============================================================

# ==================== QUESTION_SUMMARY_LINKS_V1 ====================

@app.get("/api/question-related-concepts/{question_id}")
def get_related_concepts(question_id: int):

    conn = get_db()

    try:

        question = conn.execute(
            """
            SELECT
                id,
                subject,
                question_text
            FROM exam_questions
            WHERE id = ?
            """,
            (question_id,),
        ).fetchone()

        if question is None:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "message": "문제를 찾을 수 없습니다.",
                },
            )

        rows = conn.execute(
            """
            SELECT
                qsl.link_rank,
                ssc.chunk_number,
                ssc.text
            FROM question_summary_links AS qsl
            JOIN subject_summary_chunks AS ssc
              ON ssc.id = qsl.summary_chunk_id
            WHERE qsl.question_id = ?
              AND ssc.subject = ?
            ORDER BY qsl.link_rank
            LIMIT 3
            """,
            (
                question_id,
                question["subject"],
            ),
        ).fetchall()

        return {
            "success": True,
            "question_id": question_id,
            "subject": question["subject"],
            "results": [
                {
                    "rank": row["link_rank"],
                    "chunk_number": row["chunk_number"],
                    "text": row["text"] or "",
                }
                for row in rows
            ],
        }

    finally:
        conn.close()

# ============================================================
# 메인 대시보드
# ============================================================

@app.get("/", response_class=HTMLResponse)
def home():
    return r"""
<!DOCTYPE html>
<html lang="ko">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>치위생 국가고시 AI Tutor</title>

    <style>
        * {
            box-sizing: border-box;
        }

        body {
            margin: 0;
            min-height: 100vh;
            color: #18181b;
            font-family:
                -apple-system,
                BlinkMacSystemFont,
                "Segoe UI",
                Arial,
                sans-serif;
            background:
                linear-gradient(
                    135deg,
                    #eef2ff 0%,
                    #f8f5ff 52%,
                    #eef6ff 100%
                );
        }

        button,
        input {
            font: inherit;
        }

        .bg {
            position: fixed;
            inset: 0;
            pointer-events: none;
            overflow: hidden;
            z-index: 0;
        }

        .orb {
            position: absolute;
            border-radius: 50%;
        }

        .orb-1 {
            width: 320px;
            height: 320px;
            left: -140px;
            top: -150px;
            border: 2px solid rgba(130, 120, 180, 0.22);
        }

        .orb-2 {
            width: 220px;
            height: 220px;
            right: 7%;
            top: 90px;
            background: rgba(160, 150, 200, 0.10);
        }

        .orb-3 {
            width: 360px;
            height: 360px;
            right: -170px;
            bottom: -170px;
            background: rgba(255, 255, 255, 0.55);
        }

        .shell {
            position: relative;
            z-index: 1;
            width: min(1080px, calc(100% - 32px));
            margin: 0 auto;
            padding: 52px 0 64px;
        }

        .hero {
            display: flex;
            align-items: flex-end;
            justify-content: space-between;
            gap: 24px;
            margin-bottom: 34px;
        }

        .eyebrow {
            font-size: 12px;
            font-weight: 800;
            color: #7c3aed;
            letter-spacing: 0.06em;
        }

        .hero h1 {
            margin: 8px 0 0;
            font-size: clamp(30px, 5vw, 48px);
            line-height: 1.06;
            letter-spacing: -0.05em;
        }

        .hero p {
            margin: 12px 0 0;
            color: #71717a;
            font-size: 15px;
        }

        .hero-actions {
            display: flex;
            gap: 10px;
            flex-wrap: wrap;
        }

        .hero-button {
            border: none;
            border-radius: 14px;
            padding: 12px 16px;
            background: rgba(255,255,255,0.78);
            box-shadow: 0 10px 30px rgba(80,70,120,0.08);
            cursor: pointer;
            font-weight: 700;
        }

        .hero-button.dark {
            background: #18181b;
            color: white;
        }

        .session {
            margin-top: 30px;
        }

        .session-heading {
            display: flex;
            align-items: baseline;
            justify-content: space-between;
            gap: 12px;
            margin-bottom: 14px;
        }

        .session-heading h2 {
            margin: 0;
            font-size: 21px;
            letter-spacing: -0.04em;
        }

        .session-heading span {
            color: #a1a1aa;
            font-size: 12px;
        }

        .subject-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 14px;
        }

        .subject-card {
            width: 100%;
            border: 1px solid rgba(228, 228, 231, 0.92);
            border-radius: 20px;
            padding: 20px;
            background: rgba(255,255,255,0.84);
            box-shadow: 0 18px 44px rgba(80,70,120,0.07);
            cursor: pointer;
            text-align: left;
            transition:
                transform .15s ease,
                border-color .15s ease,
                box-shadow .15s ease;
        
            outline: 2px solid transparent;
            outline-offset: 2px;
            transition: border-color .18s ease, box-shadow .18s ease, transform .12s ease;
        }

        /* SUBJECT_CARD_STYLE_V1 */
        .subject-card:hover {
            border-color: rgba(30, 41, 59, .34) !important;
            box-shadow: 0 0 0 3px rgba(30, 41, 59, .10);
            transform: translateY(-1px);
        }

        .subject-card:focus,
        .subject-card:focus-visible {
            outline: 2px solid rgba(30, 41, 59, .28);
            outline-offset: 3px;
            border-color: rgba(30, 41, 59, .42) !important;
            box-shadow: 0 0 0 4px rgba(30, 41, 59, .08);
        }

        .subject-card:active {
            transform: translateY(0);
            border-color: rgba(30, 41, 59, .50) !important;
            box-shadow: 0 0 0 3px rgba(30, 41, 59, .13);
        }



        .subject-card:hover {
            transform: translateY(-2px);
            border-color: #c4b5fd;
            box-shadow: 0 22px 50px rgba(80,70,120,0.11);
        }

        .subject-name {
            font-size: 17px;
            font-weight: 800;
            letter-spacing: -0.03em;
        }

        .subject-count {
            margin-top: 8px;
            color: #71717a;
            font-size: 12px;
        }

        .year-row {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
            margin-top: 14px;
        }

        .year-chip {
            padding: 6px 8px;
            border-radius: 9px;
            background: #f4f4f5;
            color: #52525b;
            font-size: 11px;
        }

        .feature-grid {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 14px;
            margin-top: 30px;
        }

        .feature-card {
            padding: 20px;
            border-radius: 20px;
            background: rgba(255,255,255,0.58);
            border: 1px solid rgba(255,255,255,0.7);
        }

        .feature-title {
            font-weight: 800;
        }

        .feature-text {
            margin-top: 7px;
            color: #71717a;
            font-size: 13px;
            line-height: 1.5;
        }

        .loading {
            color: #71717a;
            padding: 30px 0;
        }

        @media (max-width: 820px) {
            .subject-grid,
            .feature-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }

            .hero {
                align-items: flex-start;
                flex-direction: column;
            }
        }

        @media (max-width: 560px) {
            .shell {
                padding-top: 30px;
            }

            .subject-grid,
            .feature-grid {
                grid-template-columns: 1fr;
            }
        }
    </style>
</head>

<body>
<!-- AUTH_TOP_LOGIN_BUTTON_V2 -->
<style>
.auth-top-login-v2 {
    position: fixed;
    top: 18px;
    right: 24px;
    z-index: 99999;
}
.auth-top-login-v2 a {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    min-width: 92px;
    padding: 10px 15px;
    border: 1px solid #e4e4e7;
    border-radius: 12px;
    background: rgba(255,255,255,.96);
    color: #18181b;
    text-decoration: none;
    font-size: 13px;
    font-weight: 800;
    box-shadow: 0 8px 24px rgba(0,0,0,.10);
}
</style>
<div class="auth-top-login-v2">
    <a href="/login">🔐 로그인</a>
</div>


<div class="bg">
    <div class="orb orb-1"></div>
    <div class="orb orb-2"></div>
    <div class="orb orb-3"></div>
</div>

<div class="shell">

    <section class="hero">

        <div>
            <div class="eyebrow">
                DENTAL HYGIENE AI TUTOR
            </div>

            <h1>
                오늘은 어떤 과목을<br>
                공부할까요?
            </h1>

            <p>
                2022~2025 치위생사 국가시험 기출문제를
                과목별로 공부할 수 있습니다.
            </p>
        </div>

        <div class="hero-actions">

            <button
                class="hero-button dark"
                type="button"
                onclick="location.href='/mock-exam'"
            >
                AI 모의고사
            </button>

        </div>

    </section>


    <div id="content">
        <div class="loading">
            과목을 불러오는 중...
        </div>
    </div>


    <section class="feature-grid">

        <div class="feature-card" role="button" tabindex="0"
             onclick="location.href='/study'">
            <div class="feature-title">시험문제</div>
            <div class="feature-text">
                2022~2025 치위생사 국가시험
                기출문제를 과목별로 공부합니다.
            </div>
        </div>

        <div class="feature-card" role="button" tabindex="0"
             onclick="location.href='/expected'">
            <div class="feature-title">예상문제</div>
            <div class="feature-text">
                업로드된 예상문제 문제은행에서
                실제 시험 형식으로 연습합니다.
            </div>
        </div>

        <div class="feature-card" role="button" tabindex="0"
             onclick="location.href='/wrong'">
            <div class="feature-title">오답정리</div>
            <div class="feature-text">
                틀린 문제를 한곳에서 모아
                다시 확인하고 복습합니다.
            </div>
        </div>

    </section>

</div>


<script>

async function loadSubjects() {

    const container =
        document.getElementById("content");

    try {

        const response =
            await fetch("/api/subject-stats");

        if (!response.ok) {
            throw new Error("과목 정보를 불러오지 못했습니다.");
        }

        const data =
            await response.json();

        if (!data.success) {
            throw new Error(
                data.message ||
                "과목 정보를 불러오지 못했습니다."
            );
        }

        renderSubjects(data.subjects);

    } catch (error) {

        container.innerHTML = `
            <div class="loading">
                ${escapeHtml(error.message)}
            </div>
        `;

    }
}


function renderSubjects(subjects) {

    const session1 =
        subjects.filter(item => item.session === 1);

    const session2 =
        subjects.filter(item => item.session === 2);

    document.getElementById("content").innerHTML = `
        ${renderSession(1, session1)}
        ${renderSession(2, session2)}
    `;
}


function renderSession(session, subjects) {

    const sessionName =
        session === 1 ? "1교시" : "2교시";

    const description =
        session === 1
            ? "기초·구강보건 및 의료관계 영역"
            : "임상 치위생 및 임상치학 영역";

    const cards =
        subjects.map(item => {

            const chips =
                Object.entries(item.years)
                    .sort(
                        (a, b) =>
                            Number(a[0]) -
                            Number(b[0])
                    )
                    .map(
                        ([year, count]) =>
                            `<span class="year-chip">
                                ${year} · ${count}
                            </span>`
                    )
                    .join("");

            return `
                <button
                    class="subject-card"
                    type="button"
                    onclick='openSubject(${JSON.stringify(item.subject)})'
                >
                    <div class="subject-name">
                        ${escapeHtml(item.subject)}
                    </div>

                    <div class="subject-count">
                        총 ${item.total}문제 · 2022~2025
                    </div>

                    <div class="year-row">
                        ${chips}
                    </div>
                </button>
            `;
        }).join("");

    return `
        <section class="session">

            <div class="session-heading">
                <h2>${sessionName}</h2>
                <span>${description}</span>
            </div>

            <div class="subject-grid">
                ${cards}
            </div>

        </section>
    `;
}


function openSubject(subject) {

    location.href =
        "/study?subject=" +
        encodeURIComponent(subject);

}


function escapeHtml(value) {

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");
}


loadSubjects();

</script>

</body>
</html>
"""


# ============================================================
# 과목별 학습 페이지
# ============================================================

@app.get("/study", response_class=HTMLResponse)
def study_page():
    return r"""
<!DOCTYPE html>
<html lang="ko">

<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">

    <title>과목 학습</title>

    <style>
        * { box-sizing: border-box; }

        body {
            margin: 0;
            min-height: 100vh;
            color: #18181b;
            font-family:
                -apple-system,
                BlinkMacSystemFont,
                "Segoe UI",
                Arial,
                sans-serif;
            background:
                linear-gradient(
                    135deg,
                    #eef2ff 0%,
                    #f8f5ff 52%,
                    #eef6ff 100%
                );
        }

        button { font: inherit; }

        .shell {
            width: min(920px, calc(100% - 32px));
            margin: 0 auto;
            padding: 26px 0 60px;
        }

        .top {
            display: flex;
            align-items: center;
            gap: 14px;
            margin-bottom: 24px;
        }

        .back {
            width: 42px;
            height: 42px;
            border: none;
            border-radius: 14px;
            background: rgba(255,255,255,.80);
            box-shadow: 0 8px 24px rgba(80,70,120,.08);
            cursor: pointer;
            font-size: 18px;
        }

        .top h1 {
            margin: 0;
            font-size: 27px;
            letter-spacing: -.045em;
        }

        .top p {
            margin: 5px 0 0;
            color: #71717a;
            font-size: 12px;
        }

        .hero-card {
            padding: 26px;
            border-radius: 25px;
            background: rgba(255,255,255,.90);
            box-shadow: 0 22px 60px rgba(80,70,120,.10);
        }

        .hero-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 18px;
        }

        .hero-title {
            font-size: 19px;
            font-weight: 850;
            letter-spacing: -.035em;
        }

        .hero-count {
            color: #71717a;
            font-size: 12px;
        }

        .slots {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 12px;
            margin-top: 22px;
        }

        .slot {
            min-height: 120px;
            border: 1px solid #e4e4e7;
            border-radius: 18px;
            background: #fff;
            padding: 17px;
            text-align: left;
            cursor: pointer;
            transition: .15s ease;
        }

        .slot:hover {
            transform: translateY(-2px);
            border-color: #c4b5fd;
            box-shadow: 0 12px 28px rgba(80,70,120,.09);
        }

        .slot.active {
            border-color: #8b5cf6;
            background: #f5f3ff;
        }

        .slot-icon {
            font-size: 21px;
        }

        .slot-title {
            margin-top: 10px;
            font-weight: 820;
            letter-spacing: -.025em;
        }

        .slot-desc {
            margin-top: 6px;
            color: #71717a;
            font-size: 11px;
            line-height: 1.45;
        }

        .panel {
            display: none;
            margin-top: 16px;
        }

        .panel.active {
            display: block;
        }

        .panel-card {
            padding: 24px;
            border-radius: 23px;
            background: rgba(255,255,255,.92);
            box-shadow: 0 22px 60px rgba(80,70,120,.09);
        }

        .panel-title {
            margin: 0;
            font-size: 19px;
            letter-spacing: -.035em;
        }

        .panel-subtitle {
            margin: 7px 0 0;
            color: #71717a;
            font-size: 12px;
            line-height: 1.5;
        }

        /* 시험문제 */
        .year-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0,1fr));
            gap: 8px;
            margin-top: 20px;
        }

        .year-btn {
            border: 1px solid #e4e4e7;
            border-radius: 13px;
            background: #fff;
            padding: 12px 8px;
            cursor: pointer;
            font-weight: 750;
        }

        .year-btn.active {
            border-color: #8b5cf6;
            background: #f5f3ff;
            color: #7c3aed;
        }

        .exam-actions {
            display: grid;
            grid-template-columns: repeat(3, minmax(0,1fr));
            gap: 9px;
            margin-top: 18px;
        }

        .exam-action {
            border: 1px solid #e4e4e7;
            border-radius: 15px;
            background: #fff;
            padding: 14px;
            text-align: left;
            cursor: pointer;
        }

        .exam-action strong {
            display: block;
            font-size: 14px;
        }

        .exam-action span {
            display: block;
            margin-top: 5px;
            color: #71717a;
            font-size: 11px;
            line-height: 1.4;
        }

        .stats {
            display: flex;
            justify-content: space-between;
            gap: 10px;
            margin-top: 17px;
            color: #71717a;
            font-size: 12px;
        }

        .quiz {
            margin-top: 16px;
            padding: 26px;
            border-radius: 23px;
            background: rgba(255,255,255,.92);
            box-shadow: 0 22px 60px rgba(80,70,120,.09);
        }

        .quiz.hidden {
            display: none;
        }

        .quiz-meta {
            display: flex;
            justify-content: space-between;
            align-items: center;
            gap: 10px;
        }

        .badge {
            padding: 7px 11px;
            border-radius: 999px;
            background: #f3e8ff;
            color: #7c3aed;
            font-size: 12px;
            font-weight: 800;
        }

        .meta-right {
            color: #a1a1aa;
            font-size: 12px;
        }

        .question {
            margin: 18px 0 0;
            font-size: 22px;
            line-height: 1.65;
            letter-spacing: -.035em;
            white-space: pre-wrap;
        }

        .choices {
            display: flex;
            flex-direction: column;
            gap: 10px;
            margin-top: 24px;
        }

        .choice {
            width: 100%;
            display: flex;
            align-items: center;
            gap: 12px;
            border: 1px solid #e4e4e7;
            border-radius: 15px;
            background: #fff;
            padding: 15px;
            text-align: left;
            cursor: pointer;
            transition: .15s ease;
        }

        .choice:hover {
            border-color: #c4b5fd;
        }

        .choice.selected {
            border-color: #8b5cf6;
            background: #f5f3ff;
        }

        .choice-no {
            width: 31px;
            height: 31px;
            flex: 0 0 auto;
            border-radius: 9px;
            background: #f4f4f5;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            font-weight: 800;
        }

        .choice.selected .choice-no {
            background: #8b5cf6;
            color: #fff;
        }

        .choice.correct {
            border-color: #16a34a;
            background: #f0fdf4;
        }

        .choice.correct .choice-no {
            background: #16a34a;
            color: #fff;
        }

        .choice.incorrect {
            border-color: #dc2626;
            background: #fef2f2;
        }

        .choice.incorrect .choice-no {
            background: #dc2626;
            color: #fff;
        }

        .answer-box {
            margin-top: 16px;
            padding: 15px 17px;
            border-radius: 15px;
            background: #f8fafc;
            border: 1px solid #e4e4e7;
            line-height: 1.6;
        }

        .answer-title {
            font-weight: 850;
        }

        .answer-detail {
            margin-top: 5px;
            color: #52525b;
            font-size: 13px;
        }

        .next {
            width: 100%;
            height: 56px;
            margin-top: 22px;
            border: none;
            border-radius: 15px;
            background: #18181b;
            color: #fff;
            font-weight: 800;
            cursor: pointer;
        }

        .next:disabled {
            opacity: .42;
            cursor: not-allowed;
        }

        .status {
            min-height: 20px;
            margin-top: 9px;
            text-align: center;
            color: #71717a;
            font-size: 12px;
        }

        .placeholder {
            padding: 30px 0 10px;
            text-align: center;
        }

        .placeholder-icon {
            font-size: 30px;
        }

        .placeholder-title {
            margin-top: 11px;
            font-weight: 800;
        }

        .placeholder-text {
            margin: 7px auto 0;
            max-width: 520px;
            color: #71717a;
            font-size: 12px;
            line-height: 1.6;
        }

        @media (max-width: 760px) {
            .slots {
                grid-template-columns: repeat(2, minmax(0,1fr));
            }

            .exam-actions {
                grid-template-columns: 1fr;
            }
        }

        @media (max-width: 520px) {
            .shell {
                padding-top: 18px;
            }

            .hero-card,
            .panel-card,
            .quiz {
                padding: 19px;
            }

            .year-grid {
                grid-template-columns: repeat(2, minmax(0,1fr));
            }

            .question {
                font-size: 19px;
            }
        }
    </style>
</head>

<body>
<!-- AUTH_TOP_LOGIN_BUTTON_V2 -->
<style>
.auth-top-login-v2 {
    position: fixed;
    top: 18px;
    right: 24px;
    z-index: 99999;
}
.auth-top-login-v2 a {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    gap: 6px;
    min-width: 92px;
    padding: 10px 15px;
    border: 1px solid #e4e4e7;
    border-radius: 12px;
    background: rgba(255,255,255,.96);
    color: #18181b;
    text-decoration: none;
    font-size: 13px;
    font-weight: 800;
    box-shadow: 0 8px 24px rgba(0,0,0,.10);
}
</style>
<div class="auth-top-login-v2">
    <a href="/login">🔐 로그인</a>
</div>


<div class="shell">

    <div class="top">

        <button
            class="back"
            type="button"
            onclick="location.href='/'"
        >
            ←
        </button>

        <div>
            <h1 id="title">과목 학습</h1>
            <p id="subtitle">
                이 과목을 원하는 방식으로 공부하세요.
            </p>
        </div>

    </div>


    <section class="hero-card">

        <div class="hero-row">

            <div>
                <div class="hero-title">
                    <span id="subjectName">과목</span>
                </div>

                <div
                    class="hero-count"
                    id="heroCount"
                >
                    시험문제 수를 확인하는 중...
                </div>
            </div>

        </div>


        <div class="slots">
<button
                class="slot"
                type="button"
                onclick="openPanel('exam', this)"
            >
                <div class="slot-icon">📝</div>
                <div class="slot-title">시험문제</div>
                <div class="slot-desc">
                    국가고시를 포함한 시험문제를 과목별로 풉니다.
                </div>
            </button>


            <button
                class="slot"
                type="button"
                onclick="location.href='/expected?subject=' + encodeURIComponent(subject)"
            >
                <div class="slot-icon">📚</div>
                <div class="slot-title">예상문제</div>
                <div class="slot-desc">
                    내가 업로드한 해당 과목 예상문제를 랜덤으로 풉니다.
                </div>
            </button>


            <button
                class="slot"
                type="button"
                onclick="openPanel('wrong', this)"
            >
                <div class="slot-icon">❌</div>
                <div class="slot-title">오답정리</div>
                <div class="slot-desc">
                    틀린 문제와 취약 개념을 모아봅니다.
                </div>
            </button>

        </div>

    </section>



    <!-- 시험문제 -->
    <section
        class="panel"
        id="panel-exam"
    >

        <div class="panel-card">

            <h2 class="panel-title">
                시험문제
            </h2>

            <p class="panel-subtitle">
                현재 저장된 2022~2025 국가고시를 과목별로 풉니다.
                국가고시 기출문제만 제공합니다.
            </p>


            <div class="year-grid">

                <button
                    class="year-btn active"
                    data-year="all"
                >
                    전체
                </button>

                <button
                    class="year-btn"
                    data-year="2025"
                >
                    2025
                </button>

                <button
                    class="year-btn"
                    data-year="2024"
                >
                    2024
                </button>

                <button
                    class="year-btn"
                    data-year="2023"
                >
                    2023
                </button>

                <button
                    class="year-btn"
                    data-year="2022"
                >
                    2022
                </button>

            </div>


            <div class="exam-actions">

                <button
                    class="exam-action"
                    type="button"
                    onclick="startExam()"
                >
                    <strong>시험문제 시작</strong>
                    <span>
                        선택한 범위에서 문제를 바로 풉니다.
                    </span>
                </button>


                <button
                    class="exam-action"
                    type="button"
                    onclick="startExam()"
                >
                    <strong>랜덤 문제</strong>
                    <span>
                        선택한 연도 범위에서 무작위로 출제합니다.
                    </span>
                </button>


                <button
                    class="exam-action"
                    type="button"
                    onclick="startExam(true)"
                >
                    <strong>20문제 집중</strong>
                    <span>
                        최대 20문제를 연속해서 풉니다.
                    </span>
                </button>

            </div>


            <div class="stats" id="stats">
                <span>문제 수 확인 중...</span>
                <span></span>
            </div>


        </div>


        <section
            class="quiz hidden"
            id="quiz"
        >

            <div class="placeholder">
                문제를 불러오는 중...
            </div>

        </section>

    </section>



    <!-- 오답정리 -->
    <section
        class="panel"
        id="panel-wrong"
    >

        <div class="panel-card">

            <h2 class="panel-title">
                오답정리
            </h2>

            <p class="panel-subtitle">
                앞으로 저장한 오답과 관련 개념을 과목별로 모아볼 수 있는 영역입니다.
            </p>

            <div class="placeholder">

                <div class="placeholder-icon">
                    ❌
                </div>

                <div class="placeholder-title">
                    오답정리 준비 중
                </div>

                <div class="placeholder-text">
                    로그인과 문제 풀이 기록을 연결하면
                    틀린 문제, 정답률, 취약 개념을 자동으로 모을 수 있습니다.
                </div>

            </div>

        </div>

    </section>

</div>


<script>

const params =
    new URLSearchParams(location.search);

const subject =
    params.get("subject") || "";

document.getElementById("title").textContent =
    subject || "과목 학습";

document.getElementById("subjectName").textContent =
    subject || "과목";

document.getElementById("subtitle").textContent =
    subject
        ? "이 과목을 원하는 방식으로 공부하세요."
        : "기출문제를 원하는 범위로 풀어보세요.";


let selectedYear = "all";

let currentQuestion = null;
let selectedChoice = null;
let currentNumber = 1;
let correctCount = 0;
let solvedCount = 0;
let maxQuestions = 0;


function openPanel(panelName, button) {

    document
        .querySelectorAll(".slot")
        .forEach(item => {
            item.classList.remove("active");
        });

    button.classList.add("active");


    document
        .querySelectorAll(".panel")
        .forEach(panel => {
            panel.classList.remove("active");
        });


    document
        .getElementById(
            "panel-" + panelName
        )
        .classList.add("active");

}


document
    .querySelectorAll(".year-btn")
    .forEach(button => {

        button.addEventListener(
            "click",
            () => {

                document
                    .querySelectorAll(".year-btn")
                    .forEach(item => {
                        item.classList.remove("active");
                    });

                button.classList.add("active");

                selectedYear =
                    button.dataset.year;

                loadStats();

            }
        );

    });


async function searchConcepts() {

    const query =
        document.getElementById("conceptQuery").value;

    const box =
        document.getElementById("conceptResults");

    box.innerHTML = `
        <div class="loading">
            요약본을 검색하는 중...
        </div>
    `;

    try {

        const response =
            await fetch(
                "/api/concepts/" +
                encodeURIComponent(subject) +
                "?q=" +
                encodeURIComponent(query) +
                "&limit=8"
            );

        const data =
            await response.json();

        if (!data.success) {
            throw new Error(
                data.message ||
                "개념을 불러오지 못했습니다."
            );
        }

        if (!data.results.length) {

            box.innerHTML = `
                <div class="placeholder">
                    <div class="placeholder-title">
                        관련 개념을 찾지 못했습니다.
                    </div>
                    <div class="placeholder-text">
                        다른 키워드로 검색해보세요.
                    </div>
                </div>
            `;

            return;
        }

        box.innerHTML =
            data.results.map(item => `
                <article
                    style="
                        padding:16px;
                        margin-bottom:9px;
                        border:1px solid #e4e4e7;
                        border-radius:15px;
                        background:#fff;
                    "
                >
                    <div
                        style="
                            font-size:11px;
                            color:#7c3aed;
                            font-weight:800;
                            margin-bottom:7px;
                        "
                    >
                        요약본 p.${item.page}
                    </div>

                    <div
                        style="
                            font-size:13px;
                            line-height:1.65;
                            white-space:pre-wrap;
                        "
                    >
                        ${escapeHtml(item.text)}
                    </div>
                </article>
            `)
            .join("");

    } catch (error) {

        box.innerHTML = `
            <div class="placeholder">
                <div class="placeholder-title">
                    검색 오류
                </div>
                <div class="placeholder-text">
                    ${escapeHtml(error.message)}
                </div>
            </div>
        `;

    }
}


async function loadStats() {

    if (!subject) {
        return;
    }

    try {

        const response =
            await fetch(
                "/api/subject-stats/" +
                encodeURIComponent(subject)
            );

        const data =
            await response.json();

        if (!data.success) {
            throw new Error(
                data.message ||
                "과목 정보를 불러오지 못했습니다."
            );
        }


        let count = data.total;


        if (selectedYear !== "all") {

            count =
                data.years[selectedYear] || 0;

        }


        document.getElementById("heroCount")
            .textContent =
                `시험문제 ${data.total}문제 · 2022~2025`;


        document.getElementById("stats")
            .innerHTML = `
                <span>
                    선택 범위:
                    ${selectedYear === "all"
                        ? "전체"
                        : selectedYear}
                </span>

                <span>
                    ${count}문제
                </span>
            `;


    } catch (error) {

        document.getElementById("stats")
            .innerHTML = `
                <span>
                    ${escapeHtml(error.message)}
                </span>
                <span></span>
            `;

    }
}


function startExam(focus20 = false) {

    currentNumber = 1;
    correctCount = 0;
    solvedCount = 0;

    maxQuestions =
        focus20 ? 20 : 0;

    loadQuestion();

}


async function loadQuestion() {

    const quiz =
        document.getElementById("quiz");

    quiz.classList.remove("hidden");


    quiz.innerHTML = `
        <div class="loading">
            문제를 불러오는 중...
        </div>
    `;


    selectedChoice = null;


    try {

        let url = "/api/exam-random-question";
        const query = new URLSearchParams();

        if (subject) {
            query.set("subject", subject);
        }

        if (selectedYear !== "all") {
            query.set("year", selectedYear);
        }

        const queryString = query.toString();
        if (queryString) {
            url += "?" + queryString;
        }


        const response =
            await fetch(url);


        if (!response.ok) {

            throw new Error(
                "선택한 범위에서 문제를 찾을 수 없습니다."
            );

        }


        const data =
            await response.json();


        if (!data.success) {

            throw new Error(
                data.message ||
                "문제를 찾지 못했습니다."
            );

        }


        currentQuestion =
            data.question;


        renderQuestion();


    } catch (error) {

        quiz.innerHTML = `
            <div class="placeholder">
                <div class="placeholder-title">
                    문제를 불러오지 못했습니다.
                </div>
                <div class="placeholder-text">
                    ${escapeHtml(error.message)}
                </div>
            </div>
        `;

    }

}


function renderQuestion() {

    const q =
        currentQuestion;


    document.getElementById("quiz")
        .innerHTML = `

        <div class="quiz-meta">

            <span class="badge">
                ${escapeHtml(q.subject)}
            </span>

            <span class="meta-right">
                ${q.exam_year}년
                · ${q.session}교시
                · ${q.question_number}번
            </span>

        </div>


        <h2 class="question">${escapeHtml(cleanQuestionText(q.question_text))}</h2>


        <div class="choices">

            ${q.choices.map(choice => `

                <button
                    class="choice"
                    type="button"
                    data-number="${choice.number}"
                    onclick="selectChoice(${choice.number})"
                >

                    <span class="choice-no">
                        ${choice.number}
                    </span>

                    <span>
                        ${escapeHtml(choice.text)}
                    </span>

                </button>

            `).join("")}

        </div>


        <button
            class="next"
            id="nextButton"
            type="button"
            onclick="nextQuestion()"
            disabled
        >
            다음 문제 →
        </button>

        <button
            type="button"
            onclick="showRelatedConcepts()"
            style="
                width:100%;
                height:46px;
                margin-top:9px;
                border:1px solid #e4e4e7;
                border-radius:14px;
                background:#fff;
                cursor:pointer;
                font-size:13px;
                font-weight:750;
            "
        >
            📚 기출문제 해설
        </button>

        <div
            class="status"
            id="status"
        >
            답을 선택해주세요.
        </div>

        <div
            id="relatedConcepts"
            style="margin-top:12px;"
        ></div>

    `;

    document.getElementById("subtitle").textContent =
        `${q.exam_year}년 ${q.session}교시 · ${currentNumber}번째 문제`;

}



function cleanQuestionText(value) {
    return String(value || "")
        .replace(/\u00A0/g, " ")
        .replace(/\r\n/g, "\n")
        .split("\n")
        .map(line => line.replace(/^[\s\u00A0]+/, "").replace(/[\s\u00A0]+$/, ""))
        .filter(line => line.length > 0)
        .join("\n")
        .trim();
}

async function showRelatedConcepts() {
    const box = document.getElementById("relatedConcepts");

    if (!box || !currentQuestion) {
        return;
    }

    box.innerHTML = `
        <div style="padding:14px 15px 10px;font-size:15px;font-weight:900;color:#18181b;">📚 기출문제 해설</div>
        <div style="margin:0 15px 14px;padding:15px;border:1px solid #e4e4e7;border-radius:13px;background:#fff;font-size:14px;line-height:1.8;color:#27272a;">
            해설을 불러오는 중...
        </div>
    `;

    try {
        const response = await fetch("/api/ai-explanation/" + currentQuestion.id);
        const data = await response.json();

        if (!data.success) {
            throw new Error("explanation_load_failed");
        }

        const cleanText = (value) => {
            return String(value || "")
                .replace(/\u00A0/g, " ")
                .replace(/\r\n/g, "\n")
                .split("\n")
                .map(line => line.replace(/^[\s\u00A0]+/, "").replace(/[\s\u00A0]+$/, ""))
                .filter(line => line.length > 0)
                .join("\n")
                .trim();
        };

        const answerReason = cleanText(data.answer_reason);
        const choiceExplanations = data.choice_explanations || {};
        const memoryPoint = cleanText(data.memory_point);

        const choiceHtml = Object.keys(choiceExplanations)
            .sort((a, b) => Number(a) - Number(b))
            .map(number => {
                const text = cleanText(choiceExplanations[number]);
                const isCorrect = Number(number) === Number(data.answer);

                return `
                    <div style="
                        display:flex;
                        align-items:flex-start;
                        gap:12px;
                        margin-bottom:9px;
                        padding:13px 14px;
                        border:1px solid ${isCorrect ? "#f1b7c4" : "#d9e1ea"};
                        border-radius:11px;
                        background:#fff;
                        font-size:14px;
                        line-height:1.8;
                        color:#27272a;
                        box-sizing:border-box;
                    ">
                        <span style="
                            flex:0 0 30px;
                            width:30px;
                            height:30px;
                            display:flex;
                            align-items:center;
                            justify-content:center;
                            border-radius:50%;
                            background:${isCorrect ? "#d92d5b" : "#eef2f7"};
                            color:${isCorrect ? "#fff" : "#18181b"};
                            font-size:14px;
                            font-weight:900;
                        ">${number}</span>
                        <div style="flex:1;min-width:0;margin:0;padding:0;white-space:normal;overflow-wrap:anywhere;">${escapeHtml(text)}</div>
                    </div>
                `;
            })
            .join("");

        const memoryHtml = memoryPoint
            .split("\n")
            .map(line => line.trim())
            .filter(Boolean)
            .map(line => `
                <div style="display:flex;align-items:flex-start;gap:9px;margin:0 0 7px;padding:0;font-size:14px;line-height:1.8;">
                    <span style="font-weight:900;">•</span>
                    <span style="flex:1;">${escapeHtml(line)}</span>
                </div>
            `)
            .join("");

        box.innerHTML = `
            <div style="padding:14px 15px 10px;font-size:15px;font-weight:900;color:#18181b;">
                📚 기출문제 해설
            </div>

            <div style="margin:0 15px 10px;padding:15px;border:1px solid #f0c5ce;border-radius:13px;background:#fff7f8;box-sizing:border-box;">
                <div style="margin-bottom:9px;font-size:15px;font-weight:900;color:#18181b;">
                    📌 정답 이유
                </div>
                <div style="margin:0;padding:0;font-size:14px;line-height:1.8;white-space:normal;overflow-wrap:anywhere;color:#27272a;">
                    ${escapeHtml(answerReason).replace(/\n/g, "<br>")}
                </div>
            </div>

            <div style="margin:0 15px 10px;padding:15px;border:1px solid #c9ddf5;border-radius:13px;background:#f7fbff;box-sizing:border-box;">
                <div style="margin-bottom:10px;font-size:15px;font-weight:900;color:#18181b;">
                    🔎 선택지별 해설
                </div>
                ${choiceHtml}
            </div>

            <div style="margin:0 15px 14px;padding:15px;border:1px solid #c8ead5;border-radius:13px;background:#f7fff9;box-sizing:border-box;">
                <div style="margin-bottom:9px;font-size:15px;font-weight:900;color:#18181b;">
                    🧠 암기 포인트
                </div>
                <div style="margin:0;padding:0;color:#27272a;">
                    ${memoryHtml}
                </div>
            </div>
        `;
    } catch (error) {
        box.innerHTML = `
            <div style="margin:0 15px 14px;padding:14px;border-radius:13px;background:#fafafa;color:#71717a;font-size:14px;line-height:1.8;">
                해설을 불러오지 못했습니다.
            </div>
        `;
    }
}
function selectChoiceInternal(number) {

    if (
        currentQuestion === null ||
        selectedChoice !== null
    ) {
        return;
    }

    selectedChoice = number;

    const correctAnswer =
        Number(currentQuestion.answer);

    document
        .querySelectorAll(".choice")
        .forEach(button => {

            const choiceNumber =
                Number(button.dataset.number);

            button.classList.remove(
                "selected",
                "correct",
                "incorrect"
            );

            if (choiceNumber === correctAnswer) {
                button.classList.add("correct");
            }

            if (choiceNumber === number) {
                button.classList.add("selected");

                if (choiceNumber !== correctAnswer) {
                    button.classList.add("incorrect");
                }
            }

            button.disabled = true;
        });

    const isCorrect =
        Number(number) === correctAnswer;

    document.getElementById("nextButton")
        .disabled = false;

    document.getElementById("status")
        .textContent = isCorrect
            ? "정답입니다!"
            : "오답입니다. 정답을 확인하세요.";

    document.getElementById("status").style.color =
        isCorrect ? "#15803d" : "#b91c1c";

    const answerBox = document.createElement("div");
    answerBox.className = "answer-box";
    answerBox.innerHTML = `
        <div class="answer-title">
            ${isCorrect ? "✅ 정답" : "❌ 오답"}
        </div>
        <div class="answer-detail">
            정답은 <strong>${correctAnswer}번</strong>입니다.
        </div>
    `;

    const quiz = document.getElementById("quiz");
    const nextButton = document.getElementById("nextButton");

    quiz.insertBefore(answerBox, nextButton);

    document.getElementById("nextButton")
        .textContent = "다음 문제 →";


}


function nextQuestion() {

    if (
        currentQuestion === null ||
        selectedChoice === null
    ) {
        return;
    }


    solvedCount += 1;


    if (
        currentQuestion.answer ===
        selectedChoice
    ) {
        correctCount += 1;
    }


    if (
        maxQuestions > 0 &&
        solvedCount >= maxQuestions
    ) {

        document.getElementById("quiz")
            .innerHTML = `

            <div class="placeholder">

                <div class="placeholder-icon">
                    🎉
                </div>

                <div class="placeholder-title">
                    20문제 풀이 완료
                </div>

                <div class="placeholder-text">
                    ${solvedCount}문제 중
                    ${correctCount}문제를 맞혔습니다.
                    정답률:
                    ${
                        Math.round(
                            (
                                correctCount /
                                solvedCount
                            ) * 100
                        )
                    }%
                </div>

            </div>

        `;

        document.getElementById("scoreText");

        return;
    }


    currentNumber += 1;

    loadQuestion();

}


function escapeHtml(value) {

    return String(value)
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");

}


loadStats();




/* ATTEMPT_UI_SYNC_V3 */
async function saveCurrentQuestionAttempt() {
    try {
        const token = localStorage.getItem("firebase_id_token");

        if (!token || !currentQuestion || !currentQuestion.id || !selectedChoice) {
            return null;
        }

        const key = "attempt_saved_" + currentQuestion.id;

        if (sessionStorage.getItem(key) === "1") {
            return null;
        }

        sessionStorage.setItem(key, "1");

        const response = await fetch("/api/attempt", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "Authorization": "Bearer " + token
            },
            body: JSON.stringify({
                question_id: Number(currentQuestion.id),
                selected_choice: Number(selectedChoice)
            })
        });

        const result = await response.json();

        if (!response.ok || !result.success) {
            sessionStorage.removeItem(key);
            console.warn("문제풀이 기록 저장 실패:", result);
            return null;
        }

        return result;
    } catch (error) {
        console.warn("문제풀이 기록 저장 오류:", error);
        return null;
    }
}


async function selectChoice(choiceNumber) {
    const result = selectChoiceInternal(choiceNumber);

    try {
        if (result && typeof result.then === "function") {
            await result;
        }
    } catch (error) {
        console.warn("기존 문제 선택 처리 오류:", error);
    }

    await saveCurrentQuestionAttempt();
}

</script>

</body>
</html>
"""


# ============================================================
# 특정 문제 조회 API
# ============================================================

@app.get("/api/exam-question/{year}/{session}/{question_number}")
def get_exam_question(
    year: int,
    session: int,
    question_number: int,
):
    conn = get_db()

    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                id,
                exam_year,
                session,
                question_number,
                subject,
                question_text,
                answer
            FROM exam_questions
            WHERE exam_year = ?
              AND session = ?
              AND question_number = ?
            """,
            (
                year,
                session,
                question_number,
            ),
        )

        question = cursor.fetchone()

        if question is None:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "message": "문제를 찾을 수 없습니다.",
                },
            )

        cursor.execute(
            """
            SELECT
                choice_number,
                choice_text
            FROM question_choices
            WHERE question_id = ?
            ORDER BY choice_number
            """,
            (question["id"],),
        )

        choices = cursor.fetchall()

        return {
            "success": True,
            "question": {
                "id": question["id"],
                "exam_year": question["exam_year"],
                "session": question["session"],
                "question_number": question["question_number"],
                "subject": question["subject"],
                "question_text": question["question_text"],
                "answer": question["answer"],
                "choices": [
                    {
                        "number": choice["choice_number"],
                        "text": choice["choice_text"],
                    }
                    for choice in choices
                ],
            },
        }

    finally:
        conn.close()


# ============================================================
# 랜덤 문제 API
# ============================================================

@app.get("/api/exam-random-question")
def get_random_exam_question(
    year: int | None = None,
    session: int | None = None,
    subject: str | None = None,
):
    conn = get_db()

    try:
        cursor = conn.cursor()

        query = """
            SELECT
                id,
                exam_year,
                session,
                question_number,
                subject,
                question_text,
                answer
            FROM exam_questions
            WHERE question_text IS NOT NULL
              AND length(trim(question_text)) > 0
        """

        params = []

        if year is not None:
            query += " AND exam_year = ?"
            params.append(year)

        if session is not None:
            query += " AND session = ?"
            params.append(session)

        if subject is not None:
            query += " AND subject = ?"
            params.append(subject)

        query += " ORDER BY RANDOM() LIMIT 1"

        cursor.execute(query, params)
        question = cursor.fetchone()

        if question is None:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "message": "조건에 맞는 문제를 찾을 수 없습니다.",
                },
            )

        cursor.execute(
            """
            SELECT
                choice_number,
                choice_text
            FROM question_choices
            WHERE question_id = ?
            ORDER BY choice_number
            """,
            (question["id"],),
        )

        choices = cursor.fetchall()

        return {
            "success": True,
            "question": {
                "id": question["id"],
                "exam_year": question["exam_year"],
                "session": question["session"],
                "question_number": question["question_number"],
                "subject": question["subject"],
                "question_text": question["question_text"],
                "answer": question["answer"],
                "choices": [
                    {
                        "number": choice["choice_number"],
                        "text": choice["choice_text"],
                    }
                    for choice in choices
                ],
            },
        }

    finally:
        conn.close()


# ============================================================
# 전체 과목 API
# ============================================================

@app.get("/api/subjects")
def get_subjects():

    conn = get_db()

    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT DISTINCT subject
            FROM exam_questions
            WHERE subject IS NOT NULL
            ORDER BY subject
            """
        )

        subjects = [
            row["subject"]
            for row in cursor.fetchall()
        ]

        return {
            "success": True,
            "subjects": subjects,
        }

    finally:
        conn.close()


# ============================================================
# 과목별 통계 API
# ============================================================

@app.get("/api/subject-stats")
def get_subject_stats():

    conn = get_db()

    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                session,
                subject,
                exam_year,
                COUNT(*) AS count
            FROM exam_questions
            WHERE subject IS NOT NULL
            GROUP BY
                session,
                subject,
                exam_year
            ORDER BY
                session,
                subject,
                exam_year
            """
        )

        rows = cursor.fetchall()

        result = {}

        for row in rows:

            subject = row["subject"]

            if subject not in result:

                result[subject] = {
                    "session": row["session"],
                    "subject": subject,
                    "years": {},
                    "total": 0,
                }

            count = int(row["count"])

            result[subject]["years"][
                str(row["exam_year"])
            ] = count

            result[subject]["total"] += count

        ordered = []

        for session in [1, 2]:

            for subject in SESSION_SUBJECTS[session]:

                if subject in result:

                    ordered.append(
                        result[subject]
                    )

        return {
            "success": True,
            "subjects": ordered,
        }

    finally:
        conn.close()


# ============================================================
# 특정 과목 통계 API
# ============================================================

@app.get("/api/subject-stats/{subject}")
def get_single_subject_stats(subject: str):

    conn = get_db()

    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                exam_year,
                COUNT(*) AS count
            FROM exam_questions
            WHERE subject = ?
            GROUP BY exam_year
            ORDER BY exam_year
            """,
            (subject,),
        )

        rows = cursor.fetchall()

        years = {}

        for row in rows:
            years[
                str(row["exam_year"])
            ] = int(row["count"])

        total = sum(years.values())

        if total == 0:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "message": "해당 과목을 찾을 수 없습니다.",
                },
            )

        return {
            "success": True,
            "subject": subject,
            "years": years,
            "total": total,
        }

    finally:
        conn.close()


# ============================================================
# PDF 업로드
# ============================================================

@app.post("/upload-pdf", response_class=HTMLResponse)
async def upload_pdf(file: UploadFile = File(...)):

    filename = file.filename or ""

    if not filename.lower().endswith(".pdf"):
        return HTMLResponse(
            """
            <h2>PDF 파일만 업로드할 수 있습니다.</h2>
            <a href="/">← 홈으로 돌아가기</a>
            """,
            status_code=400,
        )

    safe_name = Path(filename).name
    file_path = UPLOAD_DIR / safe_name

    content = await file.read()

    with open(file_path, "wb") as f:
        f.write(content)

    doc = pymupdf.open(file_path)

    pages = []

    for index, pdf_page in enumerate(doc):

        text = pdf_page.get_text("text").strip()

        pages.append(
            {
                "source_file": safe_name,
                "page": index + 1,
                "text": text,
            }
        )

    doc.close()

    json_path = (
        DATA_DIR /
        f"{file_path.stem}.json"
    )

    with open(
        json_path,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            {
                "source_file": safe_name,
                "total_pages": len(pages),
                "pages": pages,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    chunk_size = 1200
    overlap = 200

    chunks = []

    for page in pages:

        text = page["text"]

        if not text:
            continue

        start = 0
        chunk_number = 1

        while start < len(text):

            end = min(
                start + chunk_size,
                len(text),
            )

            chunks.append(
                {
                    "chunk_id": (
                        f"{file_path.stem}"
                        f"_p{page['page']:03d}"
                        f"_c{chunk_number:03d}"
                    ),
                    "source_file": safe_name,
                    "page": page["page"],
                    "chunk_number": chunk_number,
                    "text": text[start:end],
                }
            )

            if end >= len(text):
                break

            start = end - overlap
            chunk_number += 1

    chunk_path = (
        CHUNK_DIR /
        f"{file_path.stem}_chunks.json"
    )

    with open(
        chunk_path,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            chunks,
            f,
            ensure_ascii=False,
            indent=2,
        )

    return f"""
<!DOCTYPE html>
<html lang="ko">

<head>
    <meta charset="UTF-8">
    <title>PDF 분석 결과</title>
</head>

<body style="
    font-family:Arial,sans-serif;
    max-width:900px;
    margin:40px auto;
    padding:20px;
">

    <h1>PDF 분석 완료</h1>

    <p>
        파일:
        <strong>{escape_text(safe_name)}</strong>
    </p>

    <p>
        전체 페이지:
        <strong>{len(pages)}</strong>
    </p>

    <p>
        생성된 청크:
        <strong>{len(chunks)}</strong>
    </p>

    <p>
        페이지 JSON:
        {escape_text(json_path.name)}
    </p>

    <p>
        청크 JSON:
        {escape_text(chunk_path.name)}
    </p>

    <p>
        <a href="/">← 홈으로 돌아가기</a>
    </p>

</body>
</html>
"""


# ============================================================
# Firebase 로그인
# FIREBASE_LOGIN_PATCH_V1
# ============================================================

CONFIG_FILE = CONFIG

@app.get("/firebase-config.js")
def firebase_config_js():
    if not CONFIG_FILE.exists():
        return HTMLResponse(
            "window.__FIREBASE_CONFIG__ = null;",
            media_type="application/javascript",
            status_code=404,
        )

    raw = CONFIG_FILE.read_text(encoding="utf-8")
    if "// Initialize Firebase" in raw:
        raw = raw.split("// Initialize Firebase", 1)[0].strip()

    raw = raw.replace(
        "const firebaseConfig",
        "window.__FIREBASE_CONFIG__",
        1,
    )

    return HTMLResponse(
        raw + "\n",
        media_type="application/javascript",
    )


@app.get("/login", response_class=HTMLResponse)
def login_page():
    login_file = BASE_DIR / "login.html"
    if not login_file.exists():
        return HTMLResponse("login.html이 없습니다.", status_code=500)
    return HTMLResponse(
        login_file.read_text(encoding="utf-8"),
        media_type="text/html",
    )


# ============================================================
# 서버 상태
# ============================================================

@app.get("/api/health")
def health():
    return {
        "success": True,
        "database_exists": DB_PATH.exists(),
    }

@app.get("/api/ai-explanation/{question_id}")
def api_ai_explanation(question_id: int):
    """
    기출문제 FINAL 해설 조회.
    Gemini를 호출하지 않고 exam_explanations_final에서
    정답 이유 / 선택지별 해설 / 암기 포인트를 반환한다.
    """

    conn = get_db()

    try:
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT
                f.exam_year,
                f.session,
                f.question_number,
                f.answer,
                f.answer_choice,
                f.answer_reason,
                f.choice_explanations_json,
                f.memory_point,
                f.confidence,
                f.review_status
            FROM exam_questions q
            JOIN exam_explanations_final f
                ON f.exam_year = q.exam_year
                AND f.session = q.session
                AND f.question_number = q.question_number
            WHERE q.id = ?
            """,
            (question_id,),
        )

        row = cursor.fetchone()

        if row is None:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "message": "해당 문제의 FINAL 해설을 찾을 수 없습니다.",
                },
            )

        choice_explanations = json.loads(
            row["choice_explanations_json"] or "{}"
        )

        return {
            "success": True,
            "mode": "final_written",
            "exam_year": row["exam_year"],
            "session": row["session"],
            "question_number": row["question_number"],
            "answer": row["answer"],
            "answer_choice": row["answer_choice"],
            "answer_reason": row["answer_reason"] or "",
            "choice_explanations": choice_explanations,
            "memory_point": row["memory_point"] or "",
            "confidence": row["confidence"] or "",
            "review_status": row["review_status"] or "",
        }

    finally:
        conn.close()


# ============================================================
# Firebase Admin 인증
# FIREBASE_ADMIN_AUTH_V1
# 브라우저가 전달한 Firebase ID Token을 서버에서 검증하고
# 현재 로그인 사용자의 기본 정보를 반환한다.
# ============================================================

from fastapi import Request

_FIREBASE_ADMIN_READY = False
_FIREBASE_ADMIN_ERROR = None

try:
    import firebase_admin
    from firebase_admin import credentials, auth as firebase_auth

    _firebase_key_file = BASE_DIR / "serviceAccountKey.json"

    if _firebase_key_file.exists():
        try:
            firebase_admin.get_app()
        except ValueError:
            firebase_admin.initialize_app(
                credentials.Certificate(str(_firebase_key_file))
            )
        _FIREBASE_ADMIN_READY = True
    else:
        _FIREBASE_ADMIN_ERROR = "serviceAccountKey.json이 없습니다."
except Exception as _e:
    _FIREBASE_ADMIN_ERROR = str(_e)


def _verify_firebase_request(request: Request):
    if not _FIREBASE_ADMIN_READY:
        raise HTTPException(
            status_code=503,
            detail="Firebase Admin 인증이 준비되지 않았습니다.",
        )

    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="로그인 토큰이 없습니다.",
        )

    token = authorization[7:].strip()
    if not token:
        raise HTTPException(
            status_code=401,
            detail="로그인 토큰이 없습니다.",
        )

    try:
        return firebase_auth.verify_id_token(token)
    except Exception:
        raise HTTPException(
            status_code=401,
            detail="유효하지 않거나 만료된 로그인 토큰입니다.",
        )


# ============================================================
# 사용자 DB 동기화
# USER_SYNC_V1
# ============================================================

def _sync_firebase_user(decoded):
    user_db = sqlite3.connect(DB_PATH)
    user_db.execute(
        """
        INSERT INTO users (
            firebase_uid, email, display_name, email_verified,
            created_at, last_login_at
        )
        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        ON CONFLICT(firebase_uid) DO UPDATE SET
            email = excluded.email,
            display_name = excluded.display_name,
            email_verified = excluded.email_verified,
            last_login_at = CURRENT_TIMESTAMP
        """,
        (
            decoded.get("uid"),
            decoded.get("email"),
            decoded.get("name") or decoded.get("email"),
            1 if decoded.get("email_verified", False) else 0,
        ),
    )
    user_db.commit()
    user_db.close()


@app.get("/api/me")
async def api_me(request: Request):
    decoded = _verify_firebase_request(request)
    _sync_firebase_user(decoded)

    return {
        "authenticated": True,
        "uid": decoded.get("uid"),
        "email": decoded.get("email"),
        "name": decoded.get("name") or decoded.get("email"),
        "email_verified": bool(decoded.get("email_verified", False)),
    }

# ============================================================
# 문제풀이 기록 API
# ATTEMPT_API_V3
# ============================================================

@app.post("/api/attempt")
async def api_record_attempt(request: Request):
    decoded = _verify_firebase_request(request)

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="잘못된 JSON 요청입니다.")

    try:
        question_id = int(payload.get("question_id"))
        selected_choice = int(payload.get("selected_choice"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="question_id와 selected_choice가 필요합니다."
        )

    if selected_choice < 1 or selected_choice > 5:
        raise HTTPException(
            status_code=400,
            detail="선택지는 1~5번이어야 합니다."
        )

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        "SELECT id FROM users WHERE firebase_uid = ?",
        (decoded.get("uid"),)
    )
    user_row = cur.fetchone()

    if user_row is None:
        cur.execute(
            "INSERT INTO users (firebase_uid, email, display_name, email_verified) VALUES (?, ?, ?, ?)",
            (
                decoded.get("uid"),
                decoded.get("email"),
                decoded.get("name") or decoded.get("email"),
                1 if decoded.get("email_verified", False) else 0,
            ),
        )
        conn.commit()
        user_id = cur.lastrowid
    else:
        user_id = user_row[0]

    cur.execute(
        "SELECT answer FROM exam_questions WHERE id = ?",
        (question_id,)
    )
    question_row = cur.fetchone()

    if question_row is None:
        conn.close()
        raise HTTPException(status_code=404, detail="문제를 찾을 수 없습니다.")

    correct_choice = question_row[0]

    if correct_choice is None:
        conn.close()
        raise HTTPException(
            status_code=500,
            detail="문제의 정답이 등록되어 있지 않습니다."
        )

    is_correct = int(selected_choice == int(correct_choice))

    cur.execute(
        "INSERT INTO question_attempts (user_id, question_id, selected_choice, correct_choice, is_correct) VALUES (?, ?, ?, ?, ?)",
        (user_id, question_id, selected_choice, int(correct_choice), is_correct),
    )

    attempt_id = cur.lastrowid
    conn.commit()
    conn.close()

    return {
        "success": True,
        "attempt_id": attempt_id,
        "user_id": user_id,
        "question_id": question_id,
        "selected_choice": selected_choice,
        "correct_choice": int(correct_choice),
        "is_correct": bool(is_correct),
    }

# ATTEMPT_UI_SYNC_V3_COMPLETE
# ==================== QUESTION_BANK_AND_MOCK_V4 ====================

def _v4_db_path():
    return Path(os.getenv("DENTAL_DB_PATH", str(Path(__file__).resolve().parent / "data" / "dental_tutor.db")))


def _v4_conn():
    conn = sqlite3.connect(_v4_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _v4_user_id(request: Request):
    try:
        verifier = globals().get("_verify_firebase_request")
        if verifier is None:
            return None
        user = verifier(request)
        if not user:
            return None
        uid = user.get("uid") if isinstance(user, dict) else None
        if not uid:
            return None
        conn = _v4_conn()
        try:
            row = conn.execute("SELECT id FROM users WHERE firebase_uid=?", (uid,)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


def _v4_shell(title, body, script=""):
    return f'''<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
*{{box-sizing:border-box}}
body{{margin:0;background:#f6f6f7;color:#18181b;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Malgun Gothic",Arial,sans-serif}}
.wrap{{max-width:1080px;margin:0 auto;padding:34px 18px 64px}}
.top{{display:flex;justify-content:space-between;gap:15px;align-items:flex-start;margin-bottom:22px}}
h1{{margin:0;font-size:28px;letter-spacing:-.04em}}
.sub{{margin-top:7px;color:#71717a;font-size:13px;line-height:1.6}}
.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:13px}}
.card,.quiz,.upload,.wrong{{background:#fff;border:1px solid #e4e4e7;border-radius:18px;padding:18px;box-shadow:0 5px 22px rgba(0,0,0,.04)}}
.card{{cursor:pointer;text-align:left;transition:.15s ease}}
.card:hover{{transform:translateY(-2px);border-color:#a1a1aa}}
.title{{font-weight:800;font-size:16px}}
.count{{margin-top:7px;color:#71717a;font-size:13px}}
button{{font:inherit;border:none;border-radius:12px;padding:11px 14px;cursor:pointer;font-weight:800}}
.dark{{background:#18181b;color:white}} .light{{background:#f4f4f5;color:#18181b}}
.choice{{display:block;width:100%;text-align:left;background:#fafafa;border:1px solid #e4e4e7;margin:8px 0}}
.choice.selected{{border-color:#18181b;background:#f4f4f5}}
.choice.correct{{border-color:#52525b;background:#f4f4f5}}
.choice.wrong{{border-color:#d4d4d8;background:#fafafa}}
.ok{{padding:14px;border-radius:12px;background:#f4f4f5;margin-top:14px;line-height:1.72}}
.row{{display:flex;gap:10px;flex-wrap:wrap;align-items:center}}
progress{{width:100%;height:12px}}
.list{{display:grid;gap:10px;margin-top:15px}}
.small{{font-size:12px;color:#71717a}}
@media(max-width:760px){{.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}
@media(max-width:520px){{.grid{{grid-template-columns:1fr}}.wrap{{padding-top:20px}}}}
</style>
</head><body><div class="wrap">{body}</div><script>{script}</script></body></html>'''


@app.get("/expected", response_class=HTMLResponse)
def expected_page(subject: str | None = None):
    body = f'''
    <div class="top"><div><h1>예상문제</h1><div class="sub"><div class="sub"></div></div></div><a class="light" href="/study?subject={json.dumps(subject or '')}">뒤로</a></div>
    <div id="subjectGrid" class="grid"></div>
    <div id="quizBox" style="display:none;margin-top:20px"></div>
    '''
    script = r'''
    const fixedSubject = %s;
    let current=null, selected=null;
    function esc(v){return String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;')}
    async function loadSubjects(){
      const grid=document.getElementById('subjectGrid');
      if(fixedSubject){
        grid.innerHTML=`<button class="card" onclick="loadQuestion(fixedSubject)"><div class="title">${esc(fixedSubject)}</div><div class="count">이 과목 예상문제 시작</div></button>`;
        loadQuestion(fixedSubject); return;
      }
      const r=await fetch('/api/expected/stats'); const d=await r.json();
      grid.innerHTML=(d.subjects||[]).map(x=>`<button class="card" onclick="loadQuestion('${esc(x.subject)}')"><div class="title">${esc(x.subject)}</div><div class="count">예상문제 ${x.total}문제</div></button>`).join('');
    }
    async function loadQuestion(subject){
      const r=await fetch('/api/expected/random?subject='+encodeURIComponent(subject)); const d=await r.json();
      if(!d.success){alert(d.message||'등록된 예상문제가 없습니다.');return}
      current=d.question; selected=null; render();
      document.getElementById('quizBox').style.display='block';
      window.scrollTo({top:document.getElementById('quizBox').offsetTop-20,behavior:'smooth'});
    }
    function render(){const q=current;document.getElementById('quizBox').innerHTML=`<div class="quiz"><div class="small">${esc(q.subject)} · 예상문제 ${q.question_number}번</div><h2 style="font-size:21px;line-height:1.65">${esc(q.question_text)}</h2>${q.choices.map(c=>`<button class="choice ${selected===c.number?'selected':''}" onclick="choose(${c.number})">${c.number}. ${esc(c.text)}</button>`).join('')}<div class="row" style="margin-top:16px"><button class="dark" onclick="submitAnswer()">정답 확인</button><button class="light" onclick="loadQuestion(current.subject)">다른 문제</button></div><div id="result"></div></div>`}
    function choose(n){selected=n;render()}
    async function submitAnswer(){
      if(selected===null){alert('보기를 선택하세요.');return}
      const good=selected===current.answer;
      const token=localStorage.getItem('firebase_id_token')||'';
      let save='';
      try{const r=await fetch('/api/expected/attempt',{method:'POST',headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},body:JSON.stringify({question_id:current.id,selected_choice:selected})}); const d=await r.json(); save=d.success?'풀이 기록이 저장되었습니다.':''}catch(e){}
      document.getElementById('result').innerHTML=`<div class="ok"><strong>${good?'정답입니다.':'오답입니다.'}</strong><br>정답: ${current.answer}번<br><br>${esc(current.explanation||'해설이 등록되지 않은 문제입니다.')}<br><br><span class="small">${esc(save)}</span></div>`;
    }
    loadSubjects();
    ''' % json.dumps(subject or '', ensure_ascii=False)
    return HTMLResponse(_v4_shell("예상문제", body, script))


@app.get("/api/expected/stats")
def expected_stats_api_v4():
    conn=_v4_conn()
    try:
        return {"success":True,"subjects":expected_stats(conn)}
    finally: conn.close()


@app.get("/api/expected/random")
def expected_random_api_v4(subject: str):
    conn=_v4_conn()
    try:
        row=get_random_expected(conn, subject)
        if row is None:
            return JSONResponse(status_code=404,content={"success":False,"message":f"{subject} 과목의 예상문제가 아직 없습니다."})
        return {"success":True,"question":row}
    finally: conn.close()


@app.post("/api/expected/attempt")
def expected_attempt_api_v4(request: Request, payload: dict):
    conn=_v4_conn()
    try:
        init_expected_tables(conn)
        qid=int(payload.get("question_id"))
        selected=payload.get("selected_choice")
        row=conn.execute("SELECT answer FROM expected_questions WHERE id=?",(qid,)).fetchone()
        if not row:
            return JSONResponse(status_code=404,content={"success":False,"message":"예상문제를 찾지 못했습니다."})
        correct=int(row[0])
        user_id=_v4_user_id(request)
        good=int(selected)==correct
        conn.execute("INSERT INTO expected_attempts(user_id,question_id,selected_choice,correct_choice,is_correct) VALUES(?,?,?,?,?)",(user_id, qid, selected, correct, int(good)))
        conn.commit()
        return {"success":True,"is_correct":good,"correct_choice":correct}
    finally: conn.close()


@app.get("/admin/expected", response_class=HTMLResponse)
def expected_admin_v4():
    body='''<div class="top"><div><h1>예상문제 문제은행</h1><div class="sub">DOCX 여러 개를 한 번에 업로드합니다. 파일명에서 과목을 자동 인식합니다.</div></div><button class="light" style="font-weight:700;font-size:16px;padding:10px 20px;min-width:90px;cursor:pointer" onclick="location.href='/'">← 홈으로</button></div><div class="upload"><input id="files" type="file" accept=".docx" multiple><button class="dark" onclick="uploadAll()">문제은행에 추가</button><div id="status" style="margin-top:14px;line-height:1.8"></div></div>'''
    script=r'''
    function esc(v){return String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')}
    async function uploadAll(){
      const files=[...document.getElementById('files').files]; if(!files.length){alert('DOCX 파일을 선택하세요.');return}
      const token=localStorage.getItem('firebase_id_token')||''; let out=[];
      for(const f of files){const fd=new FormData();fd.append('file',f);const r=await fetch('/api/expected/import',{method:'POST',body:fd,headers:token?{Authorization:'Bearer '+token}:{}});const d=await r.json();out.push(d.message||f.name)}
      document.getElementById('status').innerHTML=out.map(x=>`• ${esc(x)}`).join('<br>');
    }
    '''
    return HTMLResponse(_v4_shell("예상문제 업로드", body, script))


@app.post("/api/expected/import")
async def expected_import_api_v4(request: Request, file: UploadFile = File(...)):
    if _v4_user_id(request) is None:
        return JSONResponse(status_code=401,content={"success":False,"message":"로그인 후 업로드하세요."})
    filename=Path(file.filename or "uploaded.docx").name
    if not filename.lower().endswith('.docx'):
        return JSONResponse(status_code=400,content={"success":False,"message":"DOCX만 업로드할 수 있습니다."})
    data=await file.read()
    conn=_v4_conn()
    try:
        return import_docx_to_db(conn,data,filename)
    except Exception as e:
        conn.rollback()
        return JSONResponse(status_code=400,content={"success":False,"message":f"{filename}: {type(e).__name__}: {e}"})
    finally: conn.close()


@app.get("/mock-exam", response_class=HTMLResponse)
def mock_exam_v4():
    body='''<div class="top"><div><h1>AI 모의고사</h1><div class="sub">1교시 100문제 / 2교시 100문제. 실제 국가시험의 과목별 문항 수를 그대로 적용합니다.</div></div><button class="light" onclick="location.href='/'">홈</button></div><div class="grid"><button class="card" onclick="start(1)"><div class="title">1교시 AI 모의고사</div><div class="count">100문제</div></button><button class="card" onclick="start(2)"><div class="title">2교시 AI 모의고사</div><div class="count">100문제</div></button></div><div id="quizBox" style="display:none;margin-top:20px"></div>'''
    script=r'''
    let sessionId=null, sessionNo=null, index=1, q=null, selected=null, answered=false;

    function esc(v){
        return String(v??'')
            .replaceAll('&','&amp;')
            .replaceAll('<','&lt;')
            .replaceAll('>','&gt;')
            .replaceAll('"','&quot;')
            .replaceAll("'",'&#039;');
    }

    async function start(s){
        const token=localStorage.getItem('firebase_id_token')||'';
        const r=await fetch('/api/mock-exam/start?session='+s,{
            method:'POST',
            headers:token?{Authorization:'Bearer '+token}:{}
        });
        const d=await r.json();
        if(!d.success){
            alert(d.message||'모의고사를 시작할 수 없습니다.');
            return;
        }
        sessionId=d.session_id;
        sessionNo=s;
        index=1;
        await next();
    }

    async function next(){
        const box=document.getElementById('quizBox');
        box.style.display='block';
        box.innerHTML='<div class="quiz"><div class="small">문제를 불러오는 중입니다...</div></div>';

        const r=await fetch(`/api/mock-exam/${sessionId}/question/${index}`);
        const d=await r.json();
        if(!d.success){
            alert(d.message||'문제를 불러오지 못했습니다.');
            return;
        }

        q=d.question;
        selected=null;
        answered=false;
        render();

        window.scrollTo({
            top:box.offsetTop-20,
            behavior:'smooth'
        });
    }

    function render(){
        document.getElementById('quizBox').innerHTML=`
        <div class="quiz">
            <div class="small">${sessionNo}교시 · ${index}/100 · ${esc(q.subject)}</div>
            <progress max="100" value="${index}"></progress>
            <h2 style="font-size:20px;line-height:1.65">${esc(q.question_text)}</h2>
            ${q.choices.map((c,i)=>`
                <button
                    class="choice ${selected===i+1?'selected':''}"
                    onclick="choose(${i+1})"
                    ${answered?'disabled':''}
                >${i+1}. ${esc(c)}</button>
            `).join('')}
            <div class="row" style="margin-top:16px">
                <button class="dark" onclick="answer()" ${answered?'disabled':''}>정답 확인</button>
            </div>
            <div id="result"></div>
        </div>`;
    }

    function choose(n){
        if(answered) return;
        selected=n;
        render();
    }

    async function answer(){
        if(answered) return;
        if(selected===null){
            alert('보기를 선택하세요.');
            return;
        }

        const token=localStorage.getItem('firebase_id_token')||'';
        const r=await fetch('/api/mock-exam/answer',{
            method:'POST',
            headers:{
                'Content-Type':'application/json',
                ...(token?{Authorization:'Bearer '+token}:{})
            },
            body:JSON.stringify({
                session_id:sessionId,
                question_id:q.id,
                selected_choice:selected
            })
        });

        const d=await r.json();
        if(!d.success){
            alert(d.message||'정답 처리 중 오류가 발생했습니다.');
            return;
        }

        answered=true;
        render();

        document.getElementById('result').innerHTML=`
            <div class="ok">
                <strong>${d.is_correct?'정답입니다.':'오답입니다.'}</strong>
                <br>정답: ${d.correct_choice}번
                <br><br>${esc(q.explanation||'해설이 없습니다.')}
            </div>
            <div style="margin-top:14px">
                <button
                    class="dark"
                    onclick="${index<100?'index+=1;next()':'finish()'}"
                >${index<100?'다음 문제':'모의고사 종료'}</button>
            </div>`;
    }

    async function finish(){
        const box=document.getElementById('quizBox');
        box.innerHTML='<div class="quiz"><div class="small">채점 결과를 계산하는 중입니다...</div></div>';

        const r=await fetch(`/api/mock-exam/${sessionId}/result`);
        const d=await r.json();

        if(!d.success){
            box.innerHTML=`<div class="quiz"><h2>결과를 불러오지 못했습니다.</h2><div class="small">${esc(d.message||'다시 시도해주세요.')}</div></div>`;
            return;
        }

        const subjects=(d.subjects||[]).map(s=>`
            <div style="
                display:grid;
                grid-template-columns:1fr auto;
                gap:12px;
                padding:12px 0;
                border-bottom:1px solid #e4e4e7;
            ">
                <div>
                    <strong>${esc(s.subject)}</strong>
                    <div class="small">${s.correct}/${s.answered} 정답</div>
                </div>
                <div style="font-weight:800">${s.accuracy}%</div>
            </div>
        `).join('');

        box.innerHTML=`
        <div class="quiz">
            <div class="small">${sessionNo}교시 AI 모의고사 결과</div>
            <h2 style="font-size:26px;margin-bottom:6px">모의고사를 완료했습니다.</h2>

            <div style="
                display:grid;
                grid-template-columns:repeat(2,minmax(0,1fr));
                gap:10px;
                margin:18px 0;
            ">
                <div class="ok" style="text-align:center">
                    <div class="small">점수</div>
                    <div style="font-size:28px;font-weight:900">${d.score}점</div>
                </div>
                <div class="ok" style="text-align:center">
                    <div class="small">정답률</div>
                    <div style="font-size:28px;font-weight:900">${d.accuracy}%</div>
                </div>
                <div class="ok" style="text-align:center">
                    <div class="small">정답</div>
                    <div style="font-size:22px;font-weight:900">${d.correct}문제</div>
                </div>
                <div class="ok" style="text-align:center">
                    <div class="small">오답</div>
                    <div style="font-size:22px;font-weight:900">${d.wrong}문제</div>
                </div>
            </div>

            <div class="small" style="margin-bottom:8px">
                응답 ${d.answered}/${d.total_questions}문제
            </div>

            <h3 style="margin-top:22px">과목별 결과</h3>
            <div>${subjects||'<div class="small">과목별 결과가 없습니다.</div>'}</div>

            <div class="row" style="margin-top:20px">
                <button class="dark" onclick="location.href='/wrong'">오답정리 보기</button>
                <button class="light" onclick="location.href='/mock-exam'">다시 풀기</button>
                <button class="light" onclick="location.href='/'">홈</button>
            </div>
        </div>`;

        window.scrollTo({top:box.offsetTop-20,behavior:'smooth'});
    }

    '''
    return HTMLResponse(_v4_shell("AI 모의고사", body, script))


@app.post("/api/mock-exam/start")
def mock_start_api_v4(request: Request, session: int):
    user_id=_v4_user_id(request)
    conn=_v4_conn()
    try:
        return {"success":True,**start_ai_mock_session(conn,session,user_id)}
    except Exception as e:
        return JSONResponse(status_code=400,content={"success":False,"message":str(e)})
    finally: conn.close()


@app.get("/api/mock-exam/{session_id}/question/{index}")
def mock_question_api_v4(session_id: int, index: int):
    conn=_v4_conn()
    try:
        init_mock_tables(conn)
        session=conn.execute("SELECT * FROM ai_mock_sessions WHERE id=?",(session_id,)).fetchone()
        if not session:
            return JSONResponse(status_code=404,content={"success":False,"message":"모의고사를 찾지 못했습니다."})
        if not 1 <= index <= int(session[3]):
            return JSONResponse(status_code=400,content={"success":False,"message":f"문제 번호는 1~{session[3]}입니다."})
        existing=conn.execute("SELECT * FROM ai_mock_questions WHERE session_id=? AND question_index=?",(session_id,index)).fetchone()
        if existing:
            explanation = existing[8] or ""

            if not explanation:
                exam_row = conn.execute(
                    "SELECT id FROM exam_questions WHERE subject=? AND question_text=? LIMIT 1",
                    (existing[4], existing[5]),
                ).fetchone()

                if exam_row:
                    final_row = conn.execute(
                        "SELECT f.answer_reason, f.choice_explanations_json, f.memory_point "
                        "FROM exam_questions q "
                        "JOIN exam_explanations_final f "
                        "ON f.exam_year=q.exam_year AND f.session=q.session "
                        "AND f.question_number=q.question_number "
                        "WHERE q.id=?",
                        (exam_row[0],),
                    ).fetchone()

                    if final_row:
                        choice_data = json.loads(final_row[1] or "{}")
                        choice_lines = [
                            f"{number}. {str(choice_data[number] or '').strip()}"
                            for number in sorted(choice_data, key=lambda value: int(value))
                        ]
                        parts = [
                            "📌 정답 이유",
                            str(final_row[0] or "").strip(),
                            "",
                            "🔎 선택지별 해설",
                            "\n".join(choice_lines),
                        ]
                        memory_point = str(final_row[2] or "").strip()
                        if memory_point:
                            parts.extend(["", "🧠 암기 포인트", memory_point])
                        explanation = "\n".join(
                            part for part in parts if part != ""
                        ).strip()

                        conn.execute(
                            "UPDATE ai_mock_questions SET explanation=? WHERE id=?",
                            (explanation, existing[0]),
                        )
                        conn.commit()

            return {"success":True,"question":{"id":existing[0],"subject":existing[4],"question_text":existing[5],"choices":json.loads(existing[6]),"answer":existing[7],"explanation":explanation}}
        q=generate_ai_mock_question(conn,int(session[2]),index,session_id)
        cur=conn.cursor();cur.execute("INSERT INTO ai_mock_questions(session_id,question_index,session_no,subject,question_text,choices_json,correct_choice,explanation) VALUES(?,?,?,?,?,?,?,?)",(session_id,index,int(session[2]),q['subject'],q['question_text'],json.dumps(q['choices'],ensure_ascii=False),q['answer'],q['explanation']))
        qid=cur.lastrowid;conn.commit()
        return {"success":True,"question":{"id":qid,"subject":q['subject'],"question_text":q['question_text'],"choices":q['choices'],"answer":q['answer'],"explanation":q['explanation']}}
    except Exception as e:
        return JSONResponse(status_code=500,content={"success":False,"message":f"AI 출제 오류: {type(e).__name__}: {e}"})
    finally: conn.close()


@app.post("/api/mock-exam/answer")
def mock_answer_api_v4(request: Request, payload: dict):
    conn=_v4_conn()
    try:
        init_mock_tables(conn)
        session_id=int(payload.get('session_id')); qid=int(payload.get('question_id')); selected=payload.get('selected_choice')
        row=conn.execute("SELECT correct_choice FROM ai_mock_questions WHERE id=? AND session_id=?",(qid,session_id)).fetchone()
        if not row:
            return JSONResponse(status_code=404,content={"success":False,"message":"문제를 찾지 못했습니다."})
        correct=int(row[0]); good=int(selected)==correct
        user_id=_v4_user_id(request)
        conn.execute("DELETE FROM ai_mock_attempts WHERE session_id=? AND mock_question_id=?",(session_id,qid))
        conn.execute("INSERT INTO ai_mock_attempts(session_id,mock_question_id,user_id,selected_choice,correct_choice,is_correct) VALUES(?,?,?,?,?,?)",(session_id,qid,user_id,selected,correct,int(good)))
        conn.commit()
        return {"success":True,"is_correct":good,"correct_choice":correct}
    finally: conn.close()



@app.get("/api/mock-exam/{session_id}/result")
def mock_result_api_v4(session_id: int):
    conn=_v4_conn()
    try:
        init_mock_tables(conn)

        session=conn.execute(
            "SELECT * FROM ai_mock_sessions WHERE id=?",
            (session_id,),
        ).fetchone()

        if not session:
            return JSONResponse(
                status_code=404,
                content={"success":False,"message":"모의고사를 찾지 못했습니다."},
            )

        total_questions=int(session["total_questions"])

        overall=conn.execute(
            '''
            SELECT
                COUNT(*) AS answered,
                COALESCE(SUM(is_correct),0) AS correct
            FROM ai_mock_attempts
            WHERE session_id=?
            ''',
            (session_id,),
        ).fetchone()

        answered=int(overall["answered"] or 0)
        correct=int(overall["correct"] or 0)
        wrong=max(answered-correct,0)
        accuracy=round((correct/answered)*100,1) if answered else 0.0
        score=round((correct/total_questions)*100,1) if total_questions else 0.0

        subject_rows=conn.execute(
            '''
            SELECT
                q.subject AS subject,
                COUNT(a.id) AS answered,
                COALESCE(SUM(a.is_correct),0) AS correct
            FROM ai_mock_questions q
            LEFT JOIN ai_mock_attempts a
              ON a.mock_question_id=q.id
             AND a.session_id=q.session_id
            WHERE q.session_id=?
            GROUP BY q.subject
            ORDER BY MIN(q.question_index)
            ''',
            (session_id,),
        ).fetchall()

        subjects=[]
        for row in subject_rows:
            s_answered=int(row["answered"] or 0)
            s_correct=int(row["correct"] or 0)
            subjects.append({
                "subject":row["subject"],
                "answered":s_answered,
                "correct":s_correct,
                "wrong":max(s_answered-s_correct,0),
                "accuracy":round((s_correct/s_answered)*100,1) if s_answered else 0.0,
            })

        return {
            "success":True,
            "session_id":session_id,
            "session":int(session["session_no"]),
            "total_questions":total_questions,
            "answered":answered,
            "correct":correct,
            "wrong":wrong,
            "accuracy":accuracy,
            "score":score,
            "subjects":subjects,
        }
    finally:
        conn.close()


@app.get("/wrong", response_class=HTMLResponse)
def wrong_page_v4(request: Request):
    uid=_v4_user_id(request)
    if uid is None:
        return HTMLResponse(_v4_shell("오답정리", '<div class="wrong"><h1>로그인이 필요합니다.</h1><p class="sub">로그인 후 문제풀이 기록을 확인할 수 있습니다.</p></div>'))
    conn=_v4_conn()
    try:
        rows=[]
        try:
            rows += [dict(r) for r in conn.execute("SELECT '시험문제' kind,q.subject,q.question_text,a.created_at FROM question_attempts a JOIN exam_questions q ON q.id=a.question_id WHERE a.user_id=? AND a.is_correct=0 ORDER BY a.created_at DESC LIMIT 100",(uid,)).fetchall()]
        except Exception:
            pass
        try:
            rows += [dict(r) for r in conn.execute("SELECT '예상문제' kind,q.subject,q.question_text,a.created_at FROM expected_attempts a JOIN expected_questions q ON q.id=a.question_id WHERE a.user_id=? AND a.is_correct=0 ORDER BY a.created_at DESC LIMIT 100",(uid,)).fetchall()]
        except Exception:
            pass
        try:
            rows += [dict(r) for r in conn.execute("SELECT 'AI 모의고사' kind,q.subject,q.question_text,a.created_at FROM ai_mock_attempts a JOIN ai_mock_questions q ON q.id=a.mock_question_id WHERE a.user_id=? AND a.is_correct=0 ORDER BY a.created_at DESC LIMIT 100",(uid,)).fetchall()]
        except Exception:
            pass
        body='<div class="top"><div><h1>오답정리</h1><div class="sub">시험문제와 예상문제에서 틀린 기록을 모아봅니다.</div></div><button class="light" onclick="location.href='/'">홈</button></div>'
        body += '<div class="list">' + ''.join(f'<div class="wrong"><div><strong>{str(r.get("kind",""))}</strong> · {str(r.get("subject",""))}</div><div style="margin-top:7px;line-height:1.6">{str(r.get("question_text",""))}</div><div class="small" style="margin-top:6px">{str(r.get("created_at",""))}</div></div>' for r in rows)
        body += '</div>' if rows else '<div class="wrong"><h2>오답 기록이 없습니다.</h2></div>'
        return HTMLResponse(_v4_shell("오답정리",body))
    finally: conn.close()

