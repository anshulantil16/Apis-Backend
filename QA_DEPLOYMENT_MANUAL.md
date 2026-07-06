# Manual QA Deployment Guide

Since SSH isn't available in the current environment, follow these steps on the QA server directly.

## Prerequisites
- SSH access to QA server (103.205.66.45)
- Sudo permissions for systemctl

## Deployment Steps

### Step 1: Copy Backend Files
SSH to QA server:
```bash
ssh root@103.205.66.45
```

Navigate to backend directory:
```bash
cd /var/www/html/apis-qa/backend
```

### Step 2: Update Code Files
Copy these files from your local machine to the QA server:

**Critical files (must update):**
```
backend/pms/models.py                              # New OfferLetterApproval model
backend/pms/views.py                               # New approval endpoints
backend/pms/urls.py                                # New URL routes
backend/pms/offer_letter.py                        # Updated email template
backend/pms/tasks.py                               # Pass offer_letter_id to email
backend/pms/migrations/0007_add_offer_letter_approval.py  # Database migration
backend/config/celery.py                           # Optional Celery import
backend/config/__init__.py                         # Optional Celery import
```

Use `scp` to copy files:
```bash
scp /path/to/local/file root@103.205.66.45:/var/www/html/apis-qa/backend/path/to/file
```

Or copy one file at a time through your preferred method.

### Step 3: Run Database Migration
On the QA server:

```bash
cd /var/www/html/apis-qa/backend
/var/www/html/apis-qa/backend/venv/bin/python manage.py migrate pms
```

**Expected output:**
```
Running migrations:
  Applying pms.0007_add_offer_letter_approval... OK
```

### Step 4: Collect Static Files (Optional)
```bash
/var/www/html/apis-qa/backend/venv/bin/python manage.py collectstatic --noinput
```

### Step 5: Restart Gunicorn
```bash
sudo systemctl restart apis-qa
```

**Verify it's running:**
```bash
sudo systemctl status apis-qa
```

Should show: `active (running)`

### Step 6: Test the Endpoints

#### Test 1: Get all approvals
```bash
curl http://103.205.66.45:8080/api/pms/offer-letter/approvals/
```

**Expected response:**
```json
{
  "total": 0,
  "accepted": 0,
  "rejected": 0,
  "pending": 0,
  "acceptance_rate": "0%",
  "letters": []
}
```

#### Test 2: Upload offer letters
```bash
# Download template
curl http://103.205.66.45:8080/api/pms/offer-letter/template/ -o template.xlsx

# Fill with data and upload
curl -X POST \
  -F "file=@template.xlsx" \
  http://103.205.66.45:8080/api/pms/offer-letter/upload/
```

#### Test 3: Check approval status
```bash
# After uploading, get offer letter ID from response, then:
curl http://103.205.66.45:8080/api/pms/offer-letter/1/status/
```

## Verification Checklist

- [ ] Files copied successfully
- [ ] Migration ran without errors
- [ ] Gunicorn restarted
- [ ] `/api/pms/offer-letter/approvals/` returns JSON
- [ ] Can download template from `/api/pms/offer-letter/template/`
- [ ] Can upload Excel file with employee data
- [ ] Email is sent (check Celery logs in `/var/www/html/apis-qa/backend/logs/celery.log`)

## If Something Goes Wrong

### Migration failed
```bash
# Check migration status
/var/www/html/apis-qa/backend/venv/bin/python manage.py showmigrations pms

# Rollback if needed
/var/www/html/apis-qa/backend/venv/bin/python manage.py migrate pms 0006
```

### Import errors
```bash
# Check Python environment
/var/www/html/apis-qa/backend/venv/bin/pip list | grep -E "celery|reportlab|openpyxl"

# Install missing packages if needed
/var/www/html/apis-qa/backend/venv/bin/pip install celery reportlab openpyxl
```

### Gunicorn won't restart
```bash
# Check logs
sudo journalctl -u apis-qa -n 50

# Check syntax errors in Python files
/var/www/html/apis-qa/backend/venv/bin/python -m py_compile pms/models.py
/var/www/html/apis-qa/backend/venv/bin/python -m py_compile pms/views.py
```

## What's New in This Release

✓ Email-based offer letter approval system
✓ Employees can accept/reject via email buttons
✓ Approval tracking with IP address and user-agent
✓ Dashboard API showing acceptance rates
✓ HTML-formatted emails with professional styling

## Files Changed Summary

```
pms/models.py              +52 lines    (OfferLetterApproval model)
pms/views.py               +161 lines   (3 approval endpoints)
pms/urls.py                +5 lines     (URL routes)
pms/offer_letter.py        +65 lines    (Email template update)
pms/tasks.py               +2 lines     (Pass offer_letter_id)
config/celery.py           +10 lines    (Optional import)
config/__init__.py         +4 lines     (Optional import)
migrations/0007_*.py       +1 new file  (Database table)
```

## Next Steps After Deployment

1. **Test with real employees:**
   - Upload a small Excel file (3-5 employees)
   - Verify emails are sent
   - Check approval endpoint shows pending status

2. **Monitor Celery worker:**
   - Check logs for PDF generation
   - Verify email sending
   - Look for any encryption warnings (PDF passwords optional)

3. **Prepare for production:**
   - Same steps on PROD server at 103.205.66.45:80
   - Only deploy NEW tables/fields, never modify existing PROD data

## Support

For issues or questions:
- Check `/var/www/html/apis-qa/backend/logs/` for error logs
- Verify database migration: `mysql -u user -p db_name -e "SHOW TABLES LIKE 'pms_offerletter%';"`
- Test email backend in settings.py
