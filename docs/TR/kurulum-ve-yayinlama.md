# CyberPanel Vault Kurulum ve GitHub'a Yukleme

Bu belge, projeyi GitHub'a yayinlamak ve daha sonra sunucuda kullanmak icin gereken temel adimlari icerir.

Publisher ve proje sahibi: [ademyuce.tr](https://ademyuce.tr)

## Onerilen repo bilgileri

- Proje adi: `CyberPanel Vault`
- Repo adi: `cyberpanel-vault`
- Website: `https://ademyuce.tr`
- Kisa aciklama:
  `CyberPanel server backup and restore manager with weekly full backups, incremental chains, Google Drive support, encryption, and a CyberPanel-ready UI.`

## GitHub repo ayarlari

GitHub uzerinde repo olustururken su alanlari doldurun:

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

## GitHub'a yukleme yontemleri

### Yontem 1: Git CLI ile

```bash
git init -b main
git add .
git commit -m "Initial release: CyberPanel Vault"
gh repo create cyberpanel-vault --public --source=. --remote=origin --push
```

Eger `gh` kullanmayacaksaniz:

```bash
git init -b main
git add .
git commit -m "Initial release: CyberPanel Vault"
git remote add origin https://github.com/KULLANICI_ADINIZ/cyberpanel-vault.git
git push -u origin main
```

### Yontem 2: GitHub web arayuzu ile

1. GitHub'da yeni bir repo olusturun.
2. Repo adini `cyberpanel-vault` yapin.
3. Repo aciklamasini ekleyin.
4. Website alanina `https://ademyuce.tr` yazin.
5. Topics alanina onerilen etiketleri ekleyin.
6. Bu klasordeki dosyalari repo icerisine yukleyin.

## SEO ve GEO notu

Bu repo bir GitHub kod reposu olarak hazirlandi. Ayrica bir GitHub Pages landing page tutulmuyor.

SEO ve GEO ayarlari GitHub repo metadata seviyesinde su sekilde ele alinmali:

- Description alanini doldurun
- Website alanina `https://ademyuce.tr` yazin
- Topics alanlarini ekleyin
- README icinde `ademyuce.tr` baglantisini koruyun

## Sunucuya kopyalama

Backup ve restore scriptlerini sunucuda su yollara koyabilirsiniz:

- `/usr/local/bin/cyberpanel_full_backup.sh`
- `/usr/local/bin/cyberpanel_restore.sh`

Calistirilabilir yapmak icin:

```bash
chmod 750 /usr/local/bin/cyberpanel_full_backup.sh
chmod 750 /usr/local/bin/cyberpanel_restore.sh
```

Sifre dosyasi:

- `/root/.config/cyberpanel-backup/encryption.pass`

## CyberPanel plugin iskeletini kullanma

`serverBackupManager/` klasoru, CyberPanel icine entegre edilebilecek bir Django app iskeletidir.

Onu yayinlarken:

1. CyberPanel plugin yapisina gore paketi yerlestirin.
2. `CYBERPANEL_SERVER_BACKUP_SCRIPT` ve `CYBERPANEL_SERVER_RESTORE_SCRIPT` ortam degiskenlerini ayarlayin.
3. `rclone` remote'unun sunucuda calistigini dogrulayin.
4. Restore islemlerini once staging ortaminda test edin.

## Onerilen ilk commit

```bash
git add .
git commit -m "Initial release: CyberPanel Vault"
```
