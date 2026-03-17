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

Bu bölüm, daha önce SSH ile sunucuya bağlanmamış biri düşünülerek yazıldı. Komutları tek tek çalıştırın. Bir satır bitmeden diğerine geçmeyin.

0. Sunucuya bağlanın.

Önce bilgisayarınızdan sunucuya `root` olarak bağlanmanız gerekir. Elinizde sunucunun IP adresi ve root parolası olmalı.

```bash
ssh root@SUNUCU_IP_ADRESI
```

İlk bağlantıda bir onay sorusu gelirse `yes` yazıp Enter'a basın. Ardından root parolanızı girin. Yazarken ekranda karakter görünmemesi normaldir.

1. Gerekli paketleri kurun.

Sunucunuzun Linux sürümüne göre aşağıdaki komutlardan birini çalıştırın.

Ubuntu / Debian için:

```bash
apt update
apt install -y git wget unzip rclone openssl rsync
```

AlmaLinux / Rocky / CentOS için:

```bash
dnf install -y git wget unzip rclone openssl rsync
```

Bu adım, projeyi indirmek ve Google Drive bağlantısını kurmak için gereken temel araçları yükler.

2. Proje dosyalarını sunucuya alın.

İki yöntemden birini seçin. Genelde en kolay yol `git clone` kullanmaktır.

Kolay yol, Git ile:

```bash
mkdir -p /opt
git clone https://github.com/adeministratorr/cyberpanel-vault.git /opt/cyberpanel-vault
cd /opt/cyberpanel-vault
```

Alternatif yol, `wget` ile:

```bash
mkdir -p /opt
cd /opt
wget -O cyberpanel-vault-main.zip https://github.com/adeministratorr/cyberpanel-vault/archive/refs/heads/main.zip
unzip -o cyberpanel-vault-main.zip
mv cyberpanel-vault-main cyberpanel-vault
cd /opt/cyberpanel-vault
```

Bu adımın sonunda bulunduğunuz klasörün `/opt/cyberpanel-vault` olması gerekir.

3. Betikleri sistem klasörüne yerleştirin.

Şimdi backup ve restore komutlarını sunucunun her yerinden çalıştırılabilir hale getiriyoruz:

```bash
install -m 750 cyberpanel_full_backup.sh /usr/local/bin/cyberpanel_full_backup.sh
install -m 750 cyberpanel_restore.sh /usr/local/bin/cyberpanel_restore.sh
```

4. Gerekli klasörleri oluşturun.

Bu klasörler yedek durumu, geçici dosyalar ve arayüz kayıtları için kullanılır:

```bash
mkdir -p /root/.config/cyberpanel-backup /var/lib/cyberpanel-backup /var/lib/cyberpanel-backup-ui
chmod 700 /root/.config/cyberpanel-backup /var/lib/cyberpanel-backup /var/lib/cyberpanel-backup-ui
```

5. Şifreleme parolasını oluşturun.

Bu parola çok önemlidir. Çünkü Google Drive'a giden yedek dosyaları bununla şifrelenir. Kısa ve tahmin edilebilir bir parola kullanmayın:

```bash
printf '%s\n' 'GUCLU_UZUN_BIR_SIFRE' >/root/.config/cyberpanel-backup/encryption.pass
chmod 600 /root/.config/cyberpanel-backup/encryption.pass
```

`GUCLU_UZUN_BIR_SIFRE` kısmını kendinize ait uzun bir parola ile değiştirin. Bu dosyayı kaybederseniz yedekleri açamazsınız.

6. Google Drive bağlantısını kurun.

Yedeklerin Google Drive'a gidebilmesi için `rclone` ayarı yapmanız gerekir:

```bash
rclone config
```

Ardından ekrandaki sorularda yeni bir remote oluşturun. Remote adını `gdrive` yazın. Depolama tipi olarak `drive` seçin. Google hesabınızla giriş yapıp yetki verin. İşlem bittiğinde bağlantıyı test edin:

```bash
rclone lsd gdrive:
```

Google Drive içindeki klasörleri görüyorsanız bağlantı hazır demektir.

7. İlk tam yedeği başlatın.

İlk çalıştırmada tam yedek almanız gerekir. Bu işlem sunucudaki veri miktarına göre uzun sürebilir:

```bash
BACKUP_MODE=full /usr/local/bin/cyberpanel_full_backup.sh
```

İşlem sırasında hata alırsanız günlük dosyasına bakın:

```bash
tail -f /var/log/cyberpanel_backup.log
```

8. Otomatik çalışmayı ayarlayın.

Yedeklerin her gün otomatik alınmasını istiyorsanız root kullanıcısının cron tablosuna bir satır ekleyin:

```bash
crontab -e
```

Açılan dosyanın en altına şu satırı ekleyin:

```bash
0 3 * * * BACKUP_MODE=auto /usr/local/bin/cyberpanel_full_backup.sh
```

Kaydedip çıkın. Bundan sonra sistem her gece 03:00'te çalışır. `auto` modunda haftada bir tam yedek alınır, diğer günlerde artımlı yedek oluşturulur.

9. Kurulumun doğru çalıştığını kontrol edin.

Aşağıdaki üç şeyi kontrol edin:

- `rclone lsd gdrive:` komutu hata vermemeli.
- `ls -l /usr/local/bin/cyberpanel_full_backup.sh` çıktısında dosya görünmeli.
- `tail -n 50 /var/log/cyberpanel_backup.log` içinde başarılı yükleme satırları görünmeli.

Bu üç kontrol temizse kurulum büyük ölçüde tamamdır.

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
