from pathlib import Path

p = Path(r".\dental_patch_v4\mock_exam_v4.py")
t = p.read_text(encoding="utf-8")

marker = "def generate_question(conn: sqlite3.Connection, session: int, index: int, session_id: Optional[int] = None) -> dict:"

block = r'''
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


'''

if marker not in t:
    raise SystemExit("MARKER_NOT_FOUND")

if "SUBJECT_SUMMARY_FILES" in t:
    raise SystemExit("ALREADY_INSERTED")

t = t.replace(marker, block + marker, 1)
p.write_text(t, encoding="utf-8")

print("SUMMARY_HELPERS_INSERTED")
