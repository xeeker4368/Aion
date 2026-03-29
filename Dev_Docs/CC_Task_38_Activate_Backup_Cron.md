# CC Task 38 — Activate Backup Cron Job

## Why

Going live means permanent data. Backups need to be running automatically before that happens. The backup script exists and works. The cron job was written but never activated.

## What to Do

### Step 1: Verify backup.py works manually

```bash
cd /home/localadmin/aion
source aion/bin/activate
python3 backup.py
```

Should print backup sizes and the backup directory path.

### Step 2: Add the cron job

```bash
crontab -e
```

Add this line (runs daily at 5:00 AM):

```
0 5 * * * /home/localadmin/aion/aion/bin/python /home/localadmin/aion/backup.py >> /home/localadmin/aion/data/logs/backup.log 2>&1
```

**Important:** Use the venv Python path `/home/localadmin/aion/aion/bin/python`, not system Python.

### Step 3: Verify cron is set

```bash
crontab -l
```

Should show the backup line.

### Step 4: Test the cron command manually

Run the exact command from the cron entry to make sure paths are right:

```bash
/home/localadmin/aion/aion/bin/python /home/localadmin/aion/backup.py >> /home/localadmin/aion/data/logs/backup.log 2>&1
```

Check it worked:

```bash
tail -10 /home/localadmin/aion/data/logs/backup.log
ls -lt /home/localadmin/aion/data/backups/ | head -5
```

## What NOT to Do

- Do NOT modify backup.py
- Do NOT change the backup retention count (currently keeps last 7)
- Do NOT use system Python — use the venv path

## Verification

`crontab -l` shows the backup entry. Manual run produces a backup directory with all three stores. Backup log shows success.
