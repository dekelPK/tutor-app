#!/usr/bin/env python3
"""
send_reminders.py — sends today's lesson-reminder push notifications.

Run once a day by .github/workflows/daily-lesson-reminders.yml (GitHub Actions
has unrestricted outbound internet, unlike PythonAnywhere's free tier, so this
is where the actual call to Apple's/Google's push service happens).

Required environment variables (set as GitHub Actions secrets):
  APP_URL            e.g. https://dekelpa.pythonanywhere.com
  CRON_SECRET        must match the server's .cron_secret file
  VAPID_PRIVATE_KEY  base64url-encoded raw EC private key (never commit this)
  VAPID_PUBLIC_KEY   base64url-encoded uncompressed EC point (matches the
                      server's hardcoded VAPID_PUBLIC_KEY)
"""
import os
import sys
import json
import requests
from pywebpush import webpush, WebPushException

APP_URL = os.environ['APP_URL'].rstrip('/')
CRON_SECRET = os.environ['CRON_SECRET']
VAPID_PRIVATE_KEY = os.environ['VAPID_PRIVATE_KEY']
VAPID_PUBLIC_KEY = os.environ['VAPID_PUBLIC_KEY']
VAPID_CLAIMS = {'sub': os.environ.get('VAPID_CONTACT_EMAIL', 'mailto:admin@example.com')}


def main():
    resp = requests.get(f'{APP_URL}/api/push/due-today', params={'token': CRON_SECRET}, timeout=30)
    resp.raise_for_status()
    due = resp.json()
    print(f'{len(due)} reminder(s) to send')

    sent, failed = 0, 0
    for item in due:
        subscription_info = {
            'endpoint': item['endpoint'],
            'keys': {'p256dh': item['p256dh'], 'auth': item['auth']},
        }
        payload = json.dumps({'title': item['title'], 'body': item['body'], 'url': item['url']})
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
                # Subscription is gone (uninstalled / expired) — clean it up server-side.
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
