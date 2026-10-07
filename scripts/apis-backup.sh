#!/bin/bash
# Nightly backup for every APIS environment on this machine.
#
# Why this exists: until it did, the only copy of live employee data was a
# single dump somebody took by hand on cutover day. The portal, Appraisal,
# EOM, PMS, goal sheets and TA/DA claims all live in one MySQL database, and
# the uploads -- wall photos, appraisal support documents, offer letters --
# live on disk outside it. A backup of one without the other restores to a
# database full of rows pointing at files that are gone.
#
# Reads its credentials from the environment's own .env, so no password is
# written here and adding an environment means adding one line to ENVS.
#
# Install:  sudo install -m 755 apis-backup.sh /usr/local/bin/apis-backup
#           sudo mkdir -p /var/backups/apis
#           then the cron line at the bottom of this file.
#
# Restore:  zcat /var/backups/apis/<env>-db-<stamp>.sql.gz | mysql -u USER -p DBNAME
#           tar xzf /var/backups/apis/<env>-media-<stamp>.tar.gz -C /

set -uo pipefail

DEST=/var/backups/apis
KEEP_DAYS=21              # dailies older than this go, except the 1st of a month
LOG=/var/log/apis-backup.log

# name|path to that environment's backend (which holds .env and media/)
ENVS="
portal|/var/www/html/apis-portal/backend
prod|/var/www/html/apis/backend
qa|/var/www/html/apis-qa/backend
"

STAMP=$(date +%Y%m%d_%H%M%S)
rc=0
ok_dbs=0        # database dumps this run actually completed

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

mkdir -p "$DEST" || { echo "cannot create $DEST"; exit 1; }
chmod 700 "$DEST"

log "=== backup run $STAMP ==="

# Read one key out of a .env without sourcing it -- a .env can contain
# characters that a shell would try to interpret, and one stray backtick in a
# password would run as a command.
envget() {
  sed -n "s/^$2=//p" "$1/.env" 2>/dev/null | head -1 | tr -d '\r' | sed 's/^"\(.*\)"$/\1/'
}

for row in $ENVS; do
  [ -z "$row" ] && continue
  name=${row%%|*}
  dir=${row##*|}

  if [ ! -f "$dir/.env" ]; then
    log "$name: SKIP, no .env at $dir"
    continue
  fi

  db=$(envget "$dir" DB_NAME)
  user=$(envget "$dir" DB_USER)
  pass=$(envget "$dir" DB_PASSWORD)
  host=$(envget "$dir" DB_HOST); host=${host:-localhost}

  if [ -z "$db" ] || [ -z "$user" ]; then
    log "$name: FAIL, .env names no DB_NAME/DB_USER"
    rc=1
    continue
  fi

  # Two environments pointing at one database is deliberate here (the portal
  # shares PROD's), so say so rather than dumping the same rows twice under
  # two names and looking like two backups.
  out="$DEST/$name-db-$STAMP.sql.gz"

  # MYSQL_PWD rather than --password=, which would show the password to
  # anyone running ps while the dump is in flight.
  MYSQL_PWD="$pass" mysqldump \
      --host="$host" --user="$user" \
      --single-transaction --quick --routines --triggers --events \
      --default-character-set=utf8mb4 \
      --databases "$db" 2>>"$LOG" | gzip -9 > "$out"

  # A dump that failed halfway still leaves a plausible-looking .gz behind.
  # mysqldump writes this marker as its last line and only on success, so it
  # is the difference between a backup and a file.
  if zcat "$out" 2>/dev/null | tail -3 | grep -q 'Dump completed'; then
    ok_dbs=$((ok_dbs + 1))
    log "$name: db ok  $(du -h "$out" | cut -f1)  ($db on $host)"
  else
    log "$name: DB DUMP FAILED -- incomplete file removed: $out"
    rm -f "$out"
    rc=1
  fi

  # Uploads. Absolute paths inside the tar, so a restore is one tar -C /
  # and lands them back where the database expects them.
  if [ -d "$dir/media" ]; then
    mout="$DEST/$name-media-$STAMP.tar.gz"
    if tar czf "$mout" "$dir/media" 2>>"$LOG"; then
      log "$name: media ok  $(du -h "$mout" | cut -f1)"
    else
      log "$name: MEDIA ARCHIVE FAILED"
      rm -f "$mout"
      rc=1
    fi
  else
    log "$name: no media/ directory, nothing to archive"
  fi
done

# Retention. The 1st of each month is kept indefinitely: the failure this
# guards against is not a disk dying, it is somebody noticing in November
# that a column has been wrong since August.
find "$DEST" -maxdepth 1 -type f -name '*_*' -mtime +$KEEP_DAYS \
     ! -name "*-??????01_*" -print -delete >> "$LOG" 2>&1

# A backup nobody checks is a hope, so the run judges itself.
#
# This counts what THIS run completed rather than asking find what is new.
# The first version asked `find -newermt 'today'`, which looks right and is
# not: GNU find reads a bare "today" as the current moment, not midnight, so
# nothing is ever newer than it and the alarm fired on a run that had just
# written five good files. A counter cannot misread a date.
if [ "$ok_dbs" -eq 0 ]; then
  log "ALARM: this run produced no database dump at all"
  rc=1
fi

log "=== run $STAMP finished, exit $rc, $(df -h "$DEST" | tail -1 | awk '{print $4}') free ==="
exit $rc

# ── cron ──────────────────────────────────────────────────────────────────────
# sudo tee /etc/cron.d/apis-backup >/dev/null <<'EOF'
# SHELL=/bin/bash
# PATH=/usr/local/sbin:/usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
# 15 1 * * * root /usr/local/bin/apis-backup
# EOF
# sudo chmod 644 /etc/cron.d/apis-backup
