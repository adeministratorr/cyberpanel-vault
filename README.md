# CyberPanel Vault

CyberPanel Vault is a server-level backup and restore toolkit for CyberPanel deployments. It combines weekly full backups, incremental backup chains, encrypted Google Drive uploads through `rclone`, and a CyberPanel-ready management UI for running backup and restore jobs.

Project website and publisher: [ademyuce.tr](https://ademyuce.tr)

Suggested GitHub repository name: `cyberpanel-vault`

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

- Turkish publishing guide: [`docs/TR/kurulum-ve-yayinlama.md`](/Users/ademyuce/Documents/CyberPanel/docs/TR/kurulum-ve-yayinlama.md)
- English publishing guide: [`docs/EN/setup-and-publishing.md`](/Users/ademyuce/Documents/CyberPanel/docs/EN/setup-and-publishing.md)

## Suggested GitHub metadata

- Repository name: `cyberpanel-vault`
- Description:
  `CyberPanel server backup and restore manager with weekly full backups, incremental chains, Google Drive support, encryption, and a CyberPanel-ready UI.`
- Website:
  `https://ademyuce.tr`
- Topics:
  `cyberpanel`, `backup`, `restore`, `google-drive`, `rclone`, `incremental-backup`, `server-management`, `django`, `openlitespeed`, `devops`

## Quick publish

Using Git CLI:

```bash
git init -b main
git add .
git commit -m "Initial release: CyberPanel Vault"
gh repo create cyberpanel-vault --public --source=. --remote=origin --push
```

Using GitHub web UI:

1. Create a new repository named `cyberpanel-vault`.
2. Set the website field to `https://ademyuce.tr`.
3. Add the suggested description and topics.
4. Upload the files from this directory.
5. Upload the repository contents.

## Notes

- The backup and restore scripts are designed for Linux servers and must run as `root`.
- The Django app is a plugin skeleton, not a drop-in upstream CyberPanel module.
- Before production use, test a full restore on a staging server.
