import os
import sys
import django
from datetime import date
import io

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, os.path.dirname(__file__))
django.setup()

from pms.offer_letter import send_offer_letter_email
from django.conf import settings

# Mock PDF buffer
pdf_buffer = io.BytesIO(b'Mock PDF content')

# Print the email that would be sent
print("=" * 70)
print("EMAIL PREVIEW: Offer Letter Approval Email")
print("=" * 70)

# Temporarily capture the email
from django.core.mail import EmailMessage
original_send = EmailMessage.send

captured_email = None
def capture_send(self, **kwargs):
    global captured_email
    captured_email = self
    return 1

EmailMessage.send = capture_send

try:
    send_offer_letter_email(
        employee_email='employee@example.com',
        employee_name='Rahul Sharma',
        pdf_buffer=pdf_buffer,
        effective_date=date(2026, 7, 1),
        offer_letter_id=1
    )

    if captured_email:
        print(f"\nTo: {captured_email.to}")
        print(f"Subject: {captured_email.subject}")
        print(f"Content-Type: {captured_email.content_subtype}")

        # Write to file
        with open('email_preview.html', 'w', encoding='utf-8') as f:
            f.write(captured_email.body)

        print("\nEmail body written to: email_preview.html")
        print(f"\nAttachments: {len(captured_email.attachments)} file(s)")
        for i, (filename, content, mime_type) in enumerate(captured_email.attachments):
            print(f"  [{i+1}] {filename} ({mime_type}, {len(content)} bytes)")

finally:
    EmailMessage.send = original_send
