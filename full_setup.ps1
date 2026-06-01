# ============================================================
#  APIS India - Full Automated Server Setup
#  Run as Administrator in PowerShell
#  One-liner to run from GitHub:
#
#  Set-ExecutionPolicy Bypass -Scope Process -Force
#  Invoke-WebRequest "https://raw.githubusercontent.com/anshulantil16/Apis-Backend/main/full_setup.ps1" -OutFile "$env:TEMP\setup.ps1"
#  & "$env:TEMP\setup.ps1"
# ============================================================

$ErrorActionPreference = "Stop"

# ── Config ───────────────────────────────────────────────────
$AppDir      = "C:\APIS\backend"
$PgPassword  = "APISIndia@db2024"
$PgPort      = 5432
$AppPort     = 8000
$ServerIP    = "103.205.66.97"
$FrontendURL = "https://apis-frontend-mu.vercel.app"
$RepoZip     = "https://github.com/anshulantil16/Apis-Backend/archive/refs/heads/main.zip"
$PyInstaller = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe"
$PgInstaller = "https://get.enterprisedb.com/postgresql/postgresql-16.3-3-windows-x64.exe"

function Write-Step($n, $total, $msg) {
    Write-Host ""
    Write-Host "[$n/$total] $msg" -ForegroundColor Cyan
    Write-Host ("-" * 50) -ForegroundColor DarkGray
}

function Refresh-Path {
    $env:PATH = [System.Environment]::GetEnvironmentVariable("PATH","Machine") + ";" +
                [System.Environment]::GetEnvironmentVariable("PATH","User")
}

# ── Check Admin ───────────────────────────────────────────────
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "ERROR: Please run PowerShell as Administrator." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  APIS India - Automated Server Setup" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green

# ── 1. Install Python 3.11 ────────────────────────────────────
Write-Step 1 7 "Installing Python 3.11..."
$pyExe = "$env:TEMP\python_setup.exe"
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Write-Host "Downloading Python installer..."
    Invoke-WebRequest $PyInstaller -OutFile $pyExe -UseBasicParsing
    Write-Host "Installing Python (silent)..."
    Start-Process -Wait -FilePath $pyExe -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_pip=1 Include_test=0"
    Refresh-Path
    Write-Host "Python installed." -ForegroundColor Green
} else {
    Write-Host "Python already installed: $(python --version)" -ForegroundColor Yellow
}

# ── 2. Install PostgreSQL 16 ──────────────────────────────────
Write-Step 2 7 "Installing PostgreSQL 16..."
$pgExe = "$env:TEMP\pg_setup.exe"
$pgBin = "C:\Program Files\PostgreSQL\16\bin"
if (-not (Test-Path "$pgBin\psql.exe")) {
    Write-Host "Downloading PostgreSQL installer (~300MB, please wait)..."
    Invoke-WebRequest $PgInstaller -OutFile $pgExe -UseBasicParsing
    Write-Host "Installing PostgreSQL (silent)..."
    Start-Process -Wait -FilePath $pgExe -ArgumentList (
        "--mode unattended",
        "--superpassword `"$PgPassword`"",
        "--servicename postgresql-16",
        "--servicepassword `"$PgPassword`"",
        "--serverport $PgPort",
        "--install_runtimes 1"
    )
    $env:PATH += ";$pgBin"
    [System.Environment]::SetEnvironmentVariable("PATH", $env:PATH, "Machine")
    Write-Host "PostgreSQL installed." -ForegroundColor Green
} else {
    Write-Host "PostgreSQL already installed." -ForegroundColor Yellow
}

# ── 3. Create Database ────────────────────────────────────────
Write-Step 3 7 "Creating PostgreSQL database 'apis_db'..."
$env:PGPASSWORD = $PgPassword
$psql = "$pgBin\psql.exe"
$dbExists = & $psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='apis_db';" 2>$null
if ($dbExists -ne "1") {
    & $psql -U postgres -c "CREATE DATABASE apis_db;" 2>$null
    Write-Host "Database 'apis_db' created." -ForegroundColor Green
} else {
    Write-Host "Database 'apis_db' already exists." -ForegroundColor Yellow
}

# ── 4. Download & Extract Repo ────────────────────────────────
Write-Step 4 7 "Downloading APIS India backend code..."
$zipPath = "$env:TEMP\apis_backend.zip"
New-Item -ItemType Directory -Force -Path "C:\APIS" | Out-Null
if (Test-Path $AppDir) {
    Write-Host "Removing old installation..."
    Remove-Item $AppDir -Recurse -Force
}
Write-Host "Downloading from GitHub..."
Invoke-WebRequest $RepoZip -OutFile $zipPath -UseBasicParsing
Write-Host "Extracting..."
Expand-Archive $zipPath -DestinationPath "C:\APIS" -Force
Rename-Item "C:\APIS\Apis-Backend-main" "backend" -ErrorAction SilentlyContinue
Write-Host "Code extracted to $AppDir" -ForegroundColor Green

# ── 5. Python venv + Install Requirements ─────────────────────
Write-Step 5 7 "Setting up Python virtual environment..."
Set-Location $AppDir
python -m venv venv
Write-Host "Installing Python packages..."
& ".\venv\Scripts\pip.exe" install --upgrade pip --quiet
& ".\venv\Scripts\pip.exe" install -r requirements.txt --quiet
Write-Host "Packages installed." -ForegroundColor Green

# ── 6. Generate .env ──────────────────────────────────────────
Write-Step 6 7 "Generating environment configuration..."
$secretKey = & ".\venv\Scripts\python.exe" -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"

$envContent = @"
SECRET_KEY=$secretKey
DEBUG=False
ALLOWED_HOSTS=$ServerIP,localhost,127.0.0.1

DATABASE_URL=postgresql://postgres:$PgPassword@localhost:$PgPort/apis_db

SERVER_HOST=0.0.0.0
SERVER_PORT=$AppPort
SERVER_THREADS=8

CORS_ALLOW_ALL=False
CORS_EXTRA_ORIGINS=$FrontendURL,http://$ServerIP

EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
"@

$envContent | Out-File -FilePath "$AppDir\.env" -Encoding utf8 -NoNewline
Write-Host ".env written." -ForegroundColor Green

# Run migrations and collectstatic
Write-Host "Running database migrations..."
& ".\venv\Scripts\python.exe" manage.py migrate --noinput
Write-Host "Collecting static files..."
& ".\venv\Scripts\python.exe" manage.py collectstatic --noinput --clear

# ── 7. Firewall + Auto-Start Task ─────────────────────────────
Write-Step 7 7 "Configuring firewall and auto-start..."

# Firewall
try { Remove-NetFirewallRule -DisplayName "APIS Backend" -ErrorAction SilentlyContinue } catch {}
New-NetFirewallRule -DisplayName "APIS Backend" -Direction Inbound -Protocol TCP `
    -LocalPort $AppPort -Action Allow | Out-Null
Write-Host "Firewall port $AppPort opened." -ForegroundColor Green

# Windows Task Scheduler — auto-start on reboot
$taskExe  = "cmd.exe"
$taskArgs = "/c `"cd /d $AppDir && start.bat`""
$action   = New-ScheduledTaskAction -Execute $taskExe -Argument $taskArgs -WorkingDirectory $AppDir
$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 2) `
            -ExecutionTimeLimit ([TimeSpan]::Zero)
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
Register-ScheduledTask -TaskName "APIS_Backend_Server" -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Auto-start task registered (runs on every reboot)." -ForegroundColor Green

# ── Done ──────────────────────────────────────────────────────
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Setup Complete!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  App directory : $AppDir" -ForegroundColor White
Write-Host "  Database      : apis_db (PostgreSQL, localhost:$PgPort)" -ForegroundColor White
Write-Host "  PG Password   : $PgPassword" -ForegroundColor White
Write-Host "  Server URL    : http://$ServerIP`:$AppPort" -ForegroundColor White
Write-Host ""
Write-Host "  Starting server now..." -ForegroundColor Cyan
Write-Host ""

# Start server immediately
Set-Location $AppDir
Start-Process "cmd.exe" -ArgumentList "/k start.bat" -WorkingDirectory $AppDir
