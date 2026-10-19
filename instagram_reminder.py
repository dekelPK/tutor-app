"""
instagram_reminder.py — the weekly "time to make the Instagram content" nudge for the admin.
Shared by both backends. State lives in instagram_status.json next to the app:
  {"lastDone": ISO time the admin tapped "הכנו את השבוע", "lastReminder": YYYY-MM-DD}
A push goes out on Sunday morning (the GitHub workflow runs at 05:45 and 06:45 UTC so one
of the two lands after 08:00 Israel time whatever the DST); it is skipped if the week's
content was already marked done, and never sent twice on the same day.
"""
import json
import os
from datetime import datetime, timedelta

REMIND_WEEKDAY = 6   # Python: Monday=0 … Sunday=6
REMIND_FROM_HOUR = 8
RECENT_DONE_DAYS = 5

PUSH = {'title': '📸 תוכן האינסטגרם לשבוע הקרוב',
        'body': 'הגיע הזמן להכין את הפוסטים והרילסים של השבוע. לחצי לפתיחת הכרטיס בעמוד הניהול.',
        'url': '/?admin=instagram', 'tag': 'instagram-weekly'}


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
    """True when the Sunday reminder should be pushed right now."""
    if now.weekday() != REMIND_WEEKDAY or now.hour < REMIND_FROM_HOUR:
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
