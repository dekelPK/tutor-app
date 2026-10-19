#!/usr/bin/env python3
"""
send_welcome_email.py — the email every new signup gets right away: the free
trial has started, when it ends, and how to continue after it.

Runs on GitHub Actions for the same reason as send_approval_email.py
(PythonAnywhere's free tier can't send outbound email). Triggered by
flask_app.py's auth_register() via workflow_dispatch.

Required environment variables:
  APP_URL, EMAIL_ADDRESS, EMAIL_APP_PASSWORD
Plus: WELCOME_EMAIL, WELCOME_NAME, TRIAL_ENDS (YYYY-MM-DD)
"""
import os
import smtplib
from email.mime.text import MIMEText

APP_URL = os.environ['APP_URL'].rstrip('/')
EMAIL_ADDRESS = os.environ['EMAIL_ADDRESS']
EMAIL_APP_PASSWORD = os.environ['EMAIL_APP_PASSWORD']

TO = os.environ['WELCOME_EMAIL']
NAME = os.environ.get('WELCOME_NAME', '')
TRIAL_ENDS = os.environ.get('TRIAL_ENDS', '')


def main():
    greeting = f'היי {NAME.split(" ")[0]},' if NAME.strip() else 'היי,'
    y, m, d = (TRIAL_ENDS.split('-') + ['', '', ''])[:3]
    ends = f' (עד {d}/{m}/{y})' if d else ''
    body = f'''{greeting}

ברוכים הבאים ללוזי! 💙

החשבון שלך פתוח ושבוע הניסיון התחיל{ends}. אפשר להתחבר ולהתחיל לעבוד:
{APP_URL}/

כמה דברים שכדאי לעשות כבר היום:
• להוסיף את הלקוחות שלך
• לקבוע את התורים של השבוע
• לשלוח ללקוח קישור אישי, כדי שיוכל לקבוע תור לבד

רוצה להמשיך אחרי שבוע הניסיון? לוחצים באפליקציה על "אני רוצה להמשיך", ואנחנו שולחים בקשת תשלום בביט.

שאלות? אפשר פשוט להשיב למייל הזה.

צוות לוזי
'''
    msg = MIMEText(body, 'plain', 'utf-8')
    msg['Subject'] = 'ברוכים הבאים ללוזי — שבוע הניסיון התחיל 🎉'
    msg['From'] = EMAIL_ADDRESS
    msg['To'] = TO

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(EMAIL_ADDRESS, EMAIL_APP_PASSWORD)
        smtp.send_message(msg)

    print('Welcome email sent')


if __name__ == '__main__':
    main()
