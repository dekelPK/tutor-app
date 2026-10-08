#!/usr/bin/env python3
from flask import Flask, jsonify, request, session, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
import json, os, threading, webbrowser, sqlite3, hashlib, secrets, uuid
import portal_core
from datetime import datetime, timedelta

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
    if 'reset_token' not in cols:
        c.execute('ALTER TABLE users ADD COLUMN reset_token TEXT')
    if 'reset_token_expires' not in cols:
        c.execute('ALTER TABLE users ADD COLUMN reset_token_expires TEXT')
    c.commit()
    c.close()

init_db()

def hash_pw(pw):
    # Salted PBKDF2 (werkzeug default) — every new/rehashed password uses this.
    return generate_password_hash(pw)

def verify_pw(stored_hash, pw):
    if stored_hash and (stored_hash.startswith('pbkdf2:') or stored_hash.startswith('scrypt:')):
        return check_password_hash(stored_hash, pw)
    # Legacy unsalted SHA-256 from before the security upgrade — rehashed on
    # next successful login (see auth_login()), so this fades out over time.
    return stored_hash == hashlib.sha256(pw.encode('utf-8')).hexdigest()

# ── Per-user data files ────────────────────────────────────────────────────────
def user_data_file(uid):
    return os.path.join(BASE_DIR, f'data_{uid}.json')

def empty_data():
    return {'students': [], 'lessons': [], 'payments': [], 'packages': [], 'bookings': [], 'bookingSettings': None}

def read_data(uid=None):
    uid = uid or session.get('user_id')
    f = user_data_file(uid)
    if not os.path.exists(f):
        return empty_data()
    with open(f, encoding='utf-8') as fp:
        d = json.load(fp)
    # Files written before punch cards / self-booking existed lack these keys.
    for k, v in empty_data().items():
        d.setdefault(k, v)
    return d

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
    user = c.execute('SELECT * FROM users WHERE email=?', [email]).fetchone()
    if not user or not verify_pw(user['password_hash'], pw):
        c.close()
        return jsonify({'error': 'אימייל או סיסמה שגויים'}), 401
    if not (user['password_hash'].startswith('pbkdf2:') or user['password_hash'].startswith('scrypt:')):
        c.execute('UPDATE users SET password_hash=? WHERE id=?', [hash_pw(pw), user['id']])
        c.commit()
    c.close()
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

@app.route('/api/auth/forgot-password', methods=['POST'])
def auth_forgot_password():
    email = ((request.json or {}).get('email') or '').strip().lower()
    generic = jsonify({'message': 'אם כתובת האימייל קיימת במערכת, נשלח אליה קישור לאיפוס סיסמה.'})
    if not email:
        return generic
    c = get_db()
    user = c.execute('SELECT id FROM users WHERE email=?', [email]).fetchone()
    if not user:
        c.close()
        return generic
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(hours=1)).isoformat()
    c.execute('UPDATE users SET reset_token=?, reset_token_expires=? WHERE id=?', [token, expires, user['id']])
    c.commit()
    c.close()
    # Local dev has no email sending (same reason push-broadcast is a stub
    # here) — print the reset link so you can test the flow by hand.
    print(f'\n[local dev] password reset link: http://localhost:8080/?reset={token}\n')
    return generic

@app.route('/api/auth/reset-password', methods=['POST'])
def auth_reset_password():
    body  = request.json or {}
    token = (body.get('token') or '').strip()
    pw    = body.get('password', '')
    if not token:
        return jsonify({'error': 'קישור לא תקין'}), 400
    if len(pw) < 6:
        return jsonify({'error': 'הסיסמה חייבת להכיל לפחות 6 תווים'}), 400
    c = get_db()
    user = c.execute('SELECT id, reset_token_expires FROM users WHERE reset_token=?', [token]).fetchone()
    if not user or not user['reset_token_expires'] or datetime.fromisoformat(user['reset_token_expires']) < datetime.now():
        c.close()
        return jsonify({'error': 'הקישור פג תוקף או כבר נוצל. נא לבקש קישור חדש.'}), 400
    c.execute('UPDATE users SET password_hash=?, reset_token=NULL, reset_token_expires=NULL WHERE id=?',
              [hash_pw(pw), user['id']])
    c.commit()
    c.close()
    return jsonify({'ok': True})

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
    data['packages'] = [p for p in data['packages'] if p['studentId'] != sid]
    data['bookings'] = [b for b in data['bookings'] if b['studentId'] != sid]
    write_data(data)
    return '', 204

@app.route('/api/students/<sid>/share-link', methods=['GET'])
def student_share_link(sid):
    err = require_auth()
    if err: return err
    data = read_data()
    student = next((s for s in data['students'] if s['id'] == sid), None)
    if not student:
        return jsonify({'error': 'Not found'}), 404
    if not student.get('shareToken'):
        student['shareToken'] = secrets.token_urlsafe(20)
        write_data(data)
    return jsonify({'token': student['shareToken']})

# ── Parent/student portal (public, no login — found by share token) ─────────────
# Rendering, free-slot computation and punch-card math are in portal_core.py,
# shared with flask_app.py.
def find_by_token(token):
    """(owner_uid, data, student) for a share token, or (None, None, None)."""
    if not token:
        return None, None, None
    for fn in os.listdir(BASE_DIR):
        if fn.startswith('data_') and fn.endswith('.json'):
            owner = fn[len('data_'):-len('.json')]
            try:
                d = read_data(owner)
            except Exception:
                continue
            for s in d.get('students', []):
                if s.get('shareToken') == token:
                    return owner, d, s
    return None, None, None

def portal_free_slots(d, student):
    settings = portal_core.normalize_settings(d.get('bookingSettings'))
    return portal_core.compute_free_slots(settings, student, d['students'], d['lessons'], d['bookings'])

@app.route('/portal/<token>')
def student_portal(token):
    owner, d, student = find_by_token(token)
    if not student:
        return 'הקישור לא נמצא או שפג תוקפו', 404
    sid_ = student['id']
    mine = lambda key: [x for x in d.get(key, []) if x.get('studentId') == sid_]
    settings = portal_core.normalize_settings(d.get('bookingSettings'))
    booking_enabled = settings['enabled'] and student.get('isActive') is not False
    slots = portal_free_slots(d, student) if booking_enabled else []
    c = get_db()
    owner_row = c.execute('SELECT name FROM users WHERE id=?', [owner]).fetchone()
    c.close()
    page = portal_core.render_portal(token, student, mine('lessons'), mine('payments'),
                                     mine('packages'), mine('bookings'), slots, booking_enabled,
                                     teacher_name=(owner_row['name'] if owner_row else '') or '')
    return page, 200, {'Content-Type': 'text/html; charset=utf-8'}

@app.route('/portal/<token>/book', methods=['POST'])
def portal_book(token):
    owner, d, student = find_by_token(token)
    if not student:
        return jsonify({'error': 'הקישור לא נמצא'}), 404
    settings = portal_core.normalize_settings(d.get('bookingSettings'))
    if not settings['enabled'] or student.get('isActive') is False:
        return jsonify({'error': 'קביעת שיעורים דרך הקישור כבויה כרגע'}), 403
    pending = [b for b in d['bookings'] if b['studentId'] == student['id'] and b.get('status') == 'pending']
    if len(pending) >= portal_core.MAX_PENDING_PER_STUDENT:
        return jsonify({'error': f'אפשר להחזיק עד {portal_core.MAX_PENDING_PER_STUDENT} בקשות ממתינות בו-זמנית'}), 429
    try:
        date, time_, note = portal_core.validate_booking_request(request.json or {}, portal_free_slots(d, student))
    except ValueError as e:
        return jsonify({'error': str(e)}), 409
    d['bookings'].append({'id': uuid.uuid4().hex[:16], 'studentId': student['id'], 'date': date, 'time': time_,
                          'durationMinutes': int(student.get('lessonDurationMinutes') or 50), 'note': note,
                          'status': 'pending', 'createdAt': portal_core.now_local().isoformat(timespec='seconds')})
    write_data(d, owner)
    return jsonify({'ok': True}), 201

@app.route('/portal/<token>/cancel/<bid>', methods=['POST'])
def portal_cancel_booking(token, bid):
    owner, d, student = find_by_token(token)
    if not student:
        return jsonify({'error': 'הקישור לא נמצא'}), 404
    b = next((b for b in d['bookings'] if b['id'] == bid and b['studentId'] == student['id']), None)
    if not b:
        return jsonify({'error': 'Not found'}), 404
    if b.get('status') != 'pending':
        return jsonify({'error': 'הבקשה כבר טופלה'}), 409
    b['status'] = 'cancelled'
    b['decidedAt'] = portal_core.now_local().isoformat(timespec='seconds')
    write_data(d, owner)
    return jsonify({'ok': True})

# ── Booking settings & requests (teacher side) ───────────────────────────────────
@app.route('/api/booking-settings', methods=['GET'])
def get_booking_settings():
    err = require_auth()
    if err: return err
    return jsonify(portal_core.normalize_settings(read_data().get('bookingSettings')))

@app.route('/api/booking-settings', methods=['PUT'])
def put_booking_settings():
    err = require_auth()
    if err: return err
    data = read_data()
    data['bookingSettings'] = portal_core.normalize_settings(request.json or {})
    write_data(data)
    return jsonify(data['bookingSettings'])

@app.route('/api/bookings', methods=['GET'])
def get_bookings():
    err = require_auth()
    if err: return err
    return jsonify(read_data()['bookings'])

@app.route('/api/bookings/<bid>/approve', methods=['POST'])
def approve_booking(bid):
    err = require_auth()
    if err: return err
    lesson = (request.json or {}).get('lesson') or {}
    if not lesson.get('id'):
        return jsonify({'error': 'missing lesson'}), 400
    data = read_data()
    b = next((b for b in data['bookings'] if b['id'] == bid), None)
    if not b:
        return jsonify({'error': 'Not found'}), 404
    if b.get('status') != 'pending':
        return jsonify({'error': 'הבקשה כבר טופלה או בוטלה'}), 409
    data['lessons'].append(lesson)
    b.update(status='approved', lessonId=lesson['id'], decidedAt=portal_core.now_local().isoformat(timespec='seconds'))
    write_data(data)
    return jsonify({'booking': b, 'lesson': lesson})

@app.route('/api/bookings/<bid>/reject', methods=['POST'])
def reject_booking(bid):
    err = require_auth()
    if err: return err
    data = read_data()
    b = next((b for b in data['bookings'] if b['id'] == bid), None)
    if not b:
        return jsonify({'error': 'Not found'}), 404
    if b.get('status') != 'pending':
        return jsonify({'error': 'הבקשה כבר טופלה או בוטלה'}), 409
    b.update(status='rejected', rejectReason=str((request.json or {}).get('reason') or '').strip()[:300],
             decidedAt=portal_core.now_local().isoformat(timespec='seconds'))
    write_data(data)
    return jsonify(b)

# ── Punch cards (כרטיסיות) ───────────────────────────────────────────────────────
@app.route('/api/packages', methods=['GET'])
def get_packages():
    err = require_auth()
    if err: return err
    return jsonify(read_data()['packages'])

@app.route('/api/packages', methods=['POST'])
def add_package():
    err = require_auth()
    if err: return err
    data = read_data()
    data['packages'].append(request.json)
    write_data(data)
    return jsonify(request.json), 201

@app.route('/api/packages/<pid>', methods=['PUT'])
def update_package(pid):
    err = require_auth()
    if err: return err
    data = read_data()
    data['packages'] = [request.json if p['id'] == pid else p for p in data['packages']]
    write_data(data)
    return jsonify(request.json)

@app.route('/api/packages/<pid>', methods=['DELETE'])
def delete_package(pid):
    err = require_auth()
    if err: return err
    data = read_data()
    data['packages'] = [p for p in data['packages'] if p['id'] != pid]
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
    month_prefix = datetime.now().strftime('%Y-%m')
    lessons_month = 0
    for fn in os.listdir(BASE_DIR):
        if fn.startswith('data_') and fn.endswith('.json'):
            try:
                with open(os.path.join(BASE_DIR, fn), encoding='utf-8') as f:
                    lessons = json.load(f).get('lessons', [])
                lessons_month += sum(1 for l in lessons if l.get('date', '').startswith(month_prefix))
            except Exception:
                pass
    return jsonify({
        'activeUsers': active_users, 'pendingUsers': pending_users,
        'activeStudents': active_students, 'totalStudents': len(all_students),
        'lessonsThisMonth': lessons_month,
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
    user = c.execute('SELECT email, name FROM users WHERE id=?', [target_id]).fetchone()
    c.execute('UPDATE users SET approved=1 WHERE id=?', [target_id])
    c.commit(); c.close()
    if user:
        # Local dev has no email sending — see forgot-password for the same pattern.
        print(f"\n[local dev] would send approval email to {user['email']}\n")
    return jsonify({'ok': True})

@app.route('/api/admin/users/<target_id>/reject', methods=['POST'])
def admin_reject_user(target_id):
    err = require_admin()
    if err: return err
    c = get_db()
    c.execute('DELETE FROM users WHERE id=? AND approved=0', [target_id])
    c.commit(); c.close()
    return jsonify({'ok': True})

@app.route('/api/admin/users/<target_id>/suspend', methods=['POST'])
def admin_suspend_user(target_id):
    err = require_admin()
    if err: return err
    c = get_db()
    c.execute('UPDATE users SET approved=0 WHERE id=? AND email!=?', [target_id, ADMIN_EMAIL])
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
