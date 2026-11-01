"""
instagram_reminder.py — the monthly "time to make next month's Instagram content" nudge for the admin.
Shared by both backends. State lives in instagram_status.json next to the app:
  {"lastDone": ISO time the admin tapped "הכנו את החודש", "lastReminder": YYYY-MM-DD}
A push goes out on the 25th of the month (the GitHub workflow runs daily at 05:45 and 06:45 UTC so
one of the two lands after 08:00 Israel time whatever the DST); it is skipped if next month's
content was already marked done, and never sent twice on the same day.
"""
import json
import os
from datetime import datetime, timedelta

REMIND_DAY = 25      # day of the month
REMIND_FROM_HOUR = 8
RECENT_DONE_DAYS = 20

PUSH = {'title': '📸 תוכן האינסטגרם לחודש הבא',
        'body': 'הגיע הזמן להכין את הפוסטים, הרילסים והסטוריז של החודש הבא. לחצי לפתיחת הכרטיס בעמוד הניהול.',
        'url': '/?admin=instagram', 'tag': 'instagram-monthly'}


def load(path):
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(path, data):
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)


def mark_done(path, now):
    data = load(path)
    data['lastDone'] = now.isoformat(timespec='seconds')
    save(path, data)
    return data


def due(status, now):
    """True when the monthly reminder should be pushed right now."""
    if now.day != REMIND_DAY or now.hour < REMIND_FROM_HOUR:
        return False
    if status.get('lastReminder') == now.date().isoformat():
        return False
    done = status.get('lastDone')
    if done and datetime.fromisoformat(done) > now - timedelta(days=RECENT_DONE_DAYS):
        return False
    return True


def claim(path, now):
    """If a reminder is due, record it as sent and return True (so two runs can't both send)."""
    status = load(path)
    if not due(status, now):
        return False
    status['lastReminder'] = now.date().isoformat()
    save(path, status)
    return True
