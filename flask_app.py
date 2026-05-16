#!/usr/bin/env python3
"""
flask_app.py — PythonAnywhere WSGI entry point
מסד נתונים: SQLite (קובץ tutor.db)
העלה קובץ זה ל: /home/DekelPA/mysite/flask_app.py
"""
from flask import Flask, jsonify, request
import sqlite3, json, os
from datetime import datetime

BASE_DIR = '/home/DekelPA/mysite'
DB_PATH  = os.path.join(BASE_DIR, 'tutor.db')

app = Flask(__name__, static_folder=BASE_DIR)

# ── DB helpers ───────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS students (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lessons (
                id TEXT PRIMARY KEY,
                studentId TEXT NOT NULL,
                date TEXT NOT NULL,
                data TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS payments (
                id TEXT PRIMARY KEY,
                studentId TEXT NOT NULL,
                date TEXT NOT NULL,
                data TEXT NOT NULL
            );
        ''')

    # מיגרציה חד-פעמית מ-data.json אם קיים
    json_path = os.path.join(BASE_DIR, 'data.json')
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            with get_db() as db:
                for s in data.get('students', []):
                    db.execute('INSERT OR IGNORE INTO students VALUES (?,?)',
                               [s['id'], json.dumps(s, ensure_ascii=False)])
                for l in data.get('lessons', []):
                    db.execute('INSERT OR IGNORE INTO lessons VALUES (?,?,?,?)',
                               [l['id'], l.get('studentId',''), l.get('date',''),
                                json.dumps(l, ensure_ascii=False)])
                for p in data.get('payments', []):
                    db.execute('INSERT OR IGNORE INTO payments VALUES (?,?,?,?)',
                               [p['id'], p.get('studentId',''), p.get('date',''),
                                json.dumps(p, ensure_ascii=False)])
            os.rename(json_path, json_path + '.migrated')
            print('✅ data.json מוגרד ל-SQLite בהצלחה')
        except Exception as e:
            print(f'⚠️  שגיאה במיגרציה: {e}')

init_db()

def rows_to_list(rows):
    return [json.loads(r['data']) for r in rows]

# ── Serve HTML ───────────────────────────────────────────────────────────────

@app.route('/')
def index():
    with open(os.path.join(BASE_DIR, 'tutor-app.html'), 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}

# ── Students ─────────────────────────────────────────────────────────────────

@app.route('/api/students', methods=['GET'])
def get_students():
    with get_db() as db:
        rows = db.execute('SELECT data FROM students').fetchall()
    return jsonify(rows_to_list(rows))

@app.route('/api/students', methods=['POST'])
def add_student():
    s = request.json
    with get_db() as db:
        db.execute('INSERT INTO students VALUES (?,?)',
                   [s['id'], json.dumps(s, ensure_ascii=False)])
    return jsonify(s), 201

@app.route('/api/students/<sid>', methods=['PUT'])
def update_student(sid):
    s = request.json
    with get_db() as db:
        db.execute('UPDATE students SET data=? WHERE id=?',
                   [json.dumps(s, ensure_ascii=False), sid])
    return jsonify(s)

@app.route('/api/students/<sid>', methods=['DELETE'])
def delete_student(sid):
    with get_db() as db:
        db.execute('DELETE FROM students WHERE id=?', [sid])
        db.execute('DELETE FROM lessons  WHERE studentId=?', [sid])
        db.execute('DELETE FROM payments WHERE studentId=?', [sid])
    return '', 204

# ── Lessons ──────────────────────────────────────────────────────────────────

@app.route('/api/lessons', methods=['GET'])
def get_lessons():
    with get_db() as db:
        rows = db.execute('SELECT data FROM lessons ORDER BY date').fetchall()
    return jsonify(rows_to_list(rows))

@app.route('/api/lessons', methods=['POST'])
def add_lesson():
    l = request.json
    with get_db() as db:
        db.execute('INSERT INTO lessons VALUES (?,?,?,?)',
                   [l['id'], l.get('studentId',''), l.get('date',''),
                    json.dumps(l, ensure_ascii=False)])
    return jsonify(l), 201

@app.route('/api/lessons/<lid>', methods=['PUT'])
def update_lesson(lid):
    l = request.json
    with get_db() as db:
        db.execute('UPDATE lessons SET studentId=?, date=?, data=? WHERE id=?',
                   [l.get('studentId',''), l.get('date',''),
                    json.dumps(l, ensure_ascii=False), lid])
    return jsonify(l)

@app.route('/api/lessons/<lid>', methods=['DELETE'])
def delete_lesson(lid):
    with get_db() as db:
        db.execute('DELETE FROM lessons WHERE id=?', [lid])
    return '', 204

# ── Payments ─────────────────────────────────────────────────────────────────

@app.route('/api/payments', methods=['GET'])
def get_payments():
    with get_db() as db:
        rows = db.execute('SELECT data FROM payments ORDER BY date').fetchall()
    return jsonify(rows_to_list(rows))

@app.route('/api/payments', methods=['POST'])
def add_payment():
    p = request.json
    with get_db() as db:
        db.execute('INSERT INTO payments VALUES (?,?,?,?)',
                   [p['id'], p.get('studentId',''), p.get('date',''),
                    json.dumps(p, ensure_ascii=False)])
    return jsonify(p), 201

@app.route('/api/payments/<pid>', methods=['DELETE'])
def delete_payment(pid):
    with get_db() as db:
        db.execute('DELETE FROM payments WHERE id=?', [pid])
    return '', 204

# ── ICS calendar feed ────────────────────────────────────────────────────────

@app.route('/calendar.ics')
def calendar_ics():
    with get_db() as db:
        students = {s['id']: s for s in rows_to_list(db.execute('SELECT data FROM students').fetchall())}
        lessons  = rows_to_list(db.execute('SELECT data FROM lessons ORDER BY date').fetchall())
    stamp = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    lines = [
        'BEGIN:VCALENDAR','VERSION:2.0',
        'PRODID:-//שיעורים פרטיים//HE','CALSCALE:GREGORIAN',
        'METHOD:PUBLISH','X-WR-CALNAME:שיעורים - הוראה',
        'X-WR-TIMEZONE:Asia/Jerusalem',
        'REFRESH-INTERVAL;VALUE=DURATION:PT1H',
    ]
    for l in lessons:
        s    = students.get(l['studentId'], {})
        name = s.get('name', 'תלמיד')
        date = l['date'].replace('-', '')
        hh, mm = map(int, (l.get('time','16:00')).split(':'))
        start_min = hh*60 + mm
        end_min   = start_min + int((l.get('durationHours',1) or 1)*60)
        sh,sm = str(start_min//60).zfill(2), str(start_min%60).zfill(2)
        eh,em = str((end_min//60)%24).zfill(2), str(end_min%60).zfill(2)
        lines += [
            'BEGIN:VEVENT',
            f'UID:{l["id"]}@tutor-app',
            f'DTSTAMP:{stamp}',
            f'DTSTART;TZID=Asia/Jerusalem:{date}T{sh}{sm}00',
            f'DTEND;TZID=Asia/Jerusalem:{date}T{eh}{em}00',
            f'SUMMARY:שיעור - {name}',
            f'DESCRIPTION:{l.get("notes","") or ""}',
            'END:VEVENT',
        ]
    lines.append('END:VCALENDAR')
    return '\r\n'.join(lines), 200, {
        'Content-Type': 'text/calendar; charset=utf-8',
        'Content-Disposition': 'inline; filename="שיעורים.ics"',
    }

# ── Import (Excel) ───────────────────────────────────────────────────────────

@app.route('/api/import', methods=['POST'])
def import_data():
    body = request.json
    mode = body.get('mode', 'merge')

    if mode == 'replace':
        with get_db() as db:
            db.execute('DELETE FROM students')
            db.execute('DELETE FROM lessons')
            db.execute('DELETE FROM payments')
            for s in body.get('students', []):
                db.execute('INSERT INTO students VALUES (?,?)',
                           [s['id'], json.dumps(s, ensure_ascii=False)])
            for l in body.get('lessons', []):
                db.execute('INSERT INTO lessons VALUES (?,?,?,?)',
                           [l['id'], l.get('studentId',''), l.get('date',''),
                            json.dumps(l, ensure_ascii=False)])
            for p in body.get('payments', []):
                db.execute('INSERT INTO payments VALUES (?,?,?,?)',
                           [p['id'], p.get('studentId',''), p.get('date',''),
                            json.dumps(p, ensure_ascii=False)])
    else:  # merge
        with get_db() as db:
            existing = {s['name']: s['id'] for s in rows_to_list(db.execute('SELECT data FROM students').fetchall())}
            id_map = {}
            for ns in body.get('students', []):
                if ns['name'] in existing:
                    id_map[ns['id']] = existing[ns['name']]
                    db.execute('UPDATE students SET data=json_patch(data, ?) WHERE id=?',
                               [json.dumps({'hourlyRate': ns['hourlyRate']}), existing[ns['name']]])
                else:
                    id_map[ns['id']] = ns['id']
                    db.execute('INSERT OR IGNORE INTO students VALUES (?,?)',
                               [ns['id'], json.dumps(ns, ensure_ascii=False)])
            existing_keys = {(r['studentId'], r['date']) for r in db.execute('SELECT studentId, date FROM lessons').fetchall()}
            for l in body.get('lessons', []):
                l['studentId'] = id_map.get(l['studentId'], l['studentId'])
                if (l['studentId'], l['date']) not in existing_keys:
                    db.execute('INSERT OR IGNORE INTO lessons VALUES (?,?,?,?)',
                               [l['id'], l['studentId'], l.get('date',''),
                                json.dumps(l, ensure_ascii=False)])

    return jsonify({'ok': True})
