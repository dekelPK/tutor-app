#!/usr/bin/env python3
"""
send_password_reset.py — emails a password-reset link to a user.

PythonAnywhere's free tier can't send outbound email (or most outbound
HTTPS at all), so — same pattern as notify_admin_signup.py and
send_reminders.py — this runs on a GitHub Actions runner instead, which has
no such restriction. Triggered by flask_app.py's /api/auth/forgot-password
via workflow_dispatch (never by a human, never on a schedule).

Required environment variables:
  APP_URL, EMAIL_ADDRESS, EMAIL_APP_PASSWORD
Plus, for this script specifically:
  RESET_EMAIL, RESET_NAME, RESET_TOKEN  (passed in from workflow_dispatch inputs)
"""
import os
import smtplib
from email.mime.text import MIMEText

APP_URL = os.environ['APP_URL'].rstrip('/')
EMAIL_ADDRESS = os.environ['EMAIL_ADDRESS']
EMAIL_APP_PASSWORD = os.environ['EMAIL_APP_PASSWORD']

RESET_EMAIL = os.environ['RESET_EMAIL']
RESET_NAME = os.environ.get('RESET_NAME', '')
RESET_TOKEN = os.environ['RESET_TOKEN']


def main():
    reset_url = f'{APP_URL}/?reset={RESET_TOKEN}'
    greeting = f'היי {RESET_NAME},' if RESET_NAME else 'היי,'
    body = f'''{greeting}

קיבלנו בקשה לאיפוס הסיסמה שלך באפליקציית ניהול השיעורים הפרטיים.

לחצי על הקישור הבא כדי לקבוע סיסמה חדשה (בתוקף לשעה אחת):
{reset_url}

אם לא ביקשת לאפס סיסמה — אפשר להתעלם מההודעה הזו, הסיסמה שלך לא תשתנה.
'''
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = 'איפוס סיסמה - ניהול שיעורים פרטיים'
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = RESET_EMAIL

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
        smtp.send_message(msg)

    print(f'Password reset email sent to {RESET_EMAIL}')


if __name__ == '__main__':
    main()
