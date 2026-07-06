#!/bin/bash
# Deploy Offer Letter Approval System to QA Server

set -e

echo "============================================"
echo "Deploying to QA Server (103.205.66.45:8080)"
echo "============================================"

# Get the current directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
QA_HOST="103.205.66.45"
QA_PATH="/var/www/html/apis-qa/backend"

echo "[1/4] Syncing backend files to QA server..."
rsync -avz --exclude='*.pyc' --exclude='__pycache__' --exclude='.git' \
  --exclude='venv' --exclude='.env' --exclude='db.sqlite3' --exclude='media/' \
  $SCRIPT_DIR/ $QA_HOST:$QA_PATH/

echo "[2/4] Running migrations..."
ssh $QA_HOST "$QA_PATH/venv/bin/python $QA_PATH/manage.py migrate pms"

echo "[3/4] Collecting static files..."
ssh $QA_HOST "$QA_PATH/venv/bin/python $QA_PATH/manage.py collectstatic --noinput"

echo "[4/4] Restarting Gunicorn service..."
ssh $QA_HOST "sudo systemctl restart apis-qa"

echo ""
echo "============================================"
echo "Deployment Complete!"
echo "============================================"
echo ""
echo "Test endpoints:"
echo "  - Template: curl http://103.205.66.45:8080/api/pms/offer-letter/template/"
echo "  - Approvals: curl http://103.205.66.45:8080/api/pms/offer-letter/approvals/"
echo ""
