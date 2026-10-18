"""
demo_accounts.py — test accounts the admin can create from the admin screen,
one per profession, filled with sample clients, appointments, a punch card and
a pending booking request so every screen has something to show.

Shared by flask_app.py (SQLite tables) and server.py (JSON file): build()
returns plain records in the same shapes the app stores.
"""
import secrets
import uuid
from datetime import date, timedelta

import verticals

CLIENTS = [('נועה כהן', '#8b5cf6', '0501234567'), ('דן לוי', '#10b981', '0521112233'),
           ('יעל ברק', '#f59e0b', ''), ('איתי שמש', '#ef4444', '0549998877'), ('מאיה רון', '#0891b2', '')]
TIMES = ['10:00', '12:30', '16:00', '18:00']


def email_for(profession):
    # A fixed address per profession, so creating it again resets the same account.
    return f'demo.{verticals.normalize(profession)}@luzi.test'


def new_password():
    return 'luzi-' + secrets.token_hex(3)


def build(profession, today=None):
    """Sample data for a fresh account of this profession (records as the app stores them)."""
    profession = verticals.normalize(profession)
    today = today or date.today()
    cfg = verticals.load(profession)
    price, minutes = cfg['defaults']['price'], cfg['defaults']['durationMinutes']
    day = lambda n: (today + timedelta(days=n)).isoformat()
    nid = lambda: uuid.uuid4().hex[:12]
    has = lambda feature: verticals.has(profession, feature)

    students = [{'id': nid(), 'name': name, 'color': color, 'phone': phone, 'hourlyRate': price,
                 'lessonDurationMinutes': minutes, 'scheduledDays': [], 'isActive': True, 'notes': '',
                 'createdAt': day(-45) + 'T09:00:00'} for name, color, phone in CLIENTS]

    services = verticals.services_for(profession, None) if has('services') else []
    lessons = []
    for n in range(-35, 15):
        for i, s in enumerate(students):
            if (n + i * 3) % 7:
                continue
            paid = n < -7 or (n < 0 and i % 2 == 0)
            lesson = {'id': nid(), 'studentId': s['id'], 'date': day(n), 'time': TIMES[(n + i) % len(TIMES)],
                      'durationHours': 1, 'amount': price, 'isPaid': paid, 'paidAmount': price if paid else 0,
                      'notes': '', 'topic': 'חזרה על החומר' if has('lessonNotes') and n < 0 else '', 'homework': ''}
            if services:  # each client has a usual service
                sv = services[i % len(services)]
                lesson.update(serviceId=sv['id'], serviceName=sv['name'], unitMinutes=sv['durationMinutes'],
                              amount=sv['price'], paidAmount=sv['price'] if paid else 0)
            lessons.append(lesson)

    packages = []
    if has('packages'):
        packages.append({'id': nid(), 'studentId': students[1]['id'], 'date': day(-10), 'totalUnits': 10,
                         'price': price * 9, 'expiresAt': day(170), 'notes': ''})

    exams = []
    if has('exams'):
        exams.append({'id': nid(), 'studentId': students[0]['id'], 'date': day(2), 'subject': 'מתמטיקה', 'notes': ''})

    bookings, settings = [], None
    if has('selfBooking'):
        settings = {'enabled': True, 'windows': [{'day': d, 'start': '09:00', 'end': '19:00'} for d in range(5)],
                    'extraSlots': [], 'blockedDates': [], 'minNoticeHours': 2, 'horizonDays': 21, 'stepMinutes': 30}
        bookings.append({'id': nid(), 'studentId': students[2]['id'], 'date': day(3), 'time': '11:00', 'units': 1,
                         'durationMinutes': minutes, 'note': 'אפשר בשעה הזאת?', 'status': 'pending',
                         'createdAt': day(0) + 'T08:00:00'})

    return {'students': students, 'lessons': lessons, 'payments': [], 'packages': packages,
            'bookings': bookings, 'exams': exams, 'bookingSettings': settings}
