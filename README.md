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

- GitHub Pages landing page: [adeministratorr.github.io/cyberpanel-vault](https://adeministratorr.github.io/cyberpanel-vault/)
- Turkish installation and usage page: [adeministratorr.github.io/cyberpanel-vault/TR/kurulum-ve-kullanim/](https://adeministratorr.github.io/cyberpanel-vault/TR/kurulum-ve-kullanim/)
- English installation and usage page: [adeministratorr.github.io/cyberpanel-vault/EN/installation-and-usage/](https://adeministratorr.github.io/cyberpanel-vault/EN/installation-and-usage/)
- Turkish markdown source: [`guides/TR/kurulum-ve-kullanim.md`](/Users/ademyuce/Documents/CyberPanel/guides/TR/kurulum-ve-kullanim.md)
- English markdown source: [`guides/EN/installation-and-usage.md`](/Users/ademyuce/Documents/CyberPanel/guides/EN/installation-and-usage.md)

## Requirements

- Linux server with `root` access
- `rclone`, `mysql`, `mysqldump`, `openssl`, `tar`, `gzip`, `sha256sum`, `flock`, `rsync`
- CyberPanel MySQL root password file at `/etc/cyberpanel/mysqlPassword`
- An `rclone` remote for Google Drive, or equivalent values for `RCLONE_REMOTE` and `DRIVE_FOLDER`
- Encryption password file at `/root/.config/cyberpanel-backup/encryption.pass`
- Optional: a local `sendmail`-compatible MTA if you want email notifications from the UI

## Installation

1. Fetch the project files on your server.

Clone with Git:

```bash
mkdir -p /opt
git clone https://github.com/adeministratorr/cyberpanel-vault.git /opt/cyberpanel-vault
cd /opt/cyberpanel-vault
git checkout v0.2.1
```

Download with `wget`:

```bash
mkdir -p /opt
cd /opt
wget -O cyberpanel-vault-v0.2.1.zip https://github.com/adeministratorr/cyberpanel-vault/archive/refs/tags/v0.2.1.zip
unzip -o cyberpanel-vault-v0.2.1.zip
mv cyberpanel-vault-0.2.1 cyberpanel-vault
cd /opt/cyberpanel-vault
```

The `wget` path requires the `unzip` package on the server.

2. Copy the shell scripts to your server:

```bash
install -m 750 cyberpanel_full_backup.sh /usr/local/bin/cyberpanel_full_backup.sh
install -m 750 cyberpanel_restore.sh /usr/local/bin/cyberpanel_restore.sh
```

3. Create the required runtime directories:

```bash
mkdir -p /root/.config/cyberpanel-backup /var/lib/cyberpanel-backup /var/lib/cyberpanel-backup-ui
chmod 700 /root/.config/cyberpanel-backup /var/lib/cyberpanel-backup /var/lib/cyberpanel-backup-ui
```

4. Create the encryption password file:

```bash
printf '%s\n' 'CHANGE_THIS_TO_A_LONG_RANDOM_SECRET' >/root/.config/cyberpanel-backup/encryption.pass
chmod 600 /root/.config/cyberpanel-backup/encryption.pass
```

5. Configure `rclone` so the backup host can write to your Google Drive remote. The default remote name is `gdrive`.

If you use the default root config path, keep the file readable only by root:

```bash
chmod 600 /root/.config/rclone/rclone.conf
```

6. Run the first backup manually:

```bash
BACKUP_MODE=full /usr/local/bin/cyberpanel_full_backup.sh
```

Only the database, site files or server settings can also be backed up selectively:

```bash
BACKUP_MODE=full BACKUP_COMPONENTS=databases /usr/local/bin/cyberpanel_full_backup.sh
BACKUP_MODE=full BACKUP_COMPONENTS=site,server /usr/local/bin/cyberpanel_full_backup.sh
```

7. Schedule regular runs with `BACKUP_MODE=auto`. In `auto` mode the script takes a weekly full backup and incremental backups between full runs.

Example cron:

```bash
0 3 * * * BACKUP_MODE=auto /usr/local/bin/cyberpanel_full_backup.sh
```

## Backup usage

- Automatic mode: `BACKUP_MODE=auto /usr/local/bin/cyberpanel_full_backup.sh`
- Force a full backup: `BACKUP_MODE=full /usr/local/bin/cyberpanel_full_backup.sh`
- Force an incremental backup: `BACKUP_MODE=incremental /usr/local/bin/cyberpanel_full_backup.sh`
- Only back up selected areas: `BACKUP_COMPONENTS=databases`, `BACKUP_COMPONENTS=site`, `BACKUP_COMPONENTS=server`, `BACKUP_COMPONENTS=email`

Important runtime variables:

- `RCLONE_REMOTE`: default `gdrive`
- `DRIVE_FOLDER`: default `cyberpanel-backups`
- `BACKUP_DIR`: default `/root/backups`
- `STATE_DIR`: default `/var/lib/cyberpanel-backup`
- `LOG_FILE`: default `/var/log/cyberpanel_backup.log`
- `ENCRYPTION_PASSWORD_FILE`: default `/root/.config/cyberpanel-backup/encryption.pass`
- `ENCRYPTION_PASSWORD_COMMAND`: optional command for fetching the encryption secret from a vault or secret manager at runtime
- `BACKUP_COMPONENTS`: default `all`, available values `databases,site,server,email`

Different component combinations are kept in separate backup chains. For example, a database-only backup and a site-only backup do not share the same incremental state.

## Restore usage

First validate the selected backup chain without changing the server:

```bash
/usr/local/bin/cyberpanel_restore.sh --target-file backup__host-example.com__profile-all__chain-20260317T030000__type-incremental__at-20260318T030000.tar.gz.enc
```

Apply a real restore:

```bash
/usr/local/bin/cyberpanel_restore.sh \
  --target-file backup__host-example.com__profile-all__chain-20260317T030000__type-incremental__at-20260318T030000.tar.gz.enc \
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

Fastest installation path on a CyberPanel host:

```bash
cd /opt/cyberpanel-vault
bash install_cyberpanel_integration.sh
```

If your CyberPanel web process does not run as the default `cyberpanel` user, pass the user explicitly:

```bash
WEB_USER=YOUR_WEB_USER bash install_cyberpanel_integration.sh
```

After installation, run the verification script:

```bash
bash test_cyberpanel_integration.sh --panel-url https://panel.example.com/server-backup/
```

The installer copies the Django app into the CyberPanel codebase, installs the shell scripts, writes a restricted root runner, patches `settings.py` and `urls.py`, and creates a `sudoers` rule so the panel can launch root-only backup jobs safely.

The UI shows the saved timeout, schedule, notification and component selections when the page loads. Manual and scheduled backups can be limited to `databases`, `site`, `server` and `email`, and each component combination keeps its own incremental chain.

If the host has a local `sendmail`-compatible MTA, the UI can also send a message to the admin address when a backup finishes successfully or ends with an error.

Manual integration points:

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
