from pathlib import Path

p = Path(r".\dental_patch_v4\mock_exam_v4.py")
t = p.read_text(encoding="utf-8")

old = "        explanation = ''"
new = "        explanation = _generate_exam_explanation(subject, str(selected['question_text']).strip(), choices, int(selected['answer']))"

if t.count(old) != 1:
    raise SystemExit(f"EXPECTED_ONE_EXPLANATION_LINE_BUT_FOUND={t.count(old)}")

t = t.replace(old, new, 1)
p.write_text(t, encoding="utf-8")

print("EXAM_EXPLANATION_CONNECTED")
