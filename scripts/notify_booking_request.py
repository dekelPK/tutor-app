#!/usr/bin/env python3
"""
notify_booking_request.py — pushes a notification to the TEACHER'S OWN
device(s) when a student requests a lesson through their portal link.
Triggered by flask_app.py's portal_book() via workflow_dispatch — see
notify-booking-request.yml.

Only the booking id is passed in; the subscriptions and notification text
come from /api/push/booking-notice (CRON_SECRET-protected), so no student
name or time ever lands in GitHub's workflow inputs or logs.

Required environment variables (same secrets as the other push scripts):
  APP_URL, CRON_SECRET, VAPID_PRIVATE_KEY, BOOKING_ID
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
BOOKING_ID = os.environ['BOOKING_ID']


def main():
    resp = requests.get(f'{APP_URL}/api/push/booking-notice',
                        params={'token': CRON_SECRET, 'booking_id': BOOKING_ID}, timeout=30)
    resp.raise_for_status()
    notice = resp.json()
    subs = notice.get('subscriptions', [])
    print(f'{len(subs)} teacher device(s) found')
    if not subs:
        print('teacher has no push subscription — nothing to send')
        return

    payload = json.dumps({'title': notice['title'], 'body': notice['body'], 'url': '/'})

    sent, failed = 0, 0
    for sub in subs:
        subscription_info = {
            'endpoint': sub['endpoint'],
            'keys': {'p256dh': sub['p256dh'], 'auth': sub['auth']},
        }
        try:
            webpush(
                subscription_info=subscription_info,
                data=payload,
                vapid_private_key=VAPID_PRIVATE_KEY,
                vapid_claims=dict(VAPID_CLAIMS),
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
                    json={'endpoint': sub['endpoint']},
                    timeout=15,
                )

    print(f'done: {sent} sent, {failed} failed')
    if failed and not sent:
        sys.exit(1)


if __name__ == '__main__':
    main()
