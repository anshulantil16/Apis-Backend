# Offer Letter Approval System - Complete Guide

## Overview
The Offer Letter system now includes an email-based approval workflow. Employees receive HTML emails with clickable "Accept" and "Reject" buttons. Their responses are tracked with audit information (IP address, user agent, timestamp).

## Architecture

### Models
- **OfferLetter**: Main letter data (current CTC, new CTC, employee details, etc.)
- **OfferLetterApproval**: Tracks employee's approval status (pending/accepted/rejected/under_review)

### API Endpoints

#### 1. **POST `/api/pms/offer-letter/{id}/approve/`**
Employee accepts/rejects an offer letter.

**Request Body:**
```json
{
  "action": "accept",  // or "reject" or "review"
  "comments": "I accept the offer with immediate effect"  // optional
}
```

**Response:**
```json
{
  "message": "Letter accepted",
  "status": "accepted",
  "accepted_at": "2026-07-06T07:01:04.591622+00:00"
}
```

#### 2. **GET `/api/pms/offer-letter/{id}/status/`**
Get approval status for a specific offer letter.

**Response:**
```json
{
  "offer_letter_id": 1,
  "employee_id": "EMP001",
  "employee_name": "Rahul Sharma",
  "approval_status": "accepted",
  "accepted_at": "2026-07-06T07:01:04+00:00",
  "comments": "I accept the offer",
  "created_at": "2026-07-06T07:00:43+00:00",
  "updated_at": "2026-07-06T07:01:04+00:00"
}
```

#### 3. **GET `/api/pms/offer-letter/approvals/`**
Get approval dashboard with summary stats and all letters.

**Response:**
```json
{
  "total": 10,
  "accepted": 7,
  "rejected": 1,
  "pending": 2,
  "acceptance_rate": "70.0%",
  "letters": [
    {
      "offer_letter_id": 1,
      "employee_id": "EMP001",
      "employee_name": "Rahul Sharma",
      "letter_type": "increment",
      "effective_date": "2026-07-01",
      "approval_status": "accepted",
      "accepted_at": "2026-07-06T07:01:04+00:00",
      "email_sent": true,
      "email_sent_at": "2026-07-05T10:00:00+00:00"
    }
  ]
}
```

## Email Template

When an offer letter is sent with `send_email=True`, the employee receives an HTML email with:

1. **Letter details**: Current CTC, New CTC, Increment %, Performance rating
2. **Attached PDF**: Offer letter document (attached as separate file)
3. **Approval buttons**: 
   - Green "Accept" button
   - Red "Reject" button
4. **Action notice**: Highlights that action is required within 3 business days
5. **HR contact**: HR department contact info

### Email Flow
```
Employee receives email
↓
Clicks Accept/Reject button
↓
Button links to: /api/pms/offer-letter/{id}/approve/?action=accept/reject
↓
System records: status, timestamp, IP address, user agent
↓
OfferLetterApproval model updated
```

## Database Schema

### OfferLetterApproval Table
```
- id: Integer (PK)
- offer_letter_id: Integer (FK to OfferLetter)
- status: CharField ['pending', 'accepted', 'rejected', 'under_review']
- accepted_at: DateTime (nullable)
- comments: TextField
- ip_address: GenericIPAddressField (nullable)
- user_agent: CharField (max 500)
- created_at: DateTime (auto_now_add)
- updated_at: DateTime (auto_now)
```

## Workflow Steps

### 1. Create and Send Offer Letter
```python
# Backend view creates OfferLetter record
offer = OfferLetter.objects.create(
    employee=emp,
    letter_type='increment',
    current_ctc=500000,
    new_ctc=550000,
    increment_pct=10,
    promotion_pct=0,
    effective_date=date(2026, 7, 1),
    email_address='emp@company.com',
)

# Queue async task to generate PDF and send email
process_offer_letter(offer.id, send_email=True)
```

### 2. Task Processing (Celery)
```
Celery worker receives task
↓
Generates PDF with ReportLab
↓
Encrypts PDF (optional, via qpdf)
↓
Saves PDF to media/offer_letters/
↓
Sends HTML email with Accept/Reject buttons
↓
Updates OfferLetter: status='sent', email_sent_at=now()
↓
Creates OfferLetterApproval: status='pending'
```

### 3. Employee Approves/Rejects
```
Employee clicks email button
↓
POST /api/pms/offer-letter/{id}/approve/ with action=accept/reject
↓
OfferLetterApprovalView processes request
↓
Records: status, accepted_at, IP, user_agent
↓
Returns success response
```

### 4. Dashboard View
HR admin views approval status via `/api/pms/offer-letter/approvals/`
- Total letters sent
- Acceptance rate %
- Breakdown by status (accepted/rejected/pending)
- Individual letter details with timestamps

## Testing

Run the approval workflow test:
```bash
python test_approval_flow.py
```

This test:
1. Creates a test employee
2. Creates a test offer letter
3. Simulates employee clicking "Accept" button
4. Verifies OfferLetterApproval record created
5. Checks IP/user-agent audit fields
6. Validates status query endpoint

## Deployment

### QA Server (103.205.66.45:8080)
1. Push changes to GitHub backend
2. SSH to QA server: `ssh user@103.205.66.45`
3. Run migration: `/var/www/html/apis-qa/backend/venv/bin/python /var/www/html/apis-qa/backend/manage.py migrate pms`
4. Restart Gunicorn: `sudo systemctl restart apis-qa`
5. Test endpoint: `curl http://103.205.66.45:8080/api/pms/offer-letter/approvals/`

### PROD Server (103.205.66.45:80)
- Follow same steps but with `/var/www/html/apis-prod/` paths
- Only deploy new tables/fields to PROD, never modify existing data

## Notes

- Each OfferLetter can only have ONE OfferLetterApproval record (OneToOneField)
- If employee rejects then changes mind, they can click Accept again - status updates to "accepted"
- IP address and user agent captured for audit trail
- All timestamps in UTC timezone
- Email addresses validated before sending
- PDF encryption is optional (currently disabled in tasks.py)

## Future Enhancements

- [ ] Digital signature integration
- [ ] Email reminders after N days if pending
- [ ] Bulk approval by HR admin
- [ ] Multi-step approval (manager approval before employee)
- [ ] PDF watermarking with "Accepted on [date]"
- [ ] SMS notification fallback
