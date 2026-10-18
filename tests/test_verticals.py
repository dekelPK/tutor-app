"""Tests for vertical (profession) config loading, the terminology helper, and
the users-table migration. Run: python -m unittest discover tests"""
import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import verticals  # noqa: E402


class VerticalConfigTests(unittest.TestCase):
    def test_all_verticals_load_with_required_keys(self):
        ids = verticals.available()
        self.assertIn('tutor', ids)
        self.assertIn('barber', ids)
        for vid in ids:
            cfg = verticals.load(vid)
            for key in verticals.REQUIRED_KEYS:
                self.assertIn(key, cfg, f'{vid} missing {key}')
            self.assertEqual(cfg['id'], vid)
            self.assertIn('f', cfg['label'])
            self.assertIn('m', cfg['label'])

    def test_every_vertical_covers_every_term_and_feature(self):
        base = verticals.load(verticals.DEFAULT_VERTICAL)
        for vid in verticals.available():
            cfg = verticals.load(vid)
            self.assertEqual(set(cfg['terms']) - set(base['terms']), set(),
                             f'{vid} has terms the default vertical lacks')
            self.assertTrue(set(base['terms']) <= set(cfg['terms']), f'{vid} lacks terms')
            self.assertEqual(set(cfg['features']), set(base['features']), f'{vid} feature flags differ')

    def test_shared_files_are_not_verticals(self):
        self.assertNotIn('_brand', verticals.available())
        self.assertTrue(verticals.brand()['name'])

    def test_defaults_and_fields_are_well_formed(self):
        for vid in verticals.available():
            cfg = verticals.load(vid)
            self.assertGreater(cfg['defaults']['price'], 0)
            self.assertGreater(cfg['defaults']['durationMinutes'], 0)
            for f in cfg['fields']:
                self.assertTrue({'key', 'label', 'type'} <= set(f), f)


class TerminologyTests(unittest.TestCase):
    def test_tutor_terms_match_current_app(self):
        # Existing users are tutors — their wording must not change.
        self.assertEqual(verticals.t('tutor', 'client'), 'תלמיד')
        self.assertEqual(verticals.t('tutor', 'clients'), 'תלמידים')
        self.assertEqual(verticals.t('tutor', 'appointment'), 'שיעור')
        self.assertEqual(verticals.t('tutor', 'appointments'), 'שיעורים')
        self.assertEqual(verticals.t('tutor', 'calendar_event', name='נועה'), 'שיעור - נועה')
        self.assertEqual(verticals.t('tutor', 'reminder_title'), 'תזכורת: יש לך שיעור היום 📚')

    def test_barber_terms(self):
        self.assertEqual(verticals.t('barber', 'client'), 'לקוח')
        self.assertEqual(verticals.t('barber', 'appointment'), 'תור')

    def test_gendered_terms(self):
        self.assertEqual(verticals.t('barber', 'provider', gender='f'), 'ספרית')
        self.assertEqual(verticals.t('barber', 'provider', gender='m'), 'ספר')
        self.assertEqual(verticals.t('barber', 'provider', gender='x'), 'ספרית')  # unknown → default gender

    def test_placeholders(self):
        self.assertEqual(verticals.t('tutor', 'reminder_one', name='דן', time='16:00'), 'שיעור עם דן בשעה 16:00')
        self.assertEqual(verticals.t('barber', 'reminder_many', count=3), '3 תורים מתוכננים היום')

    def test_unknown_key_and_vertical(self):
        self.assertEqual(verticals.t('tutor', 'no_such_term'), 'no_such_term')
        self.assertEqual(verticals.t('no_such_vertical', 'client'), 'תלמיד')
        self.assertEqual(verticals.normalize(None), 'tutor')

    def test_terms_bound_to_a_business(self):
        bt = verticals.Terms('barber', 'm')
        self.assertEqual(bt.t('appointment'), 'תור')
        self.assertEqual(bt.g('לחצי', 'לחץ'), 'לחץ')
        self.assertFalse(bt.has('exams'))
        old = verticals.Terms(None, None)  # a pre-migration row
        self.assertEqual((old.vertical, old.gender), ('tutor', 'f'))
        self.assertEqual(old.t('calendar_event', name='דן'), 'שיעור - דן')

    def test_feature_flags(self):
        self.assertTrue(verticals.has('tutor', 'exams'))
        self.assertFalse(verticals.has('barber', 'exams'))
        self.assertTrue(verticals.has('barber', 'packages'))
        self.assertFalse(verticals.has('tutor', 'no_such_feature'))


class PortalTests(unittest.TestCase):
    """The client portal follows the owner's vertical: wording and features."""

    def render(self, **kw):
        import portal_core
        from datetime import timedelta
        now = portal_core.now_local()
        d = lambda n: (now + timedelta(days=n)).strftime('%Y-%m-%d')
        student = {'id': 's1', 'name': 'נועה כהן'}
        lessons = [{'id': 'l1', 'studentId': 's1', 'date': d(-2), 'time': '16:00', 'topic': 'שברים', 'homework': 'עמוד 5'},
                   {'id': 'l2', 'studentId': 's1', 'date': d(1), 'time': '16:00'}]
        exams = [{'id': 'e1', 'studentId': 's1', 'date': d(2), 'subject': 'מתמטיקה'}]
        slots = [{'date': d(3), 'time': '10:00', 'maxUnits': 2}]
        return portal_core.render_portal('tok', student, lessons, [], [], [], slots, True,
                                         teacher_name='דנה', exams=exams, **kw)

    def test_tutor_portal_keeps_lessons_exams_and_homework(self):
        page = self.render()
        for text in ('השיעור הבא שלך', 'מבחן במתמטיקה', 'עמוד 5', 'לקבוע שיעור לבד', 'מה למדנו לאחרונה'):
            self.assertIn(text, page)

    def test_barber_portal_uses_its_terms_and_hides_tutor_features(self):
        page = self.render(vertical='barber', gender='m')
        for text in ('התור הבא שלך', 'לקבוע תור לבד', 'התורים הקודמים'):
            self.assertIn(text, page)
        for text in ('שיעור', 'תלמיד', 'מבחן', 'עמוד 5', 'שברים'):
            self.assertNotIn(text, page)


class MigrationAndApiTests(unittest.TestCase):
    """An existing pre-migration database must come out as (female) tutors."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        db = sqlite3.connect(os.path.join(cls.tmp.name, 'tutor.db'))
        db.execute('CREATE TABLE users (id TEXT PRIMARY KEY, email TEXT UNIQUE NOT NULL, password_hash TEXT NOT NULL, name TEXT, created_at TEXT)')
        from werkzeug.security import generate_password_hash
        db.execute("INSERT INTO users VALUES ('old1', 'old@x.com', ?, 'מורה ותיקה', '2025-01-01')", [generate_password_hash('secret123')])
        db.commit()
        db.close()
        os.environ['TUTOR_APP_BASE_DIR'] = cls.tmp.name
        sys.modules.pop('flask_app', None)
        cls.app_mod = importlib.import_module('flask_app')
        cls.client = cls.app_mod.app.test_client()

    @classmethod
    def tearDownClass(cls):
        os.environ.pop('TUTOR_APP_BASE_DIR', None)
        cls.tmp.cleanup()

    def test_existing_user_defaults_to_female_tutor(self):
        db = sqlite3.connect(os.path.join(self.tmp.name, 'tutor.db'))
        row = db.execute("SELECT profession, gender FROM users WHERE id='old1'").fetchone()
        db.close()
        self.assertEqual(row, ('tutor', 'f'))
        c = self.app_mod.app.test_client()
        r = c.post('/api/auth/login', json={'email': 'old@x.com', 'password': 'secret123'})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json['profession'], 'tutor')
        self.assertEqual(r.json['gender'], 'f')
        self.assertEqual(r.json['vertical']['terms']['client'], 'תלמיד')
        me = c.get('/api/auth/me').json
        self.assertEqual(me['profession'], 'tutor')

    def test_register_with_profession_and_gender_then_change(self):
        c = self.app_mod.app.test_client()
        r = c.post('/api/auth/register', json={'email': 'barber@x.com', 'password': 'secret123', 'name': 'דני', 'profession': 'barber', 'gender': 'm'})
        if r.status_code == 202:  # needs admin approval — approve directly
            db = sqlite3.connect(os.path.join(self.tmp.name, 'tutor.db'))
            db.execute("UPDATE users SET approved=1 WHERE email='barber@x.com'")
            db.commit()
            db.close()
            r = c.post('/api/auth/login', json={'email': 'barber@x.com', 'password': 'secret123'})
        self.assertEqual(r.json['profession'], 'barber')
        self.assertEqual(r.json['gender'], 'm')
        self.assertFalse(r.json['vertical']['features']['exams'])
        r = c.put('/api/account', json={'profession': 'tutor', 'gender': 'f'})
        self.assertEqual((r.json['profession'], r.json['gender']), ('tutor', 'f'))
        r = c.put('/api/account', json={'profession': 'hacker', 'gender': '?'})
        self.assertEqual((r.json['profession'], r.json['gender']), ('tutor', 'f'))

    def test_server_text_follows_the_owners_vertical(self):
        db = sqlite3.connect(os.path.join(self.tmp.name, 'tutor.db'))
        db.execute("INSERT INTO users (id, email, password_hash, name, profession, gender, cal_token) "
                   "VALUES ('brb', 'b2@x.com', 'x', 'יוסי', 'barber', 'm', 'caltok')")
        db.execute("INSERT INTO students (id, user_id, data) VALUES ('s9', 'brb', ?)", [json.dumps({'id': 's9', 'name': 'רון'})])
        db.execute("INSERT INTO lessons (id, user_id, studentId, date, data) VALUES ('l9', 'brb', 's9', '2030-01-01', ?)",
                   [json.dumps({'id': 'l9', 'studentId': 's9', 'date': '2030-01-01', 'time': '10:00'})])
        db.execute("INSERT INTO bookings (id, user_id, studentId, date, data) VALUES ('b9', 'brb', 's9', '2030-01-02', ?)",
                   [json.dumps({'id': 'b9', 'studentId': 's9', 'date': '2030-01-02', 'time': '11:00', 'units': 2})])
        db.commit()
        db.close()
        secret = self.app_mod.CRON_SECRET
        r = self.client.get(f'/api/push/booking-notice?token={secret}&booking_id=b9').json
        self.assertEqual(r['title'], '🗓️ בקשה חדשה לתור')
        self.assertIn('2 תורים ברצף', r['body'])
        self.assertIn('לחץ לאישור', r['body'])
        ics = self.client.get('/calendar.ics?token=caltok').get_data(as_text=True)
        self.assertIn('SUMMARY:תור - רון', ics)
        self.assertIn('X-WR-CALNAME:תורים', ics)

    def test_public_vertical_list_and_js(self):
        r = self.client.get('/api/verticals')
        ids = [v['id'] for v in r.json['verticals']]
        self.assertEqual(r.json['default'], 'tutor')
        self.assertEqual(ids[0], 'tutor')  # current profession is listed first
        self.assertIn('barber', ids)
        js = self.client.get('/vertical.js')
        self.assertEqual(js.status_code, 200)
        self.assertIn('setVertical', js.get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
