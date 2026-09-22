from __future__ import annotations

from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parent.parent
LOCAL = ROOT / 'data' / 'dental_tutor.db'
DEPLOY = ROOT / 'data' / 'dental_tutor_deploy.db'

if not LOCAL.exists():
    raise SystemExit(f'로컬 DB가 없습니다: {LOCAL}')
if not DEPLOY.exists():
    raise SystemExit(f'배포 DB가 없습니다: {DEPLOY}')

local = sqlite3.connect(LOCAL)
local.row_factory = sqlite3.Row
deploy = sqlite3.connect(DEPLOY)

try:
    from expected_bank_v3 import init_expected_tables
    init_expected_tables(local)
    init_expected_tables(deploy)

    deploy.execute('PRAGMA foreign_keys=OFF')
    deploy.execute('BEGIN')
    deploy.execute('DELETE FROM expected_choices')
    deploy.execute('DELETE FROM expected_questions')
    deploy.execute('DELETE FROM expected_question_sets')

    sets = local.execute('SELECT id,subject,set_name,source_filename,created_at FROM expected_question_sets ORDER BY id').fetchall()
    deploy.executemany('INSERT INTO expected_question_sets(id,subject,set_name,source_filename,created_at) VALUES(?,?,?,?,?)', [tuple(r) for r in sets])

    questions = local.execute('SELECT id,set_id,subject,question_number,question_text,answer,explanation,created_at FROM expected_questions ORDER BY id').fetchall()
    deploy.executemany('INSERT INTO expected_questions(id,set_id,subject,question_number,question_text,answer,explanation,created_at) VALUES(?,?,?,?,?,?,?,?)', [tuple(r) for r in questions])

    choices = local.execute('SELECT id,question_id,choice_number,choice_text FROM expected_choices ORDER BY id').fetchall()
    deploy.executemany('INSERT INTO expected_choices(id,question_id,choice_number,choice_text) VALUES(?,?,?,?)', [tuple(r) for r in choices])

    deploy.execute('UPDATE sqlite_sequence SET seq=(SELECT COALESCE(MAX(id),0) FROM expected_question_sets) WHERE name="expected_question_sets"')
    deploy.execute('UPDATE sqlite_sequence SET seq=(SELECT COALESCE(MAX(id),0) FROM expected_questions) WHERE name="expected_questions"')
    deploy.execute('UPDATE sqlite_sequence SET seq=(SELECT COALESCE(MAX(id),0) FROM expected_choices) WHERE name="expected_choices"')
    deploy.commit()

    print('=' * 68)
    print('EXPECTED BANK SYNC COMPLETE')
    print('=' * 68)
    print('문제 세트:', len(sets))
    print('예상문제:', len(questions))
    print('선택지:', len(choices))
    print('대상:', DEPLOY)
    print('기존 시험문제/사용자/풀이기록/요약본은 건드리지 않았습니다.')
finally:
    local.close()
    deploy.close()
