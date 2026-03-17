# CyberPanel Vault

CyberPanel Vault is a server-level backup and restore toolkit for CyberPanel deployments. It combines weekly full backups, incremental backup chains, encrypted Google Drive uploads through `rclone`, and a CyberPanel-ready management UI for running backup and restore jobs.

Project website and publisher: [Adem YÜCE](https://ademyuce.tr) - [ademyuce.tr](https://ademyuce.tr)
GitHub Pages: [adeministratorr.github.io/cyberpanel-vault](https://adeministratorr.github.io/cyberpanel-vault/)

## Why this project exists

CyberPanel Backup V2 is useful for website-level backups, but production servers often need a separate host-level workflow for:

- `/home` and mail data
- CyberPanel and OpenLiteSpeed configuration
- DNS, SSL, cron, firewall, and systemd configuration
- encrypted off-site storage
- controlled restore operations from inside an admin UI

CyberPanel Vault fills that gap.

## Core features

- Weekly full backups with incremental file/config backup chains
- Full database dump on every run for safer database restores
- Encrypted archives using `openssl`
- Google Drive uploads with `rclone`
- Chain-aware cleanup logic
- Restore script that applies the correct full + incremental chain in order
- CyberPanel-ready Django app/plugin skeleton for backup and restore jobs

## Repository layout

- [`cyberpanel_full_backup.sh`](/Users/ademyuce/Documents/CyberPanel/cyberpanel_full_backup.sh)
  Weekly full + incremental backup script
- [`cyberpanel_restore.sh`](/Users/ademyuce/Documents/CyberPanel/cyberpanel_restore.sh)
  Restore script for encrypted backup chains
- [`serverBackupManager/`](/Users/ademyuce/Documents/CyberPanel/serverBackupManager)
  CyberPanel-ready Django app/plugin skeleton

## Documentation

- GitHub Pages landing page: [`docs/index.html`](/Users/ademyuce/Documents/CyberPanel/docs/index.html)
- Turkish installation and usage guide: [`docs/TR/kurulum-ve-kullanim.md`](/Users/ademyuce/Documents/CyberPanel/docs/TR/kurulum-ve-kullanim.md)
- English installation and usage guide: [`docs/EN/installation-and-usage.md`](/Users/ademyuce/Documents/CyberPanel/docs/EN/installation-and-usage.md)

## Requirements

- Linux server with `root` access
- `rclone`, `mysql`, `mysqldump`, `openssl`, `tar`, `gzip`, `sha256sum`, `flock`, `rsync`
- CyberPanel MySQL root password file at `/etc/cyberpanel/mysqlPassword`
- An `rclone` remote for Google Drive, or equivalent values for `RCLONE_REMOTE` and `DRIVE_FOLDER`
- Encryption password file at `/root/.config/cyberpanel-backup/encryption.pass`

## Installation

1. Copy the shell scripts to your server:

```bash
install -m 750 cyberpanel_full_backup.sh /usr/local/bin/cyberpanel_full_backup.sh
install -m 750 cyberpanel_restore.sh /usr/local/bin/cyberpanel_restore.sh
```

2. Create the required runtime directories:

```bash
mkdir -p /root/.config/cyberpanel-backup /var/lib/cyberpanel-backup /var/lib/cyberpanel-backup-ui
chmod 700 /root/.config/cyberpanel-backup /var/lib/cyberpanel-backup /var/lib/cyberpanel-backup-ui
```

3. Create the encryption password file:

```bash
printf '%s\n' 'CHANGE_THIS_TO_A_LONG_RANDOM_SECRET' >/root/.config/cyberpanel-backup/encryption.pass
chmod 600 /root/.config/cyberpanel-backup/encryption.pass
```

4. Configure `rclone` so the backup host can write to your Google Drive remote. The default remote name is `gdrive`.

5. Run the first backup manually:

```bash
BACKUP_MODE=full /usr/local/bin/cyberpanel_full_backup.sh
```

6. Schedule regular runs with `BACKUP_MODE=auto`. In `auto` mode the script takes a weekly full backup and incremental backups between full runs.

Example cron:

```bash
0 3 * * * BACKUP_MODE=auto /usr/local/bin/cyberpanel_full_backup.sh
```

## Backup usage

- Automatic mode: `BACKUP_MODE=auto /usr/local/bin/cyberpanel_full_backup.sh`
- Force a full backup: `BACKUP_MODE=full /usr/local/bin/cyberpanel_full_backup.sh`
- Force an incremental backup: `BACKUP_MODE=incremental /usr/local/bin/cyberpanel_full_backup.sh`

Important runtime variables:

- `RCLONE_REMOTE`: default `gdrive`
- `DRIVE_FOLDER`: default `cyberpanel-backups`
- `BACKUP_DIR`: default `/root/backups`
- `STATE_DIR`: default `/var/lib/cyberpanel-backup`
- `LOG_FILE`: default `/var/log/cyberpanel_backup.log`
- `ENCRYPTION_PASSWORD_FILE`: default `/root/.config/cyberpanel-backup/encryption.pass`

## Restore usage

First validate the selected backup chain without changing the server:

```bash
/usr/local/bin/cyberpanel_restore.sh --target-file backup__host-example.com__chain-20260317T030000__type-incremental__at-20260318T030000.tar.gz.enc
```

Apply a real restore:

```bash
/usr/local/bin/cyberpanel_restore.sh \
  --target-file backup__host-example.com__chain-20260317T030000__type-incremental__at-20260318T030000.tar.gz.enc \
  --confirm-host "$(hostname -f)" \
  --apply
```

Optional restore flags:

- `--skip-db`
- `--skip-files`
- `--skip-configs`
- `--skip-services`
- `--keep-workdir`

## CyberPanel integration

The [`serverBackupManager/`](/Users/ademyuce/Documents/CyberPanel/serverBackupManager) directory is a Django app/plugin skeleton for CyberPanel-style integration.

Minimum integration points:

1. Place `serverBackupManager/` inside the target CyberPanel Python/Django codebase.
2. Mount [`urls.py`](/Users/ademyuce/Documents/CyberPanel/serverBackupManager/urls.py) into the panel routing.
3. Set the script paths:

```bash
export CYBERPANEL_SERVER_BACKUP_SCRIPT=/usr/local/bin/cyberpanel_full_backup.sh
export CYBERPANEL_SERVER_RESTORE_SCRIPT=/usr/local/bin/cyberpanel_restore.sh
export CYBERPANEL_SERVER_BACKUP_UI_STATE_DIR=/var/lib/cyberpanel-backup-ui
```

4. Ensure the web process can spawn background jobs and that `rclone` is available on the host.

## Notes

- The backup and restore scripts are designed for Linux servers and must run as `root`.
- The Django app is a plugin skeleton, not a drop-in upstream CyberPanel module.
- Backup files are filtered by host slug, so the restore UI only lists chains that match the current host FQDN.
- Before production use, test a full restore on a staging server.
