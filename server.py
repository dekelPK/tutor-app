#!/usr/bin/env python3
from flask import Flask, jsonify, request, session, send_from_directory
import json, os, threading, webbrowser, sqlite3, hashlib, secrets, uuid
from datetime import datetime

BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
USERS_DB  = os.path.join(BASE_DIR, 'users.db')
DATA_FILE = os.path.join(BASE_DIR, 'data.json')   # legacy – migrated to first user

app = Flask(__name__, static_folder=BASE_DIR)

# ── Set to True only when you want to allow new registrations ─────────────────
REGISTRATION_OPEN = True

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
    cols = [r[1] for r in c.execute('PRAGMA table_info(users)').fetchall()]
    if 'cal_token' not in cols:
        c.execute('ALTER TABLE users ADD COLUMN cal_token TEXT')
    if 'approved' not in cols:
        c.execute('ALTER TABLE users ADD COLUMN approved INTEGER DEFAULT 1')
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

ADMIN_EMAIL = 'dekelkartel@gmail.com'
def require_admin():
    err = require_auth()
    if err: return err
    if (session.get('user_email') or '').lower() != ADMIN_EMAIL.lower():
        return jsonify({'error': 'Forbidden'}), 403
    return None

# ── Serve HTML ─────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    with open(os.path.join(BASE_DIR, 'tutor-app.html'), encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'text/html; charset=utf-8'}

# ── PWA static files (manifest/icons/service worker) ────────────────────────────
# Note: push-notification API routes are production-only (see flask_app.py) since
# they need the real public APP_URL the GitHub Actions cron job calls. Locally the
# bell button will 404 if clicked — everything else works the same.
@app.route('/manifest.json')
def web_manifest():
    with open(os.path.join(BASE_DIR, 'manifest.json'), 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'application/manifest+json; charset=utf-8'}

@app.route('/icons/<path:filename>')
def app_icons(filename):
    return send_from_directory(os.path.join(BASE_DIR, 'icons'), filename)

@app.route('/sw.js')
def service_worker():
    with open(os.path.join(BASE_DIR, 'sw.js'), 'r', encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'application/javascript; charset=utf-8'}

# ── Auth endpoints ─────────────────────────────────────────────────────────────
@app.route('/api/auth/me')
def auth_me():
    if 'user_id' not in session:
        return jsonify({'error': 'Unauthorized'}), 401
    return jsonify({
        'id':    session['user_id'],
        'name':  session.get('user_name', ''),
        'email': session.get('user_email', ''),
        'isAdmin': (session.get('user_email') or '').lower() == ADMIN_EMAIL.lower(),
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
    if not user['approved']:
        return jsonify({'error': 'החשבון שלך ממתין לאישור מנהל המערכת'}), 403
    session['user_id']    = user['id']
    session['user_name']  = user['name']
    session['user_email'] = user['email']
    return jsonify({'id': user['id'], 'name': user['name'], 'email': user['email'],
                     'isAdmin': user['email'].lower() == ADMIN_EMAIL.lower()})

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
    is_admin_account = email.lower() == ADMIN_EMAIL.lower()
    c = get_db()
    if c.execute('SELECT 1 FROM users WHERE email=?', [email]).fetchone():
        c.close()
        return jsonify({'error': 'כתובת האימייל כבר רשומה במערכת'}), 409
    # First user gets the legacy data.json migrated automatically
    is_first = c.execute('SELECT COUNT(*) FROM users').fetchone()[0] == 0
    uid = uuid.uuid4().hex[:12]
    approved = 1 if (is_first or is_admin_account) else 0
    c.execute('INSERT INTO users (id,email,password_hash,name,created_at,approved) VALUES (?,?,?,?,?,?)',
              [uid, email, hash_pw(pw), name, datetime.now().isoformat(), approved])
    c.commit()
    c.close()
    if is_first and os.path.exists(DATA_FILE):
        with open(DATA_FILE, encoding='utf-8') as f:
            legacy = json.load(f)
        write_data(legacy, uid)
    else:
        write_data(empty_data(), uid)
    if not approved:
        return jsonify({'pending': True,
                         'message': 'ההרשמה התקבלה! החשבון ימתין לאישור מנהל המערכת לפני שתוכל/י להתחבר.'}), 202
    session['user_id']    = uid
    session['user_name']  = name
    session['user_email'] = email
    return jsonify({'id': uid, 'name': name, 'email': email,
                     'isAdmin': is_admin_account}), 201

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

# ── Announcements (in-app banner) ────────────────────────────────────────────
@app.route('/api/announcement')
def get_announcement():
    err = require_auth()
    if err: return err
    ann_path = os.path.join(BASE_DIR, 'announcement.json')
    if not os.path.exists(ann_path):
        return jsonify(None)
    with open(ann_path, encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'application/json; charset=utf-8'}

# ── Admin screen (announcement only — push broadcast needs the real deployed
# GitHub Action, not meaningful against this local dev server) ─────────────────
ANN_PATH = os.path.join(BASE_DIR, 'announcement.json')

@app.route('/api/admin/announcement', methods=['GET'])
def admin_get_announcement():
    err = require_admin()
    if err: return err
    if not os.path.exists(ANN_PATH):
        return jsonify(None)
    with open(ANN_PATH, encoding='utf-8') as f:
        return f.read(), 200, {'Content-Type': 'application/json; charset=utf-8'}

@app.route('/api/admin/announcement', methods=['POST'])
def admin_set_announcement():
    err = require_admin()
    if err: return err
    message = (request.json or {}).get('message', '').strip()
    if not message:
        if os.path.exists(ANN_PATH):
            os.remove(ANN_PATH)
        return jsonify({'ok': True, 'cleared': True})
    ann = {'id': datetime.now().isoformat(), 'message': message}
    with open(ANN_PATH, 'w', encoding='utf-8') as f:
        json.dump(ann, f, ensure_ascii=False)
    return jsonify({'ok': True, 'announcement': ann})

@app.route('/api/admin/push-broadcast', methods=['POST'])
def admin_push_broadcast():
    err = require_admin()
    if err: return err
    return jsonify({'error': 'שידור Push לא נתמך בשרת המקומי — רק בפרודקשן', 'manual_fallback': True}), 503

@app.route('/api/admin/stats')
def admin_stats():
    err = require_admin()
    if err: return err
    c = get_db()
    active_users  = c.execute('SELECT COUNT(*) FROM users WHERE approved=1').fetchone()[0]
    pending_users = c.execute('SELECT COUNT(*) FROM users WHERE approved=0').fetchone()[0]
    c.close()
    all_students = []
    for fn in os.listdir(BASE_DIR):
        if fn.startswith('data_') and fn.endswith('.json'):
            try:
                with open(os.path.join(BASE_DIR, fn), encoding='utf-8') as f:
                    all_students += json.load(f).get('students', [])
            except Exception:
                pass
    active_students = sum(1 for s in all_students if s.get('isActive') is not False)
    return jsonify({
        'activeUsers': active_users, 'pendingUsers': pending_users,
        'activeStudents': active_students, 'totalStudents': len(all_students),
    })

@app.route('/api/admin/pending-users')
def admin_pending_users():
    err = require_admin()
    if err: return err
    c = get_db()
    rows = c.execute('SELECT id, name, email, created_at FROM users WHERE approved=0 ORDER BY created_at').fetchall()
    c.close()
    return jsonify([dict(r) for r in rows])

@app.route('/api/admin/users')
def admin_users():
    err = require_admin()
    if err: return err
    c = get_db()
    rows = c.execute('SELECT id, name, email, created_at FROM users WHERE approved=1 ORDER BY created_at DESC').fetchall()
    c.close()
    out = []
    for u in rows:
        d = dict(u)
        data = read_data(u['id'])
        d['studentCount'] = len(data.get('students', []))
        d['activeStudentCount'] = sum(1 for s in data.get('students', []) if s.get('isActive') is not False)
        d['lessonCount'] = len(data.get('lessons', []))
        d['isAdmin'] = u['email'].lower() == ADMIN_EMAIL.lower()
        out.append(d)
    return jsonify(out)

@app.route('/api/admin/users/<target_id>/students')
def admin_user_students(target_id):
    err = require_admin()
    if err: return err
    data = read_data(target_id)
    out = [{'id': s.get('id'), 'name': s.get('name'), 'isActive': s.get('isActive') is not False}
           for s in data.get('students', [])]
    return jsonify(out)

@app.route('/api/admin/users/<target_id>/approve', methods=['POST'])
def admin_approve_user(target_id):
    err = require_admin()
    if err: return err
    c = get_db()
    c.execute('UPDATE users SET approved=1 WHERE id=?', [target_id])
    c.commit(); c.close()
    return jsonify({'ok': True})

@app.route('/api/admin/users/<target_id>/reject', methods=['POST'])
def admin_reject_user(target_id):
    err = require_admin()
    if err: return err
    c = get_db()
    c.execute('DELETE FROM users WHERE id=? AND approved=0', [target_id])
    c.commit(); c.close()
    return jsonify({'ok': True})

# ── Live ICS calendar feed ─────────────────────────────────────────────────────
@app.route('/api/calendar-token')
def get_calendar_token():
    err = require_auth()
    if err: return err
    with get_db() as c:
        row = c.execute('SELECT cal_token FROM users WHERE id=?', [session['user_id']]).fetchone()
        token = row['cal_token'] if row else None
        if not token:
            token = secrets.token_urlsafe(24)
            c.execute('UPDATE users SET cal_token=? WHERE id=?', [token, session['user_id']])
            c.commit()
    return jsonify({'token': token})

@app.route('/calendar.ics')
def calendar_ics():
    # External calendar apps subscribing by URL never send our session
    # cookie, so they authenticate via ?token=... instead (see above).
    cal_user_id = session.get('user_id')
    if not cal_user_id:
        token = request.args.get('token')
        if token:
            with get_db() as c:
                row = c.execute('SELECT id FROM users WHERE cal_token=?', [token]).fetchone()
                cal_user_id = row['id'] if row else None
    if not cal_user_id:
        return 'Unauthorized', 401
    data     = read_data(cal_user_id)
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
        lesson_minutes = s.get('lessonDurationMinutes') or 50
        end_min   = start_min + int((l.get('durationHours', 1) or 1) * lesson_minutes)
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
        # ASCII only — a raw Hebrew filename here crashed the response mid-stream.
        'Content-Disposition': 'inline; filename="lessons.ics"',
    }

# ── Run ────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    def open_browser():
        webbrowser.open('http://localhost:8080')
    threading.Timer(1.0, open_browser).start()
    print('\n✅  שרת רץ על http://localhost:8080')
    print('   לעצירה: Ctrl+C\n')
    app.run(host='0.0.0.0', port=8080, debug=False)
