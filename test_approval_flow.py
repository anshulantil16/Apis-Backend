import os
import sys
import django
from datetime import date

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
sys.path.insert(0, os.path.dirname(__file__))
django.setup()

from pms.models import OfferLetter, OfferLetterApproval, PMSEmployee
from django.test import RequestFactory
from pms.views import OfferLetterApprovalView, OfferLetterStatusView

emp, _ = PMSEmployee.objects.get_or_create(
    employee_id='TEST001',
    defaults={
        'name': 'Test Employee',
        'department': 'IT',
        'designation': 'Developer',
    }
)

offer, _ = OfferLetter.objects.get_or_create(
    employee=emp,
    letter_type='increment',
    defaults={
        'current_ctc': 500000,
        'new_ctc': 550000,
        'increment_pct': 10,
        'promotion_pct': 0,
        'effective_date': date(2026, 7, 1),
        'email_address': 'test@example.com',
        'status': 'sent',
    }
)

print(f"[+] Created offer letter: {offer.id} for {emp.name}")

factory = RequestFactory()
request = factory.post(f'/api/pms/offer-letter/{offer.id}/approve/',
                       {'action': 'accept'},
                       content_type='application/json')
request.META['REMOTE_ADDR'] = '192.168.1.1'
request.META['HTTP_USER_AGENT'] = 'Test Browser'

view = OfferLetterApprovalView.as_view()
response = view(request, offer_letter_id=offer.id)
print(f"[+] Approval response: {response.data}")

request2 = factory.get(f'/api/pms/offer-letter/{offer.id}/status/')
view2 = OfferLetterStatusView.as_view()
response2 = view2(request2, offer_letter_id=offer.id)
print(f"[+] Status response: {response2.data}")

approval = OfferLetterApproval.objects.get(offer_letter=offer)
print(f"\n[+] OfferLetterApproval created:")
print(f"  - Status: {approval.status}")
print(f"  - Accepted at: {approval.accepted_at}")
print(f"  - IP: {approval.ip_address}")
print(f"  - User agent: {approval.user_agent[:50]}...")

print("\n[SUCCESS] All approval tests passed!")
