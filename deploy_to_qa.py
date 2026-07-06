#!/usr/bin/env python3
"""Deploy Offer Letter Approval System to QA server."""
import subprocess
import sys
import os

QA_HOST = "root@103.205.66.45"
QA_BACKEND_PATH = "/var/www/html/apis-qa/backend"
LOCAL_BACKEND_PATH = r"d:\Code\Apis\backend"

def run_cmd(cmd, description=""):
    """Run command and print output."""
    if description:
        print(f"\n{'='*60}")
        print(f"[{description}]")
        print('='*60)
    print(f"$ {cmd}\n")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0:
        print(f"ERROR: Command failed with code {result.returncode}")
        return False
    return True

def main():
    print("\n" + "="*60)
    print("DEPLOYING OFFER LETTER APPROVAL SYSTEM TO QA")
    print("="*60)

    # Step 1: Copy backend code to QA
    print("\n[1/4] Syncing backend files to QA server...")
    print("Using SCP to copy Python files...")

    # Get list of Python files that changed
    changed_files = [
        "pms/models.py",
        "pms/views.py",
        "pms/urls.py",
        "pms/offer_letter.py",
        "pms/tasks.py",
        "pms/migrations/0007_add_offer_letter_approval.py",
        "config/celery.py",
        "config/__init__.py",
    ]

    for file in changed_files:
        local_file = os.path.join(LOCAL_BACKEND_PATH, file)
        if os.path.exists(local_file):
            cmd = f'scp "{local_file}" {QA_HOST}:{QA_BACKEND_PATH}/{file}'
            print(f"  Copying {file}...")
            result = subprocess.run(cmd, shell=True, capture_output=True)
            if result.returncode != 0:
                print(f"    WARNING: {result.stderr.decode()}")
        else:
            print(f"  SKIP: {file} not found")

    # Step 2: Run migrations
    if not run_cmd(
        f'ssh {QA_HOST} "{QA_BACKEND_PATH}/venv/bin/python {QA_BACKEND_PATH}/manage.py migrate pms"',
        "2/4 - Running migrations"
    ):
        return False

    # Step 3: Collect static files
    if not run_cmd(
        f'ssh {QA_HOST} "{QA_BACKEND_PATH}/venv/bin/python {QA_BACKEND_PATH}/manage.py collectstatic --noinput"',
        "3/4 - Collecting static files"
    ):
        return False

    # Step 4: Restart Gunicorn
    if not run_cmd(
        f'ssh {QA_HOST} "sudo systemctl restart apis-qa"',
        "4/4 - Restarting Gunicorn"
    ):
        return False

    # Test the endpoint
    print("\n" + "="*60)
    print("TESTING APPROVAL ENDPOINT")
    print("="*60)
    result = subprocess.run(
        f'ssh {QA_HOST} "curl -s http://103.205.66.45:8080/api/pms/offer-letter/approvals/ | head -50"',
        shell=True
    )

    print("\n" + "="*60)
    print("DEPLOYMENT COMPLETE!")
    print("="*60)
    print("""
Endpoints ready:
  - Get approvals: curl http://103.205.66.45:8080/api/pms/offer-letter/approvals/
  - Get specific:  curl http://103.205.66.45:8080/api/pms/offer-letter/1/status/

Next steps:
  1. Upload Excel file to generate offer letters
  2. System will send emails with Accept/Reject buttons
  3. Check dashboard at /api/pms/offer-letter/approvals/
    """)

if __name__ == "__main__":
    main()
