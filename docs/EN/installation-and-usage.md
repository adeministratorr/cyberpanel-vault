# CyberPanel Vault Installation and Usage

This document explains how to install and operate `CyberPanel Vault` on a CyberPanel host.

Publisher: [Adem YÜCE](https://ademyuce.tr) - [ademyuce.tr](https://ademyuce.tr)

## What it provides

- Weekly full backups
- Incremental file and config backups between full runs
- Full database dump on every run
- `openssl` encrypted archives
- Google Drive uploads through `rclone`
- Chain-aware restore flow
- A Django app skeleton that can be mounted into a CyberPanel-style UI

## Requirements

- Linux server with `root` access
- `rclone`
- `mysql` and `mysqldump`
- `openssl`
- `tar`, `gzip`, `sha256sum`, `flock`, `rsync`
- `/etc/cyberpanel/mysqlPassword`
- A working Google Drive `rclone` remote
- An encryption password file

## Installation

1. Copy the scripts to the server:

```bash
install -m 750 cyberpanel_full_backup.sh /usr/local/bin/cyberpanel_full_backup.sh
install -m 750 cyberpanel_restore.sh /usr/local/bin/cyberpanel_restore.sh
```

2. Create the runtime and secret directories:

```bash
mkdir -p /root/.config/cyberpanel-backup /var/lib/cyberpanel-backup /var/lib/cyberpanel-backup-ui
chmod 700 /root/.config/cyberpanel-backup /var/lib/cyberpanel-backup /var/lib/cyberpanel-backup-ui
```

3. Create the encryption password file:

```bash
printf '%s\n' 'REPLACE_WITH_A_LONG_RANDOM_SECRET' >/root/.config/cyberpanel-backup/encryption.pass
chmod 600 /root/.config/cyberpanel-backup/encryption.pass
```

4. Configure `rclone` for Google Drive. The default remote name expected by the scripts is `gdrive`.

5. Run the first full backup:

```bash
BACKUP_MODE=full /usr/local/bin/cyberpanel_full_backup.sh
```

6. Schedule regular runs in `auto` mode:

```bash
0 3 * * * BACKUP_MODE=auto /usr/local/bin/cyberpanel_full_backup.sh
```

In `auto` mode the script creates a weekly full backup and incremental backups between full runs.

## Usage

Backup modes:

- `BACKUP_MODE=auto`
- `BACKUP_MODE=full`
- `BACKUP_MODE=incremental`

Examples:

```bash
BACKUP_MODE=auto /usr/local/bin/cyberpanel_full_backup.sh
BACKUP_MODE=full /usr/local/bin/cyberpanel_full_backup.sh
BACKUP_MODE=incremental /usr/local/bin/cyberpanel_full_backup.sh
```

Important environment variables:

- `RCLONE_REMOTE`, default `gdrive`
- `DRIVE_FOLDER`, default `cyberpanel-backups`
- `BACKUP_DIR`, default `/root/backups`
- `STATE_DIR`, default `/var/lib/cyberpanel-backup`
- `LOG_FILE`, default `/var/log/cyberpanel_backup.log`
- `ENCRYPTION_PASSWORD_FILE`, default `/root/.config/cyberpanel-backup/encryption.pass`

## Restore

Validate the selected chain before applying changes:

```bash
/usr/local/bin/cyberpanel_restore.sh --target-file backup__host-example.com__chain-20260317T030000__type-incremental__at-20260318T030000.tar.gz.enc
```

Run a real restore:

```bash
/usr/local/bin/cyberpanel_restore.sh \
  --target-file backup__host-example.com__chain-20260317T030000__type-incremental__at-20260318T030000.tar.gz.enc \
  --confirm-host "$(hostname -f)" \
  --apply
```

Optional flags:

- `--skip-db`
- `--skip-files`
- `--skip-configs`
- `--skip-services`
- `--keep-workdir`

## CyberPanel integration

The `serverBackupManager/` directory is a Django app skeleton.

Minimum integration steps:

1. Add the directory to the CyberPanel Django codebase.
2. Mount the routes from `urls.py`.
3. Set the required environment variables:

```bash
export CYBERPANEL_SERVER_BACKUP_SCRIPT=/usr/local/bin/cyberpanel_full_backup.sh
export CYBERPANEL_SERVER_RESTORE_SCRIPT=/usr/local/bin/cyberpanel_restore.sh
export CYBERPANEL_SERVER_BACKUP_UI_STATE_DIR=/var/lib/cyberpanel-backup-ui
```

4. Ensure the web process can spawn background jobs and that `rclone` is available on the host.

## Operational notes

- The scripts must run as `root`.
- The restore flow verifies the current host FQDN before applying changes.
- The UI only lists backup chains that match the current host slug.
- Always validate a full restore on staging before production use.
