#!/usr/bin/env python3
"""
flask_app.py — PythonAnywhere WSGI entry point
מסד נתונים: SQLite (tutor.db)
מיקום: /home/DekelPA/mysite/flask_app.py
"""
from flask import Flask, jsonify, request, session
import sqlite3, json, os, hashlib, secrets, uuid
from datetime import datetime, timedelta

BASE_DIR = '/home/DekelPA/mysite'
DB_PATH  = os.path.join(BASE_DIR, 'tutor.db')

app = Flask(__name__, static_folder=BASE_DIR)
app.permanent_session_lifetime = timedelta(days=30)

# ── Set to True only when you want to allow new registrations ─────────────────
REGISTRATION_OPEN = False

# ── Secret key (persist across restarts) ──────────────────────────────────────
_sk_file = os.path.join(BASE_DIR, '.secret_key')
if os.path.exists(_sk_file):
    app.secret_key = open(_sk_file).read().strip()
else:
    app.secret_key = secrets.token_hex(32)
    open(_sk_file, 'w').write(app.secret_key)

# ── DB helpers ────────────────────────────────────────────────────────────────
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as db:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS users (
                id            TEXT PRIMARY KEY,
                email         TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                name          TEXT,
                created_at    TEXT
            );
            CREATE TABLE IF NOT EXISTS students (
                id      TEXT PRIMARY KEY,
                user_id TEXT,
                data    TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS lessons (
                id        TEXT PRIMARY KEY,
                user_id   TEXT,
                studentId TEXT NOT NULL,
                date      TEXT NOT NULL,
                data      TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS payments (
                id        TEXT PRIMARY KEY,
                user_id   TEXT,
                studentId TEXT NOT NULL,
                date      TEXT NOT NULL,
                data      TEXT NOT NULL
            );
        ''')
        # Add user_id column to existing tables if missing (safe to run multiple times)
        for tbl in ('students', 'lessons', 'payments'):
            cols = [r[1] for r in db.execute(f'PRAGMA table_info({tbl})').fetchall()]
            if 'user_id' not in cols:
                db.execute(f'ALTER TABLE {tbl} ADD COLUMN user_id TEXT')

    # One-time migration from data.json if it exists
    json_path = os.path.join(BASE_DIR, 'data.json')
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            with get_db() as db:
                for s in data.get('students', []):
                    db.execute('INSERT OR IGNORE INTO students(id,data) VALUES (?,?)',
                               [s['id'], json.dumps(s, ensure_ascii=False)])
                for l in data.get('lessons', []):
                    db.execute('INSERT OR IGNORE INTO lessons(id,studentId,date,data) VALUES (?,?,?,?)',
                               [l['id'], l.get('studentId',''), l.get('date',''),
                                json.dumps(l, ensure_ascii=False)])
                for p in data.get('payments', []):
                    db.execute('INSERT OR IGNORE INTO payments(id,studentId,date,data) VALUES (?,?,?,?)',
                               [p['id'], p.get('studentId',''), p.get('date',''),
                                json.dumps(p, ensure_ascii=False)])
            os.rename(json_path, json_path + '.migrated')
        except Exception as e:
            print(f'Migration error: {e}')

init_db()

def rows_to_list(rows):
    return [json.loads(r['data']) for r in rows]

def hash_pw(pw):
    return hashlib.sha256(pw.encode('utf-8')).hexdigest()

def require_auth():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    return None

def uid():
    return session.get('user_id')

# ── Serve HTML ────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    with open(os.path.join(BASE_DIR, 'tutor-app.html'), 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}

# ── Auth ──────────────────────────────────────────────────────────────────────
@app.route('/api/auth/me')
def auth_me():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({'id': session['user_id'], 'name': session.get('user_name',''), 'email': session.get('user_email','')})

@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    body  = request.json or {}
    email = (body.get('email') or '').strip().lower()
    pw    = body.get('password', '')
    if not email or not pw:
        return jsonify({'error': 'נא למלא אימייל וסיסמה'}), 400
    with get_db() as db:
        user = db.execute('SELECT * FROM users WHERE email=? AND password_hash=?',
                          [email, hash_pw(pw)]).fetchone()
    if not user:
        return jsonify({'error': 'אימייל או סיסמה שגויים'}), 401
    session.permanent = True
    session['user_id']    = user['id']
    session['user_name']  = user['name']
    session['user_email'] = user['email']
    return jsonify({'id': user['id'], 'name': user['name'], 'email': user['email']})

@app.route('/api/auth/register', methods=['POST'])
def auth_register():
    if not REGISTRATION_OPEN:
        return jsonify({'error': 'ההרשמה סגורה. לפתיחת חשבון יש לפנות למנהל המערכת.'}), 403
    body  = request.json or {}
    email = (body.get('email') or '').strip().lower()
    pw    = body.get('password', '')
    name  = (body.get('name') or '').strip()
    if not email or not pw:
        return jsonify({'error': 'נא למלא אימייל וסיסמה'}), 400
    if len(pw) < 6:
        return jsonify({'error': 'הסיסמה חייבת להכיל לפחות 6 תווים'}), 400
    with get_db() as db:
        if db.execute('SELECT 1 FROM users WHERE email=?', [email]).fetchone():
            return jsonify({'error': 'כתובת האימייל כבר רשומה במערכת'}), 409
        is_first = db.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0
        new_id   = uuid.uuid4().hex[:12]
        db.execute('INSERT INTO users VALUES (?,?,?,?,?)',
                   [new_id, email, hash_pw(pw), name, datetime.now().isoformat()])
        if is_first:
            # Associate all existing data (no user_id) with this first user
            for tbl in ('students', 'lessons', 'payments'):
                db.execute(f'UPDATE {tbl} SET user_id=? WHERE user_id IS NULL', [new_id])
    session.permanent = True
    session['user_id']    = new_id
    session['user_name']  = name
    session['user_email'] = email
    return jsonify({'id': new_id, 'name': name, 'email': email}), 201

@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    session.clear()
    return '', 204

# ── Students ──────────────────────────────────────────────────────────────────
@app.route('/api/students', methods=['GET'])
def get_students():
    err = require_auth()
    if err: return err
    with get_db() as db:
        rows = db.execute('SELECT data FROM students WHERE user_id=?', [uid()]).fetchall()
    return jsonify(rows_to_list(rows))

@app.route('/api/students', methods=['POST'])
def add_student():
    err = require_auth()
    if err: return err
    s = request.json
    with get_db() as db:
        db.execute('INSERT INTO students(id,user_id,data) VALUES (?,?,?)',
                   [s['id'], uid(), json.dumps(s, ensure_ascii=False)])
    return jsonify(s), 201

@app.route('/api/students/<sid>', methods=['PUT'])
def update_student(sid):
    err = require_auth()
    if err: return err
    s = request.json
    with get_db() as db:
        db.execute('UPDATE students SET data=? WHERE id=? AND user_id=?',
                   [json.dumps(s, ensure_ascii=False), sid, uid()])
    return jsonify(s)

@app.route('/api/students/<sid>', methods=['DELETE'])
def delete_student(sid):
    err = require_auth()
    if err: return err
    with get_db() as db:
        db.execute('DELETE FROM students WHERE id=? AND user_id=?', [sid, uid()])
        db.execute('DELETE FROM lessons  WHERE studentId=? AND user_id=?', [sid, uid()])
        db.execute('DELETE FROM payments WHERE studentId=? AND user_id=?', [sid, uid()])
    return '', 204

# ── Lessons ───────────────────────────────────────────────────────────────────
@app.route('/api/lessons', methods=['GET'])
def get_lessons():
    err = require_auth()
    if err: return err
    with get_db() as db:
        rows = db.execute('SELECT data FROM lessons WHERE user_id=? ORDER BY date', [uid()]).fetchall()
    return jsonify(rows_to_list(rows))

@app.route('/api/lessons', methods=['POST'])
def add_lesson():
    err = require_auth()
    if err: return err
    l = request.json
    with get_db() as db:
        db.execute('INSERT INTO lessons(id,user_id,studentId,date,data) VALUES (?,?,?,?,?)',
                   [l['id'], uid(), l.get('studentId',''), l.get('date',''),
                    json.dumps(l, ensure_ascii=False)])
    return jsonify(l), 201

@app.route('/api/lessons/<lid>', methods=['PUT'])
def update_lesson(lid):
    err = require_auth()
    if err: return err
    l = request.json
    with get_db() as db:
        db.execute('UPDATE lessons SET studentId=?, date=?, data=? WHERE id=? AND user_id=?',
                   [l.get('studentId',''), l.get('date',''),
                    json.dumps(l, ensure_ascii=False), lid, uid()])
    return jsonify(l)

@app.route('/api/lessons/<lid>', methods=['DELETE'])
def delete_lesson(lid):
    err = require_auth()
    if err: return err
    with get_db() as db:
        db.execute('DELETE FROM lessons WHERE id=? AND user_id=?', [lid, uid()])
    return '', 204

# ── Payments ──────────────────────────────────────────────────────────────────
@app.route('/api/payments', methods=['GET'])
def get_payments():
    err = require_auth()
    if err: return err
    with get_db() as db:
        rows = db.execute('SELECT data FROM payments WHERE user_id=? ORDER BY date', [uid()]).fetchall()
    return jsonify(rows_to_list(rows))

@app.route('/api/payments', methods=['POST'])
def add_payment():
    err = require_auth()
    if err: return err
    p = request.json
    with get_db() as db:
        db.execute('INSERT INTO payments(id,user_id,studentId,date,data) VALUES (?,?,?,?,?)',
                   [p['id'], uid(), p.get('studentId',''), p.get('date',''),
                    json.dumps(p, ensure_ascii=False)])
    return jsonify(p), 201

@app.route('/api/payments/<pid>', methods=['DELETE'])
def delete_payment(pid):
    err = require_auth()
    if err: return err
    with get_db() as db:
        db.execute('DELETE FROM payments WHERE id=? AND user_id=?', [pid, uid()])
    return '', 204

# ── ICS calendar ──────────────────────────────────────────────────────────────
@app.route('/calendar.ics')
def calendar_ics():
    if 'user_id' not in session:
        return 'Unauthorized', 401
    with get_db() as db:
        students = {s['id']: s for s in rows_to_list(
            db.execute('SELECT data FROM students WHERE user_id=?', [uid()]).fetchall())}
        lessons  = rows_to_list(
            db.execute('SELECT data FROM lessons WHERE user_id=? ORDER BY date', [uid()]).fetchall())
    stamp = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR','VERSION:2.0','PRODID:-//שיעורים פרטיים//HE',
             'CALSCALE:GREGORIAN','METHOD:PUBLISH','X-WR-CALNAME:שיעורים - הוראה',
             'X-WR-TIMEZONE:Asia/Jerusalem','REFRESH-INTERVAL;VALUE=DURATION:PT1H']
    for l in lessons:
        s    = students.get(l['studentId'], {})
        name = s.get('name', 'תלמיד')
        date = l['date'].replace('-', '')
        hh, mm = map(int, (l.get('time','16:00')).split(':'))
        start_min = hh*60 + mm
        end_min   = start_min + int((l.get('durationHours',1) or 1)*60)
        sh,sm = str(start_min//60).zfill(2), str(start_min%60).zfill(2)
        eh,em = str((end_min//60)%24).zfill(2), str(end_min%60).zfill(2)
        lines += ['BEGIN:VEVENT', f'UID:{l["id"]}@tutor-app', f'DTSTAMP:{stamp}',
                  f'DTSTART;TZID=Asia/Jerusalem:{date}T{sh}{sm}00',
                  f'DTEND;TZID=Asia/Jerusalem:{date}T{eh}{em}00',
                  f'SUMMARY:שיעור - {name}',
                  f'DESCRIPTION:{l.get("notes","") or ""}', 'END:VEVENT']
    lines.append('END:VCALENDAR')
    return '\r\n'.join(lines), 200, {
        'Content-Type': 'text/calendar; charset=utf-8',
        'Content-Disposition': 'inline; filename="שיעורים.ics"',
    }

# ── Import (Excel) ────────────────────────────────────────────────────────────
@app.route('/api/import', methods=['POST'])
def import_data():
    err = require_auth()
    if err: return err
    body = request.json
    mode = body.get('mode', 'merge')
    with get_db() as db:
        if mode == 'replace':
            for tbl in ('students','lessons','payments'):
                db.execute(f'DELETE FROM {tbl} WHERE user_id=?', [uid()])
            for s in body.get('students', []):
                db.execute('INSERT INTO students(id,user_id,data) VALUES (?,?,?)',
                           [s['id'], uid(), json.dumps(s, ensure_ascii=False)])
            for l in body.get('lessons', []):
                db.execute('INSERT INTO lessons(id,user_id,studentId,date,data) VALUES (?,?,?,?,?)',
                           [l['id'], uid(), l.get('studentId',''), l.get('date',''),
                            json.dumps(l, ensure_ascii=False)])
            for p in body.get('payments', []):
                db.execute('INSERT INTO payments(id,user_id,studentId,date,data) VALUES (?,?,?,?,?)',
                           [p['id'], uid(), p.get('studentId',''), p.get('date',''),
                            json.dumps(p, ensure_ascii=False)])
        else:
            existing = {s['name']: s['id'] for s in rows_to_list(
                db.execute('SELECT data FROM students WHERE user_id=?', [uid()]).fetchall())}
            id_map = {}
            for ns in body.get('students', []):
                if ns['name'] in existing:
                    id_map[ns['id']] = existing[ns['name']]
                    db.execute('UPDATE students SET data=json_patch(data,?) WHERE id=? AND user_id=?',
                               [json.dumps({'hourlyRate': ns['hourlyRate']}), existing[ns['name']], uid()])
                else:
                    id_map[ns['id']] = ns['id']
                    db.execute('INSERT OR IGNORE INTO students(id,user_id,data) VALUES (?,?,?)',
                               [ns['id'], uid(), json.dumps(ns, ensure_ascii=False)])
            existing_keys = {(r['studentId'], r['date']) for r in
                             db.execute('SELECT studentId, date FROM lessons WHERE user_id=?', [uid()]).fetchall()}
            for l in body.get('lessons', []):
                l['studentId'] = id_map.get(l['studentId'], l['studentId'])
                if (l['studentId'], l['date']) not in existing_keys:
                    db.execute('INSERT OR IGNORE INTO lessons(id,user_id,studentId,date,data) VALUES (?,?,?,?,?)',
                               [l['id'], uid(), l['studentId'], l.get('date',''),
                                json.dumps(l, ensure_ascii=False)])
    return jsonify({'ok': True})
