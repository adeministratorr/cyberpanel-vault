# CyberPanel Vault Kurulum ve Kullanim

Bu belge, `CyberPanel Vault` projesini bir CyberPanel sunucusuna kurmak ve guvenli sekilde kullanmak icin gereken temel adimlari toplar.

Yayinci: [Adem YÜCE](https://ademyuce.tr) - [ademyuce.tr](https://ademyuce.tr)

## Ne saglar

- Haftalik tam yedek
- Aradaki kosularda incremental dosya ve config yedegi
- Her kosuda tam veritabani dump'i
- `openssl` ile sifrelenmis arsiv
- `rclone` ile Google Drive yuklemesi
- Zincirli restore akisi
- CyberPanel arayuzune baglanabilecek Django app iskeleti

## Gereksinimler

- Linux sunucu ve `root` erisimi
- `rclone`
- `mysql` ve `mysqldump`
- `openssl`
- `tar`, `gzip`, `sha256sum`, `flock`, `rsync`
- `/etc/cyberpanel/mysqlPassword` dosyasi
- Google Drive icin hazir `rclone` remote'u
- Sifreleme parola dosyasi

## Kurulum

1. Scriptleri sunucuya kopyalayin:

```bash
install -m 750 cyberpanel_full_backup.sh /usr/local/bin/cyberpanel_full_backup.sh
install -m 750 cyberpanel_restore.sh /usr/local/bin/cyberpanel_restore.sh
```

2. Durum ve parola dizinlerini olusturun:

```bash
mkdir -p /root/.config/cyberpanel-backup /var/lib/cyberpanel-backup /var/lib/cyberpanel-backup-ui
chmod 700 /root/.config/cyberpanel-backup /var/lib/cyberpanel-backup /var/lib/cyberpanel-backup-ui
```

3. Sifreleme dosyasini olusturun:

```bash
printf '%s\n' 'GUCLU_UZUN_BIR_SIFRE' >/root/.config/cyberpanel-backup/encryption.pass
chmod 600 /root/.config/cyberpanel-backup/encryption.pass
```

4. `rclone` tarafinda Google Drive remote'unu hazirlayin. Varsayilan remote adi `gdrive` olarak beklenir.

5. Ilk tam yedegi manuel alin:

```bash
BACKUP_MODE=full /usr/local/bin/cyberpanel_full_backup.sh
```

6. Sonraki gunluk kosular icin `auto` modunu zamanlayin:

```bash
0 3 * * * BACKUP_MODE=auto /usr/local/bin/cyberpanel_full_backup.sh
```

`auto` modunda script haftada bir tam yedek, diger kosularda incremental yedek alir.

## Kullanim

Backup modlari:

- `BACKUP_MODE=auto`
- `BACKUP_MODE=full`
- `BACKUP_MODE=incremental`

Ornekler:

```bash
BACKUP_MODE=auto /usr/local/bin/cyberpanel_full_backup.sh
BACKUP_MODE=full /usr/local/bin/cyberpanel_full_backup.sh
BACKUP_MODE=incremental /usr/local/bin/cyberpanel_full_backup.sh
```

Onemli ortam degiskenleri:

- `RCLONE_REMOTE`, varsayilan `gdrive`
- `DRIVE_FOLDER`, varsayilan `cyberpanel-backups`
- `BACKUP_DIR`, varsayilan `/root/backups`
- `STATE_DIR`, varsayilan `/var/lib/cyberpanel-backup`
- `LOG_FILE`, varsayilan `/var/log/cyberpanel_backup.log`
- `ENCRYPTION_PASSWORD_FILE`, varsayilan `/root/.config/cyberpanel-backup/encryption.pass`

## Restore

Canli degisiklik yapmadan once zinciri dogrulayin:

```bash
/usr/local/bin/cyberpanel_restore.sh --target-file backup__host-example.com__chain-20260317T030000__type-incremental__at-20260318T030000.tar.gz.enc
```

Gercek restore:

```bash
/usr/local/bin/cyberpanel_restore.sh \
  --target-file backup__host-example.com__chain-20260317T030000__type-incremental__at-20260318T030000.tar.gz.enc \
  --confirm-host "$(hostname -f)" \
  --apply
```

Opsiyonel bayraklar:

- `--skip-db`
- `--skip-files`
- `--skip-configs`
- `--skip-services`
- `--keep-workdir`

## CyberPanel arayuzune baglama

`serverBackupManager/` dizini bir Django app iskeletidir.

Temel adimlar:

1. Dizini CyberPanel Django kod tabanina ekleyin.
2. `urls.py` icindeki route'lari panel URL yapisina baglayin.
3. Asagidaki degiskenleri ayarlayin:

```bash
export CYBERPANEL_SERVER_BACKUP_SCRIPT=/usr/local/bin/cyberpanel_full_backup.sh
export CYBERPANEL_SERVER_RESTORE_SCRIPT=/usr/local/bin/cyberpanel_restore.sh
export CYBERPANEL_SERVER_BACKUP_UI_STATE_DIR=/var/lib/cyberpanel-backup-ui
```

4. Web prosesi arka plan job baslatabilmeli ve host uzerinde `rclone` erisimi bulunmalidir.

## Operasyon notlari

- Scriptler `root` olarak calismalidir.
- Restore tarafinda host FQDN dogrulamasi vardir.
- UI tarafinda yalnizca mevcut host slug ile eslesen yedek zincirleri listelenir.
- Uretimde kullanmadan once staging uzerinde tam restore testi yapin.
