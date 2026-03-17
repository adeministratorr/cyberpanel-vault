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

## Server Backup Manager kurulumu ve kullanımı

`serverBackupManager/` klasörü, doğrudan CyberPanel içine bağlanabilecek bir Django uygulama iskeletidir. Yani bu bölüm, normal shell script kurulumundan farklıdır; panelin Python tarafına dosya eklemeniz gerekir.

### Bu bölüm ne işe yarar?

Server Backup Manager kurulduğunda yedekleme ve geri yükleme işlemlerini terminal yerine panel benzeri bir ekrandan yönetebilirsiniz. Bu ekran şunları yapar:

- Tam, artımlı veya otomatik yedek başlatır.
- Google Drive üzerindeki bu sunucuya ait yedek zincirlerini listeler.
- Seçilen zincir için geri yükleme işi başlatır.
- Son işleri ve günlük kayıtlarını ekranda gösterir.

### Kuruluma başlamadan önce bilinmesi gerekenler

- Önce shell script tarafı çalışıyor olmalıdır. Yani `cyberpanel_full_backup.sh` ve `cyberpanel_restore.sh` kurulu olmalı.
- Google Drive bağlantısı hazır olmalıdır. Bunun için `rclone lsd gdrive:` komutu hata vermemeli.
- Bu bölüm, CyberPanel'in Django yapısına dosya eklemeyi gerektirir. Yani sadece kopyala ve çalıştır mantığında değildir.
- Bu ekranı kullanacak kişinin panelde yönetici yetkisi olmalıdır.

### 1. Uygulama klasörünü CyberPanel kod tabanına kopyalayın

Önce bu repo içindeki `serverBackupManager/` klasörünü, CyberPanel'in Django uygulamalarının bulunduğu yere kopyalayın. Hedef klasör sizin sunucunuzdaki CyberPanel kurulumuna göre değişebilir. Mantık şudur: Bu klasör, diğer Django app'lerin durduğu yerde olmalıdır.

```bash
cp -a /opt/cyberpanel-vault/serverBackupManager /CYBERPANEL_DJANGO_KLASORU/serverBackupManager
```

`/CYBERPANEL_DJANGO_KLASORU` kısmını kendi sunucunuzdaki gerçek klasörle değiştirin.

### 2. Uygulamayı Django ayarlarına ekleyin

CyberPanel'in `settings.py` dosyasında `INSTALLED_APPS` listesine şu satırı ekleyin:

```python
'serverBackupManager.apps.ServerBackupManagerConfig',
```

Bu satır eklenmezse Django uygulamayı tanımaz.

### 3. URL bağlantısını ekleyin

CyberPanel'in ana `urls.py` dosyasında `include` kullanarak bu uygulamanın yollarını bağlayın.

```python
from django.urls import include, path

urlpatterns = [
    # diğer yollar
    path("server-backup/", include("serverBackupManager.urls")),
]
```

Bu örnekte ekran şu adreste açılır: `/server-backup/`

### 4. Gerekli ortam değişkenlerini tanımlayın

Panelin web süreci, hangi backup ve restore betiğini çağıracağını bu değişkenlerden öğrenir. En az şu üç değişkeni tanımlayın:

```bash
export CYBERPANEL_SERVER_BACKUP_SCRIPT=/usr/local/bin/cyberpanel_full_backup.sh
export CYBERPANEL_SERVER_RESTORE_SCRIPT=/usr/local/bin/cyberpanel_restore.sh
export CYBERPANEL_SERVER_BACKUP_UI_STATE_DIR=/var/lib/cyberpanel-backup-ui
```

Bu değişkenler, web sürecisinin gördüğü ortamda tanımlı olmalıdır. Sadece terminalde yazmanız her zaman yeterli olmaz. CyberPanel hangi servisle çalışıyorsa, bu değişkenlerin o servise de verilmesi gerekir.

### 5. State klasörünü oluşturun

Arayüz, iş kayıtlarını ve log dosyalarını burada tutar:

```bash
mkdir -p /var/lib/cyberpanel-backup-ui/jobs
chmod 700 /var/lib/cyberpanel-backup-ui /var/lib/cyberpanel-backup-ui/jobs
```

### 6. Web sürecisini yeniden başlatın

`settings.py`, `urls.py` ve ortam değişkenleri eklendikten sonra CyberPanel'in web tarafını yeniden başlatmanız gerekir. Hangi servis kullanılıyorsa onu yeniden başlatın.

Bu adım sunucudan sunucuya değiştiği için tek bir komut vermek doğru olmaz. Ama mantık aynıdır: Django tarafı yeniden yüklenmelidir.

### 7. Sayfanın açıldığını kontrol edin

Tarayıcıdan eklediğiniz yolu açın. Örneğin üstteki örneği kullandıysanız:

```text
https://panel-adresiniz/server-backup/
```

Bu sayfayı açarken yönetici hesabıyla oturum açmış olmanız gerekir.

Sayfa açıldığında şunları görmeniz gerekir:

- Sunucu adı
- Yedekleme başlatma alanı
- Uzak yedek zincirleri tablosu
- Geri yükleme formu
- Son işler ve günlük bölümü

### Server Backup Manager nasıl kullanılır?

Kurulum tamamlandıktan sonra günlük kullanım oldukça basittir.

### 1. Yeni yedek başlatma

Sayfadaki **Yedekleme Başlat** alanında mod seçin:

- `Otomatik`: Haftalık tam, diğer günler artımlı çalışır.
- `Tam`: O anda yeni bir tam yedek alır.
- `Artımlı`: Son tam yedeğin üstüne artımlı yedek alır.

Ardından **Yedeklemeyi Başlat** düğmesine basın. İş arka planda başlar.

### 2. Yedeklerin durumunu izleme

Sayfanın alt kısmındaki **Son İşler** alanında başlatılan işleri görürsünüz. Burada işin:

- tipi
- durumu
- başlama zamanı

yer alır. **Günlüğü Aç** düğmesine basarsanız işlem çıktısını ekranda görürsünüz.

### 3. Geri yükleme başlatma

**Uzak Yedek Zincirleri** bölümünde Google Drive üzerindeki bu sunucuya ait yedekler listelenir. Buradan bir hedef seçin. Seçtiğiniz dosya otomatik olarak geri yükleme kutusuna gelir.

Sonra şu adımları izleyin:

1. **Hedef yedek** alanının dolu olduğunu kontrol edin.
2. **Onay için sunucunun FQDN değerini yazın** alanına sunucunun tam adını yazın.
3. Gerekirse `Veritabanını atla`, `Dosyaları atla` gibi seçenekleri kullanın.
4. **Geri Yüklemeyi Başlat** düğmesine basın.

Bu işlem canlı sisteme yazdığı için dikkatli kullanılmalıdır.

### 4. İlk kullanımda güvenli test önerisi

Arayüzü doğrudan üretim sunucusunda denemek yerine önce küçük bir test yapın:

- Önce panelden bir tam yedek başlatın.
- İş başarıyla tamamlandıktan sonra Google Drive'da dosyanın oluştuğunu kontrol edin.
- Ardından mümkünse test ortamında geri yükleme deneyin.

### Sorun çıktığında nerelere bakılır?

- Panelde iş görünmüyorsa `settings.py` ve `urls.py` bağlantısını kontrol edin.
- İş başlıyor ama hemen düşüyorsa ortam değişkenleri eksik olabilir.
- Uzak yedek listesi boşsa `rclone lsd gdrive:` komutunu terminalde test edin.
- Log görmek için arayüzde **Günlüğü Aç** düğmesini kullanın.

## Operasyon notları

- Betikler `root` olarak çalışmalıdır.
- Geri yükleme sırasında mevcut sunucunun FQDN değeri doğrulanır.
- Arayüz yalnızca o sunucuya ait yedek zincirlerini listeler.
- Üretime almadan önce tam geri yükleme senaryosunu test ortamında deneyin.
