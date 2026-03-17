# CyberPanel Vault Setup and GitHub Publishing

This document explains how to publish the project to GitHub and prepare it for server deployment.

Publisher and project owner: [ademyuce.tr](https://ademyuce.tr)

## Recommended repository identity

- Project name: `CyberPanel Vault`
- Repository name: `cyberpanel-vault`
- Website: `https://ademyuce.tr`
- Short description:
  `CyberPanel server backup and restore manager with weekly full backups, incremental chains, Google Drive support, encryption, and a CyberPanel-ready UI.`

## Recommended GitHub repository settings

Use the following values in the GitHub repository settings:

- Description:
  `CyberPanel server backup and restore manager with weekly full backups, incremental chains, Google Drive support, encryption, and a CyberPanel-ready UI.`
- Website:
  `https://ademyuce.tr`
- Topics:
  `cyberpanel`
  `backup`
  `restore`
  `google-drive`
  `rclone`
  `incremental-backup`
  `server-management`
  `django`
  `openlitespeed`
  `devops`

## Publishing methods

### Method 1: Git CLI

```bash
git init -b main
git add .
git commit -m "Initial release: CyberPanel Vault"
gh repo create cyberpanel-vault --public --source=. --remote=origin --push
```

If you do not want to use `gh`:

```bash
git init -b main
git add .
git commit -m "Initial release: CyberPanel Vault"
git remote add origin https://github.com/YOUR_USERNAME/cyberpanel-vault.git
git push -u origin main
```

### Method 2: GitHub web UI

1. Create a new repository on GitHub.
2. Set the repository name to `cyberpanel-vault`.
3. Add the recommended description.
4. Set the website field to `https://ademyuce.tr`.
5. Add the suggested topics.
6. Upload the files from this directory.

## SEO and GEO note

This repository is prepared as a GitHub code repository, not as a GitHub Pages landing page.

SEO and GEO signals should be handled at the repository metadata level:

- set the repository description
- set the website field to `https://ademyuce.tr`
- add the recommended topics
- keep the `ademyuce.tr` reference in the README

## Server deployment

Place the scripts on the server at:

- `/usr/local/bin/cyberpanel_full_backup.sh`
- `/usr/local/bin/cyberpanel_restore.sh`

Make them executable:

```bash
chmod 750 /usr/local/bin/cyberpanel_full_backup.sh
chmod 750 /usr/local/bin/cyberpanel_restore.sh
```

Encryption password file:

- `/root/.config/cyberpanel-backup/encryption.pass`

## CyberPanel plugin skeleton

The `serverBackupManager/` directory is a Django app/plugin skeleton intended for CyberPanel integration.

Before using it:

1. Install it according to your CyberPanel plugin structure.
2. Set `CYBERPANEL_SERVER_BACKUP_SCRIPT` and `CYBERPANEL_SERVER_RESTORE_SCRIPT`.
3. Verify the `rclone` remote on the server.
4. Test restores on a staging environment before production use.

## Recommended first commit

```bash
git add .
git commit -m "Initial release: CyberPanel Vault"
```
