#!/usr/bin/env python3
"""
send_exam_reminders.py — on the morning of a student's exam, pushes a
notification to the teacher's device(s); tapping it opens the app straight to
a ready-made "good luck" WhatsApp message for that student.

Run by .github/workflows/exam-reminders.yml at 05:00 and 06:00 UTC. The server
(/api/push/exams-due) only returns exams from 08:00 Israel time and marks each
one as handed out, so whichever run lands at/after 08:00 sends it, once.

Required environment variables (same secrets as the other push scripts):
  APP_URL, CRON_SECRET, VAPID_PRIVATE_KEY
"""
import os
import sys
import json
import requests
from pywebpush import webpush, WebPushException

APP_URL = os.environ['APP_URL'].rstrip('/')
CRON_SECRET = os.environ['CRON_SECRET']
VAPID_PRIVATE_KEY = os.environ['VAPID_PRIVATE_KEY']
VAPID_CLAIMS = {'sub': os.environ.get('VAPID_CONTACT_EMAIL', 'mailto:admin@example.com')}


def main():
    resp = requests.get(f'{APP_URL}/api/push/exams-due', params={'token': CRON_SECRET}, timeout=30)
    resp.raise_for_status()
    due = resp.json()
    print(f'{len(due)} exam reminder(s) to send')

    sent, failed = 0, 0
    for item in due:
        subscription_info = {
            'endpoint': item['endpoint'],
            'keys': {'p256dh': item['p256dh'], 'auth': item['auth']},
        }
        payload = json.dumps({'title': item['title'], 'body': item['body'], 'url': item['url'], 'tag': item['tag']})
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=dict(VAPID_CLAIMS),
                # TTL=0 (pywebpush's default) gets dropped by Apple if the iPhone is asleep.
                ttl=12 * 3600,
                headers={'Urgency': 'high'},
            )
            sent += 1
        except WebPushException as e:
            failed += 1
            status = e.response.status_code if e.response is not None else None
            print(f'  failed ({status}): {e}')
            if status in (404, 410):
                requests.delete(
                    f'{APP_URL}/api/push/due-today',
                    params={'token': CRON_SECRET},
                    json={'endpoint': item['endpoint']},
                    timeout=15,
                )

    print(f'done: {sent} sent, {failed} failed')
    if failed and not sent:
        sys.exit(1)


if __name__ == '__main__':
    main()
