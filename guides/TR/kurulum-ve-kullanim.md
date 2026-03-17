# CyberPanel Vault Kurulum ve Kullanım

Bu kılavuz, `CyberPanel Vault`'u bir CyberPanel sunucusuna kurmak ve günlük kullanımda sorunsuz biçimde çalıştırmak için hazırlandı.

Yayıncı: [Adem YÜCE](https://ademyuce.tr) - [ademyuce.tr](https://ademyuce.tr)

## Neler sunar

- Haftada bir tam yedek
- Tam yedekler arasındaki çalışmalarda artımlı dosya ve yapılandırma yedeği
- Her çalışmada tam veritabanı dökümü
- `openssl` ile şifrelenmiş arşiv
- `rclone` ile Google Drive aktarımı
- Zincir mantığıyla çalışan geri yükleme akışı
- CyberPanel arayüzüne bağlanabilecek Django uygulama iskeleti

## Gereksinimler

- `root` erişimine sahip bir Linux sunucu
- `rclone`
- `mysql` ve `mysqldump`
- `openssl`
- `tar`, `gzip`, `sha256sum`, `flock`, `rsync`
- `/etc/cyberpanel/mysqlPassword` dosyası
- Google Drive için hazır bir `rclone` remote'u
- Şifreleme için parola dosyası

## Kurulum

1. Proje dosyalarını sunucuya alın.

Git ile:

```bash
mkdir -p /opt
git clone https://github.com/adeministratorr/cyberpanel-vault.git /opt/cyberpanel-vault
cd /opt/cyberpanel-vault
```

`wget` ile:

```bash
mkdir -p /opt
cd /opt
wget -O cyberpanel-vault-main.zip https://github.com/adeministratorr/cyberpanel-vault/archive/refs/heads/main.zip
unzip -o cyberpanel-vault-main.zip
mv cyberpanel-vault-main cyberpanel-vault
cd /opt/cyberpanel-vault
```

Bu yöntem için sunucuda `unzip` paketinin kurulu olması gerekir.

2. Betikleri sunucuya kopyalayın:

```bash
install -m 750 cyberpanel_full_backup.sh /usr/local/bin/cyberpanel_full_backup.sh
install -m 750 cyberpanel_restore.sh /usr/local/bin/cyberpanel_restore.sh
```

3. Durum ve parola dizinlerini oluşturun:

```bash
mkdir -p /root/.config/cyberpanel-backup /var/lib/cyberpanel-backup /var/lib/cyberpanel-backup-ui
chmod 700 /root/.config/cyberpanel-backup /var/lib/cyberpanel-backup /var/lib/cyberpanel-backup-ui
```

4. Şifreleme dosyasını oluşturun:

```bash
printf '%s\n' 'GUCLU_UZUN_BIR_SIFRE' >/root/.config/cyberpanel-backup/encryption.pass
chmod 600 /root/.config/cyberpanel-backup/encryption.pass
```

5. `rclone` tarafında Google Drive bağlantısını hazırlayın. Betikler varsayılan olarak `gdrive` adlı remote'u kullanır.

6. İlk yedeği elle başlatın:

```bash
BACKUP_MODE=full /usr/local/bin/cyberpanel_full_backup.sh
```

7. Günlük çalışma için `auto` modunu zamanlayın:

```bash
0 3 * * * BACKUP_MODE=auto /usr/local/bin/cyberpanel_full_backup.sh
```

`auto` modunda betik haftada bir tam yedek alır; aradaki çalışmalarda artımlı yedek üretir.

## Kullanım

Kullanabileceğiniz yedekleme modları:

- `BACKUP_MODE=auto`
- `BACKUP_MODE=full`
- `BACKUP_MODE=incremental`

Örnek:

```bash
BACKUP_MODE=auto /usr/local/bin/cyberpanel_full_backup.sh
BACKUP_MODE=full /usr/local/bin/cyberpanel_full_backup.sh
BACKUP_MODE=incremental /usr/local/bin/cyberpanel_full_backup.sh
```

Sık kullanılan ortam değişkenleri:

- `RCLONE_REMOTE`, varsayılan `gdrive`
- `DRIVE_FOLDER`, varsayılan `cyberpanel-backups`
- `BACKUP_DIR`, varsayılan `/root/backups`
- `STATE_DIR`, varsayılan `/var/lib/cyberpanel-backup`
- `LOG_FILE`, varsayılan `/var/log/cyberpanel_backup.log`
- `ENCRYPTION_PASSWORD_FILE`, varsayılan `/root/.config/cyberpanel-backup/encryption.pass`

## Geri yükleme

Canlı sistemi değiştirmeden önce zinciri doğrulayın:

```bash
/usr/local/bin/cyberpanel_restore.sh --target-file backup__host-example.com__chain-20260317T030000__type-incremental__at-20260318T030000.tar.gz.enc
```

Gerçek geri yükleme için:

```bash
/usr/local/bin/cyberpanel_restore.sh \
  --target-file backup__host-example.com__chain-20260317T030000__type-incremental__at-20260318T030000.tar.gz.enc \
  --confirm-host "$(hostname -f)" \
  --apply
```

İsterseniz şu seçeneklerle kısmi geri yükleme yapabilirsiniz:

- `--skip-db`
- `--skip-files`
- `--skip-configs`
- `--skip-services`
- `--keep-workdir`

## CyberPanel arayüzüne bağlama

`serverBackupManager/` dizini, CyberPanel içine eklenebilecek bir Django uygulama iskeletidir.

Temel adımlar:

1. Dizini CyberPanel'in Django kod tabanına ekleyin.
2. `urls.py` içindeki yolları panelin mevcut URL yapısına bağlayın.
3. Gerekli ortam değişkenlerini tanımlayın:

```bash
export CYBERPANEL_SERVER_BACKUP_SCRIPT=/usr/local/bin/cyberpanel_full_backup.sh
export CYBERPANEL_SERVER_RESTORE_SCRIPT=/usr/local/bin/cyberpanel_restore.sh
export CYBERPANEL_SERVER_BACKUP_UI_STATE_DIR=/var/lib/cyberpanel-backup-ui
```

4. Web sürecinin arka planda iş başlatabildiğinden ve sunucuda `rclone` erişimi olduğundan emin olun.

## Operasyon notları

- Betikler `root` olarak çalışmalıdır.
- Geri yükleme sırasında mevcut sunucunun FQDN değeri doğrulanır.
- Arayüz yalnızca o sunucuya ait yedek zincirlerini listeler.
- Üretime almadan önce tam geri yükleme senaryosunu test ortamında deneyin.
