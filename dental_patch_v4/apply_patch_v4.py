from __future__ import annotations

from pathlib import Path
import re
import shutil
from datetime import datetime

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
MAIN = PROJECT / "main.py"
REQ = PROJECT / "requirements.txt"

if not MAIN.exists():
    raise SystemExit(f"main.py를 찾지 못했습니다: {MAIN}")

for required in ("expected_bank_v4.py", "mock_exam_v4.py"):
    p = ROOT / required
    if not p.exists():
        raise SystemExit(f"패키지 파일이 없습니다: {p}")

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backup = PROJECT / f"main_before_questionbank_v4_{stamp}.py"
shutil.copy2(MAIN, backup)

text = MAIN.read_text(encoding="utf-8", errors="ignore")

# ------------------------------------------------------------------
# requirements
# ------------------------------------------------------------------
req_lines = [x.strip() for x in REQ.read_text(encoding="utf-8", errors="ignore").splitlines()] if REQ.exists() else []
if not any(x.lower().startswith("python-docx") for x in req_lines):
    req_lines.append("python-docx")
REQ.write_text("\n".join(x for x in req_lines if x) + "\n", encoding="utf-8")

# ------------------------------------------------------------------
# copy helper modules to project root
# ------------------------------------------------------------------
shutil.copy2(ROOT / "expected_bank_v4.py", PROJECT / "expected_bank_v4.py")
shutil.copy2(ROOT / "mock_exam_v4.py", PROJECT / "mock_exam_v4.py")

# ------------------------------------------------------------------
# Ensure required imports
# ------------------------------------------------------------------
def ensure_import(text: str, module: str, names: list[str]) -> str:
    pat = re.compile(rf"^from {re.escape(module)} import ([^\n]+)$", re.M)
    m = pat.search(text)
    if m:
        current = m.group(1)
        missing = [n for n in names if not re.search(rf"\b{re.escape(n)}\b", current)]
        if missing:
            replacement = f"from {module} import {current}, " + ", ".join(missing)
            text = text[:m.start()] + replacement + text[m.end():]
    else:
        text = f"from {module} import {', '.join(names)}\n" + text
    return text

if not re.search(r"^import os\s*$", text, re.M):
    text = "import os\n" + text
if not re.search(r"^import sqlite3\s*$", text, re.M):
    text = "import sqlite3\n" + text
if not re.search(r"^import json\s*$", text, re.M):
    text = "import json\n" + text
text = ensure_import(text, "fastapi", ["Request", "File", "UploadFile"])
text = ensure_import(text, "fastapi.responses", ["HTMLResponse", "JSONResponse"])

# ------------------------------------------------------------------
# V4 backend imports before app = FastAPI
# ------------------------------------------------------------------
import_block = '''\n# ==================== QUESTION_BANK_V4 ====================\nfrom expected_bank_v4 import ALL_SUBJECTS, expected_stats, get_random_expected, import_docx_to_db, init_expected_tables\nfrom mock_exam_v4 import SESSION_SUBJECT_QUOTAS, generate_question as generate_ai_mock_question, init_mock_tables, start_session as start_ai_mock_session, subject_for_index as mock_subject_for_index\n\n'''
if "QUESTION_BANK_V4" not in text:
    app_idx = text.find("app = FastAPI")
    if app_idx == -1:
        raise SystemExit("app = FastAPI(...) 위치를 찾지 못했습니다.")
    line_start = text.rfind("\n", 0, app_idx) + 1
    text = text[:line_start] + import_block + text[line_start:]

# ------------------------------------------------------------------
# V4 routes
# ------------------------------------------------------------------
routes = """\n# ==================== QUESTION_BANK_AND_MOCK_V4 ====================\n\ndef _v4_db_path():\n    return Path(os.getenv("DENTAL_DB_PATH", str(Path(__file__).resolve().parent / "data" / "dental_tutor.db")))\n\n\ndef _v4_conn():\n    conn = sqlite3.connect(_v4_db_path())\n    conn.row_factory = sqlite3.Row\n    return conn\n\n\ndef _v4_user_id(request: Request):\n    try:\n        verifier = globals().get("_verify_firebase_request")\n        if verifier is None:\n            return None\n        user = verifier(request)\n        if not user:\n            return None\n        uid = user.get("uid") if isinstance(user, dict) else None\n        if not uid:\n            return None\n        conn = _v4_conn()\n        try:\n            row = conn.execute("SELECT id FROM users WHERE firebase_uid=?", (uid,)).fetchone()\n            return row[0] if row else None\n        finally:\n            conn.close()\n    except Exception:\n        return None\n\n\ndef _v4_shell(title, body, script=""):\n    return f'''<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width,initial-scale=1">\n<title>{title}</title>\n<style>\n*{{box-sizing:border-box}}\nbody{{margin:0;background:#f6f6f7;color:#18181b;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Malgun Gothic",Arial,sans-serif}}\n.wrap{{max-width:1080px;margin:0 auto;padding:34px 18px 64px}}\n.top{{display:flex;justify-content:space-between;gap:15px;align-items:flex-start;margin-bottom:22px}}\nh1{{margin:0;font-size:28px;letter-spacing:-.04em}}\n.sub{{margin-top:7px;color:#71717a;font-size:13px;line-height:1.6}}\n.grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:13px}}\n.card,.quiz,.upload,.wrong{{background:#fff;border:1px solid #e4e4e7;border-radius:18px;padding:18px;box-shadow:0 5px 22px rgba(0,0,0,.04)}}\n.card{{cursor:pointer;text-align:left;transition:.15s ease}}\n.card:hover{{transform:translateY(-2px);border-color:#a1a1aa}}\n.title{{font-weight:800;font-size:16px}}\n.count{{margin-top:7px;color:#71717a;font-size:13px}}\nbutton{{font:inherit;border:none;border-radius:12px;padding:11px 14px;cursor:pointer;font-weight:800}}\n.dark{{background:#18181b;color:white}} .light{{background:#f4f4f5;color:#18181b}}\n.choice{{display:block;width:100%;text-align:left;background:#fafafa;border:1px solid #e4e4e7;margin:8px 0}}\n.choice.selected{{border-color:#18181b;background:#f4f4f5}}\n.choice.correct{{border-color:#52525b;background:#f4f4f5}}\n.choice.wrong{{border-color:#d4d4d8;background:#fafafa}}\n.ok{{padding:14px;border-radius:12px;background:#f4f4f5;margin-top:14px;line-height:1.72}}\n.row{{display:flex;gap:10px;flex-wrap:wrap;align-items:center}}\nprogress{{width:100%;height:12px}}\n.list{{display:grid;gap:10px;margin-top:15px}}\n.small{{font-size:12px;color:#71717a}}\n@media(max-width:760px){{.grid{{grid-template-columns:repeat(2,minmax(0,1fr))}}}}\n@media(max-width:520px){{.grid{{grid-template-columns:1fr}}.wrap{{padding-top:20px}}}}\n</style>\n</head><body><div class="wrap">{body}</div><script>{script}</script></body></html>'''
\n\n@app.get("/expected", response_class=HTMLResponse)\ndef expected_page(subject: str | None = None):\n    body = f'''\n    <div class="top"><div><h1>예상문제</h1><div class="sub">업로드한 과목별 DOCX 문제은행에서 랜덤 출제합니다. 정답과 해설은 DOCX에 작성한 내용을 사용합니다.</div></div><button class="light" onclick="location.href='/study?subject={json.dumps(subject or '')}'">뒤로</button></div>\n    <div id="subjectGrid" class="grid"></div>\n    <div id="quizBox" style="display:none;margin-top:20px"></div>\n    '''\n    script = r'''\n    const fixedSubject = %s;\n    let current=null, selected=null;\n    function esc(v){return String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;')}\n    async function loadSubjects(){\n      const grid=document.getElementById('subjectGrid');\n      if(fixedSubject){\n        grid.innerHTML=`<button class="card" onclick="loadQuestion(fixedSubject)"><div class="title">${esc(fixedSubject)}</div><div class="count">이 과목 예상문제 시작</div></button>`;\n        loadQuestion(fixedSubject); return;\n      }\n      const r=await fetch('/api/expected/stats'); const d=await r.json();\n      grid.innerHTML=(d.subjects||[]).map(x=>`<button class="card" onclick="loadQuestion('${esc(x.subject)}')"><div class="title">${esc(x.subject)}</div><div class="count">예상문제 ${x.total}문제</div></button>`).join('');\n    }\n    async function loadQuestion(subject){\n      const r=await fetch('/api/expected/random?subject='+encodeURIComponent(subject)); const d=await r.json();\n      if(!d.success){alert(d.message||'등록된 예상문제가 없습니다.');return}\n      current=d.question; selected=null; render();\n      document.getElementById('quizBox').style.display='block';\n      window.scrollTo({top:document.getElementById('quizBox').offsetTop-20,behavior:'smooth'});\n    }\n    function render(){const q=current;document.getElementById('quizBox').innerHTML=`<div class="quiz"><div class="small">${esc(q.subject)} · 예상문제 ${q.question_number}번</div><h2 style="font-size:21px;line-height:1.65">${esc(q.question_text)}</h2>${q.choices.map(c=>`<button class="choice ${selected===c.number?'selected':''}" onclick="choose(${c.number})">${c.number}. ${esc(c.text)}</button>`).join('')}<div class="row" style="margin-top:16px"><button class="dark" onclick="submitAnswer()">정답 확인</button><button class="light" onclick="loadQuestion(q.subject)">다른 문제</button></div><div id="result"></div></div>`}\n    function choose(n){selected=n;render()}\n    async function submitAnswer(){\n      if(selected===null){alert('보기를 선택하세요.');return}\n      const good=selected===current.answer;\n      const token=localStorage.getItem('firebase_id_token')||'';\n      let save='';\n      try{const r=await fetch('/api/expected/attempt',{method:'POST',headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},body:JSON.stringify({question_id:current.id,selected_choice:selected})}); const d=await r.json(); save=d.success?'풀이 기록이 저장되었습니다.':''}catch(e){}\n      document.getElementById('result').innerHTML=`<div class="ok"><strong>${good?'정답입니다.':'오답입니다.'}</strong><br>정답: ${current.answer}번<br><br>${esc(current.explanation||'해설이 등록되지 않은 문제입니다.')}<br><br><span class="small">${esc(save)}</span></div>`;\n    }\n    loadSubjects();\n    ''' % json.dumps(subject or '', ensure_ascii=False)\n    return HTMLResponse(_v4_shell("예상문제", body, script))\n\n\n@app.get("/api/expected/stats")\ndef expected_stats_api_v4():\n    conn=_v4_conn()\n    try:\n        return {"success":True,"subjects":expected_stats(conn)}\n    finally: conn.close()\n\n\n@app.get("/api/expected/random")\ndef expected_random_api_v4(subject: str):\n    conn=_v4_conn()\n    try:\n        row=get_random_expected(conn, subject)\n        if row is None:\n            return JSONResponse(status_code=404,content={"success":False,"message":f"{subject} 과목의 예상문제가 아직 없습니다."})\n        return {"success":True,"question":row}\n    finally: conn.close()\n\n\n@app.post("/api/expected/attempt")\ndef expected_attempt_api_v4(request: Request, payload: dict):\n    conn=_v4_conn()\n    try:\n        init_expected_tables(conn)\n        qid=int(payload.get("question_id"))\n        selected=payload.get("selected_choice")\n        row=conn.execute("SELECT answer FROM expected_questions WHERE id=?",(qid,)).fetchone()\n        if not row:\n            return JSONResponse(status_code=404,content={"success":False,"message":"예상문제를 찾지 못했습니다."})\n        correct=int(row[0])\n        user_id=_v4_user_id(request)\n        good=int(selected)==correct\n        conn.execute("INSERT INTO expected_attempts(user_id,question_id,selected_choice,correct_choice,is_correct) VALUES(?,?,?,?,?)",(user_id, qid, selected, correct, int(good)))\n        conn.commit()\n        return {"success":True,"is_correct":good,"correct_choice":correct}\n    finally: conn.close()\n\n\n@app.get("/admin/expected", response_class=HTMLResponse)\ndef expected_admin_v4():\n    body='''<div class="top"><div><h1>예상문제 문제은행</h1><div class="sub">DOCX 여러 개를 한 번에 업로드합니다. 파일명에서 과목을 자동 인식합니다.</div></div><button class="light" onclick="location.href='/'">홈</button></div><div class="upload"><input id="files" type="file" accept=".docx" multiple><button class="dark" onclick="uploadAll()">문제은행에 추가</button><div id="status" style="margin-top:14px;line-height:1.8"></div></div>'''\n    script=r'''\n    function esc(v){return String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;')}\n    async function uploadAll(){\n      const files=[...document.getElementById('files').files]; if(!files.length){alert('DOCX 파일을 선택하세요.');return}\n      const token=localStorage.getItem('firebase_id_token')||''; let out=[];\n      for(const f of files){const fd=new FormData();fd.append('file',f);const r=await fetch('/api/expected/import',{method:'POST',body:fd,headers:token?{Authorization:'Bearer '+token}:{}});const d=await r.json();out.push(d.message||f.name)}\n      document.getElementById('status').innerHTML=out.map(x=>`• ${esc(x)}`).join('<br>');\n    }\n    '''\n    return HTMLResponse(_v4_shell("예상문제 업로드", body, script))\n\n\n@app.post("/api/expected/import")\nasync def expected_import_api_v4(request: Request, file: UploadFile = File(...)):\n    if _v4_user_id(request) is None:\n        return JSONResponse(status_code=401,content={"success":False,"message":"로그인 후 업로드하세요."})\n    filename=Path(file.filename or "uploaded.docx").name\n    if not filename.lower().endswith('.docx'):\n        return JSONResponse(status_code=400,content={"success":False,"message":"DOCX만 업로드할 수 있습니다."})\n    data=await file.read()\n    conn=_v4_conn()\n    try:\n        return import_docx_to_db(conn,data,filename)\n    except Exception as e:\n        conn.rollback()\n        return JSONResponse(status_code=400,content={"success":False,"message":f"{filename}: {type(e).__name__}: {e}"})\n    finally: conn.close()\n\n\n@app.get("/mock-exam", response_class=HTMLResponse)\ndef mock_exam_v4():\n    body='''<div class="top"><div><h1>AI 모의고사</h1><div class="sub">1교시 100문제 / 2교시 100문제. 실제 국가시험의 과목별 문항 수를 그대로 적용합니다.</div></div><button class="light" onclick="location.href='/'">홈</button></div><div class="grid"><button class="card" onclick="start(1)"><div class="title">1교시 AI 모의고사</div><div class="count">100문제</div></button><button class="card" onclick="start(2)"><div class="title">2교시 AI 모의고사</div><div class="count">100문제</div></button></div><div id="quizBox" style="display:none;margin-top:20px"></div>'''\n    script=r'''\n    let sessionId=null, sessionNo=null, index=1, q=null, selected=null;\n    function esc(v){return String(v??'').replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;').replaceAll("'",'&#039;')}\n    async function start(s){const token=localStorage.getItem('firebase_id_token')||'';const r=await fetch('/api/mock-exam/start?session='+s,{method:'POST',headers:token?{Authorization:'Bearer '+token}:{}});const d=await r.json();if(!d.success){alert(d.message||'모의고사를 시작할 수 없습니다.');return}sessionId=d.session_id;sessionNo=s;index=1;await next()}\n    async function next(){const r=await fetch(`/api/mock-exam/${sessionId}/question/${index}`);const d=await r.json();if(!d.success){alert(d.message||'AI 문제를 만들지 못했습니다.');return}q=d.question;selected=null;render();document.getElementById('quizBox').style.display='block';window.scrollTo({top:document.getElementById('quizBox').offsetTop-20,behavior:'smooth'})}\n    function render(){document.getElementById('quizBox').innerHTML=`<div class="quiz"><div class="small">${sessionNo}교시 · ${index}/100 · ${esc(q.subject)}</div><progress max="100" value="${index}"></progress><h2 style="font-size:20px;line-height:1.65">${esc(q.question_text)}</h2>${q.choices.map((c,i)=>`<button class="choice ${selected===i+1?'selected':''}" onclick="choose(${i+1})">${i+1}. ${esc(c)}</button>`).join('')}<div class="row" style="margin-top:16px"><button class="dark" onclick="answer()">정답 확인</button></div><div id="result"></div></div>`}\n    function choose(n){selected=n;render()}\n    async function answer(){if(selected===null){alert('보기를 선택하세요.');return}const token=localStorage.getItem('firebase_id_token')||'';const r=await fetch('/api/mock-exam/answer',{method:'POST',headers:{'Content-Type':'application/json',...(token?{Authorization:'Bearer '+token}:{})},body:JSON.stringify({session_id:sessionId,question_id:q.id,selected_choice:selected})});const d=await r.json();document.getElementById('result').innerHTML=`<div class="ok"><strong>${d.is_correct?'정답입니다.':'오답입니다.'}</strong><br>정답: ${d.correct_choice}번<br><br>${esc(q.explanation)}</div><div style="margin-top:14px"><button class="dark" onclick="${index<100?'index+=1;next()':'finish()'}">${index<100?'다음 문제':'모의고사 종료'}</button></div>`}\n    function finish(){document.getElementById('quizBox').innerHTML='<div class="quiz"><h2>모의고사를 완료했습니다.</h2><div class="small">문제풀이 기록이 저장되었습니다.</div></div>'}\n    '''\n    return HTMLResponse(_v4_shell("AI 모의고사", body, script))\n\n\n@app.post("/api/mock-exam/start")\ndef mock_start_api_v4(request: Request, session: int):\n    user_id=_v4_user_id(request)\n    conn=_v4_conn()\n    try:\n        return {"success":True,**start_ai_mock_session(conn,session,user_id)}\n    except Exception as e:\n        return JSONResponse(status_code=400,content={"success":False,"message":str(e)})\n    finally: conn.close()\n\n\n@app.get("/api/mock-exam/{session_id}/question/{index}")\ndef mock_question_api_v4(session_id: int, index: int):\n    conn=_v4_conn()\n    try:\n        init_mock_tables(conn)\n        session=conn.execute("SELECT * FROM ai_mock_sessions WHERE id=?",(session_id,)).fetchone()\n        if not session:\n            return JSONResponse(status_code=404,content={"success":False,"message":"모의고사를 찾지 못했습니다."})\n        if not 1 <= index <= int(session[3]):\n            return JSONResponse(status_code=400,content={"success":False,"message":f"문제 번호는 1~{session[3]}입니다."})\n        existing=conn.execute("SELECT * FROM ai_mock_questions WHERE session_id=? AND question_index=?",(session_id,index)).fetchone()\n        if existing:\n            return {"success":True,"question":{"id":existing[0],"subject":existing[4],"question_text":existing[5],"choices":json.loads(existing[6]),"answer":existing[7],"explanation":existing[8]}}\n        q=generate_ai_mock_question(conn,int(session[2]),index)\n        cur=conn.cursor();cur.execute("INSERT INTO ai_mock_questions(session_id,question_index,session_no,subject,question_text,choices_json,correct_choice,explanation) VALUES(?,?,?,?,?,?,?,?)",(session_id,index,int(session[2]),q['subject'],q['question_text'],json.dumps(q['choices'],ensure_ascii=False),q['answer'],q['explanation']))\n        qid=cur.lastrowid;conn.commit()\n        return {"success":True,"question":{"id":qid,"subject":q['subject'],"question_text":q['question_text'],"choices":q['choices'],"answer":q['answer'],"explanation":q['explanation']}}\n    except Exception as e:\n        return JSONResponse(status_code=500,content={"success":False,"message":f"AI 출제 오류: {type(e).__name__}: {e}"})\n    finally: conn.close()\n\n\n@app.post("/api/mock-exam/answer")\ndef mock_answer_api_v4(request: Request, payload: dict):\n    conn=_v4_conn()\n    try:\n        init_mock_tables(conn)\n        session_id=int(payload.get('session_id')); qid=int(payload.get('question_id')); selected=payload.get('selected_choice')\n        row=conn.execute("SELECT correct_choice FROM ai_mock_questions WHERE id=? AND session_id=?",(qid,session_id)).fetchone()\n        if not row:\n            return JSONResponse(status_code=404,content={"success":False,"message":"문제를 찾지 못했습니다."})\n        correct=int(row[0]); good=int(selected)==correct\n        conn.execute("INSERT INTO ai_mock_attempts(session_id,mock_question_id,user_id,selected_choice,correct_choice,is_correct) VALUES(?,?,?,?,?,?)",(session_id,qid,_v4_user_id(request),selected,correct,int(good)))\n        conn.commit()\n        return {"success":True,"is_correct":good,"correct_choice":correct}\n    finally: conn.close()\n\n\n@app.get("/wrong", response_class=HTMLResponse)\ndef wrong_page_v4(request: Request):\n    uid=_v4_user_id(request)\n    if uid is None:\n        return HTMLResponse(_v4_shell("오답정리", '<div class="wrong"><h1>로그인이 필요합니다.</h1><p class="sub">로그인 후 문제풀이 기록을 확인할 수 있습니다.</p></div>'))\n    conn=_v4_conn()\n    try:\n        rows=[]\n        try:\n            rows += [dict(r) for r in conn.execute("SELECT '시험문제' kind,q.subject,q.question_text,a.created_at FROM question_attempts a JOIN exam_questions q ON q.id=a.question_id WHERE a.user_id=? AND a.is_correct=0 ORDER BY a.created_at DESC LIMIT 100",(uid,)).fetchall()]\n        except Exception:\n            pass\n        try:\n            rows += [dict(r) for r in conn.execute("SELECT '예상문제' kind,q.subject,q.question_text,a.created_at FROM expected_attempts a JOIN expected_questions q ON q.id=a.question_id WHERE a.user_id=? AND a.is_correct=0 ORDER BY a.created_at DESC LIMIT 100",(uid,)).fetchall()]\n        except Exception:\n            pass\n        body='<div class="top"><div><h1>오답정리</h1><div class="sub">시험문제와 예상문제에서 틀린 기록을 모아봅니다.</div></div><button class="light" onclick="location.href='/'">홈</button></div>'\n        body += '<div class="list">' + ''.join(f'<div class="wrong"><div><strong>{str(r.get("kind",""))}</strong> · {str(r.get("subject",""))}</div><div style="margin-top:7px;line-height:1.6">{str(r.get("question_text",""))}</div><div class="small" style="margin-top:6px">{str(r.get("created_at",""))}</div></div>' for r in rows)\n        body += '</div>' if rows else '<div class="wrong"><h2>오답 기록이 없습니다.</h2></div>'\n        return HTMLResponse(_v4_shell("오답정리",body))\n    finally: conn.close()\n"""
if "QUESTION_BANK_AND_MOCK_V4" not in text:
    text = text.rstrip() + routes + "\n"

# ------------------------------------------------------------------
# Patch the subject study slots if they exist.
# ------------------------------------------------------------------
# Remove the concept slot button.
concept_button_patterns = [
    re.compile(r'\n\s*<button\s+class="slot(?: active)?"\s+type="button"\s+onclick="openPanel\(\'concept\', this\)".*?</button>\s*', re.S),
]
for pat in concept_button_patterns:
    text, n = pat.subn("\n", text, count=1)
    if n:
        break

# Replace AI slot with Expected slot, preserving current subject via URLSearchParams variable.
ai_button_pattern = re.compile(r'<button\s+class="slot"\s+type="button"\s+onclick="openPanel\(\'ai\', this\)".*?</button>', re.S)
expected_button = '''<button\n                class="slot"\n                type="button"\n                onclick="location.href='/expected?subject=' + encodeURIComponent(subject)"\n            >\n                <div class="slot-icon">📚</div>\n                <div class="slot-title">예상문제</div>\n                <div class="slot-desc">\n                    내가 업로드한 해당 과목 예상문제를 랜덤으로 풉니다.\n                </div>\n            </button>'''
text, _ = ai_button_pattern.subn(expected_button, text, count=1)

# Remove old concept panel and old AI panel when their comment markers exist.
text = re.sub(r'\n\s*<!-- 개념 -->.*?(?=\n\s*<!-- 시험문제 -->)', '\n', text, count=1, flags=re.S)
text = re.sub(r'\n\s*<!-- AI 문제 -->.*?(?=\n\s*<!-- 오답노트 -->|\n\s*</div>\s*\n\s*<script)', '\n', text, count=1, flags=re.S)

# Rename only visible labels/descriptions in the subject page/home.
text = text.replace("오답노트", "오답정리")
text = re.sub(r'<div class="slot-title">\s*AI 문제\s*</div>', '<div class="slot-title">예상문제</div>', text)
text = text.replace("AI 문제", "예상문제")

# Mark exam panel active when there is no active panel after concept removal.
text = re.sub(r'(<section\s+class="panel)\s+id="panel-exam"', r'\1 active" id="panel-exam"', text, count=1)
# Avoid duplicate active if it already existed.
text = text.replace('class="panel active" id="panel-exam"', 'class="panel active" id="panel-exam"')

# Home random button -> AI mock exam.
text = text.replace("랜덤 기출", "AI 모의고사")
text = text.replace("location.href='/study?mode=random'", "location.href='/mock-exam'")

# The subject dashboard may still have the old AI description.
text = text.replace("요약본과 시험문제를 바탕으로 문제를 생성합니다.", "업로드한 과목별 예상문제를 랜덤으로 풉니다.")

# ------------------------------------------------------------------
# .gitignore
# ------------------------------------------------------------------
gitignore = PROJECT / ".gitignore"
g = gitignore.read_text(encoding="utf-8", errors="ignore") if gitignore.exists() else ""
if "expected_uploads/" not in g:
    g = g.rstrip() + "\n\n# 원본 예상문제 DOCX는 GitHub에 올리지 않음\nexpected_uploads/\n"
gitignore.write_text(g, encoding="utf-8")

# ------------------------------------------------------------------
# Validate then save.
# ------------------------------------------------------------------
compile(text, str(MAIN), "exec")
MAIN.write_text(text, encoding="utf-8")
compile(MAIN.read_text(encoding="utf-8"), str(MAIN), "exec")

print("=" * 78)
print("DENTAL QUESTION BANK + EXPECTED + AI MOCK V4 PATCH COMPLETE")
print("=" * 78)
print("- 과목 화면: 개념 슬롯 제거")
print("- 과목 화면: AI 문제 -> 예상문제")
print("- 예상문제: DOCX 문제은행 랜덤 출제 + DOCX 해설")
print("- 오답노트 -> 오답정리")
print("- 랜덤 기출 -> AI 모의고사")
print("- AI 모의고사: 1교시 100 / 2교시 100")
print("- 국가시험 과목별 문항 수 적용")
print("- 관리자 DOCX 업로드: /admin/expected")
print("- 로컬 간편 등록: expected_uploads 폴더 + import_expected_docx.py")
print(f"백업: {backup.name}")
print("문법 검사: OK")
print("=" * 78)
