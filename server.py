#!/usr/bin/env python3
from flask import Flask, jsonify, request, session
import json, os, threading, webbrowser, sqlite3, hashlib, secrets, uuid
from datetime import datetime

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
USERS_DB  = os.path.join(BASE_DIR, 'users.db')
DATA_FILE = os.path.join(BASE_DIR, 'data.json')   # legacy – migrated to first user

app = Flask(__name__, static_folder=BASE_DIR)

# ── Set to True only when you want to allow new registrations ─────────────────
REGISTRATION_OPEN = False

# ── Persistent secret key ──────────────────────────────────────────────────────
_sk_file = os.path.join(BASE_DIR, '.secret_key')
if os.path.exists(_sk_file):
    app.secret_key = open(_sk_file).read().strip()
else:
    app.secret_key = secrets.token_hex(32)
    open(_sk_file, 'w').write(app.secret_key)

# ── SQLite – users ─────────────────────────────────────────────────────────────
def get_db():
    c = sqlite3.connect(USERS_DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = get_db()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
        id            TEXT PRIMARY KEY,
        email         TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        name          TEXT,
        created_at    TEXT
    )''')
    c.commit()
    c.close()

init_db()

def hash_pw(pw):
    return hashlib.sha256(pw.encode('utf-8')).hexdigest()

# ── Per-user data files ────────────────────────────────────────────────────────
def user_data_file(uid):
    return os.path.join(BASE_DIR, f'data_{uid}.json')

def empty_data():
    return {'students': [], 'lessons': [], 'payments': []}

def read_data(uid=None):
    uid = uid or session.get('user_id')
    f = user_data_file(uid)
    if not os.path.exists(f):
        return empty_data()
    with open(f, encoding='utf-8') as fp:
        return json.load(fp)

def write_data(data, uid=None):
    uid = uid or session.get('user_id')
    with open(user_data_file(uid), 'w', encoding='utf-8') as fp:
        json.dump(data, fp, ensure_ascii=False, indent=2)

# ── Auth helper ────────────────────────────────────────────────────────────────
def require_auth():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    return None

# ── Serve HTML ─────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    with open(os.path.join(BASE_DIR, 'tutor-app.html'), encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}

# ── Auth endpoints ─────────────────────────────────────────────────────────────
@app.route('/api/auth/me')
def auth_me():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({
        'id':    session['user_id'],
        'name':  session.get('user_name', ''),
        'email': session.get('user_email', ''),
    })

@app.route('/api/auth/login', methods=['POST'])
def auth_login():
    body  = request.json or {}
    email = (body.get('email') or '').strip().lower()
    pw    = body.get('password', '')
    if not email or not pw:
        return jsonify({'error': 'נא למלא אימייל וסיסמה'}), 400
    c    = get_db()
    user = c.execute(
        'SELECT * FROM users WHERE email=? AND password_hash=?',
        [email, hash_pw(pw)]
    ).fetchone()
    c.close()
    if not user:
        return jsonify({'error': 'אימייל או סיסמה שגויים'}), 401
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
    c = get_db()
    if c.execute('SELECT 1 FROM users WHERE email=?', [email]).fetchone():
        c.close()
        return jsonify({'error': 'כתובת האימייל כבר רשומה במערכת'}), 409
    # First user gets the legacy data.json migrated automatically
    is_first = c.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0
    uid = uuid.uuid4().hex[:12]
    c.execute('INSERT INTO users VALUES (?,?,?,?,?)',
              [uid, email, hash_pw(pw), name, datetime.now().isoformat()])
    c.commit()
    c.close()
    if is_first and os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding='utf-8') as f:
            legacy = json.load(f)
        write_data(legacy, uid)
    else:
        write_data(empty_data(), uid)
    session['user_id']    = uid
    session['user_name']  = name
    session['user_email'] = email
    return jsonify({'id': uid, 'name': name, 'email': email}), 201

@app.route('/api/auth/logout', methods=['POST'])
def auth_logout():
    session.clear()
    return '', 204

# ── Students ───────────────────────────────────────────────────────────────────
@app.route('/api/students', methods=['GET'])
def get_students():
    err = require_auth()
    if err: return err
    return jsonify(read_data()['students'])

@app.route('/api/students', methods=['POST'])
def add_student():
    err = require_auth()
    if err: return err
    data = read_data()
    student = request.json
    data['students'].append(student)
    write_data(data)
    return jsonify(student), 201

@app.route('/api/students/<sid>', methods=['PUT'])
def update_student(sid):
    err = require_auth()
    if err: return err
    data = read_data()
    data['students'] = [request.json if s['id'] == sid else s for s in data['students']]
    write_data(data)
    return jsonify(request.json)

@app.route('/api/students/<sid>', methods=['DELETE'])
def delete_student(sid):
    err = require_auth()
    if err: return err
    data = read_data()
    data['students'] = [s for s in data['students'] if s['id'] != sid]
    data['lessons']  = [l for l in data['lessons']  if l['studentId'] != sid]
    data['payments'] = [p for p in data['payments'] if p['studentId'] != sid]
    write_data(data)
    return '', 204

# ── Lessons ────────────────────────────────────────────────────────────────────
@app.route('/api/lessons', methods=['GET'])
def get_lessons():
    err = require_auth()
    if err: return err
    return jsonify(read_data()['lessons'])

@app.route('/api/lessons', methods=['POST'])
def add_lesson():
    err = require_auth()
    if err: return err
    data = read_data()
    lesson = request.json
    data['lessons'].append(lesson)
    write_data(data)
    return jsonify(lesson), 201

@app.route('/api/lessons/<lid>', methods=['PUT'])
def update_lesson(lid):
    err = require_auth()
    if err: return err
    data = read_data()
    data['lessons'] = [request.json if l['id'] == lid else l for l in data['lessons']]
    write_data(data)
    return jsonify(request.json)

@app.route('/api/lessons/<lid>', methods=['DELETE'])
def delete_lesson(lid):
    err = require_auth()
    if err: return err
    data = read_data()
    data['lessons'] = [l for l in data['lessons'] if l['id'] != lid]
    write_data(data)
    return '', 204

# ── Payments ───────────────────────────────────────────────────────────────────
@app.route('/api/payments', methods=['GET'])
def get_payments():
    err = require_auth()
    if err: return err
    return jsonify(read_data()['payments'])

@app.route('/api/payments', methods=['POST'])
def add_payment():
    err = require_auth()
    if err: return err
    data = read_data()
    payment = request.json
    data['payments'].append(payment)
    write_data(data)
    return jsonify(payment), 201

@app.route('/api/payments/<pid>', methods=['DELETE'])
def delete_payment(pid):
    err = require_auth()
    if err: return err
    data = read_data()
    data['payments'] = [p for p in data['payments'] if p['id'] != pid]
    write_data(data)
    return '', 204

# ── Live ICS calendar feed ─────────────────────────────────────────────────────
@app.route('/calendar.ics')
def calendar_ics():
    if 'user_id' not in session:
        return 'Unauthorized', 401
    data     = read_data()
    students = {s['id']: s for s in data['students']}
    stamp    = datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    lines = [
        'BEGIN:VCALENDAR', 'VERSION:2.0',
        'PRODID:-//שיעורים פרטיים//HE', 'CALSCALE:GREGORIAN',
        'METHOD:PUBLISH', 'X-WR-CALNAME:שיעורים - הוראה',
        'X-WR-TIMEZONE:Asia/Jerusalem',
        'REFRESH-INTERVAL;VALUE=DURATION:PT1H',
    ]
    for l in data['lessons']:
        s    = students.get(l['studentId'], {})
        name = s.get('name', 'תלמיד')
        date = l['date'].replace('-', '')
        hh, mm = map(int, (l.get('time', '16:00')).split(':'))
        start_min = hh * 60 + mm
        end_min   = start_min + int((l.get('durationHours', 1) or 1) * 50)
        sh = str(start_min // 60).zfill(2)
        sm = str(start_min % 60).zfill(2)
        eh = str((end_min // 60) % 24).zfill(2)
        em = str(end_min % 60).zfill(2)
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

# ── Excel import ───────────────────────────────────────────────────────────────
@app.route('/api/import', methods=['POST'])
def import_data():
    err = require_auth()
    if err: return err
    body     = request.json
    mode     = body.get('mode', 'merge')
    incoming = {k: body[k] for k in ('students', 'lessons', 'payments') if k in body}
    if mode == 'replace':
        write_data(incoming)
    else:
        data = read_data()
        existing_names = {s['name']: s['id'] for s in data['students']}
        id_map = {}
        for ns in incoming.get('students', []):
            if ns['name'] in existing_names:
                id_map[ns['id']] = existing_names[ns['name']]
                data['students'] = [
                    {**s, 'hourlyRate': ns['hourlyRate']}
                    if s['id'] == existing_names[ns['name']] else s
                    for s in data['students']
                ]
            else:
                id_map[ns['id']] = ns['id']
                data['students'].append(ns)
        existing_keys = {(l['studentId'], l['date']) for l in data['lessons']}
        for l in incoming.get('lessons', []):
            l['studentId'] = id_map.get(l['studentId'], l['studentId'])
            if (l['studentId'], l['date']) not in existing_keys:
                data['lessons'].append(l)
        write_data(data)
    return jsonify({'ok': True})

# ── Run ────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    def open_browser():
        webbrowser.open('http://localhost:8080')
    threading.Timer(1.0, open_browser).start()
    print('\n✅  שרת רץ על http://localhost:8080')
    print('   לעצירה: Ctrl+C\n')
    app.run(host='0.0.0.0', port=8080, debug=False)
