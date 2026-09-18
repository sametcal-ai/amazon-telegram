# 🛒 Amazon Depo Fırsat & Bildirim Botu

Amazon Türkiye Depo (**Warehouse Deals** - satıcı: `A1UNQM1SR2CHM`) ürünlerini periyodik olarak tarayan, yeni veya fiyatı düşen açık kutu/iade fırsatlarını tespit eden, Amazon Gelir Ortaklığı (Affiliate) kodunu ekleyip **Telegram** ve **WhatsApp** kanallarına anlık bildirim atan otomatik takip botu.

---

## ✨ Özellikler

* **🛡️ Dinamik Proxy Rotasyonu & Havuzu:** 
  * `proxies.txt` dosyasındaki proxy'leri (`IP:PORT:USER:PASS`) otomatik olarak yönetir.
  * CAPTCHA, 503 veya bağlantı hatası alındığında o proxy'yi geçici soğumaya alır ve **anında sıradaki proxy'ye geçer**.
* **🧠 Çift Bildirimi Önleme (SQLite Dedup):**
  * Daha önce bildirilen ürünler veritabanına kaydedilir.
  * Aynı ürün tekrar tekrar bildirilmez; sadece **ürün ilk defa stoğa girdiğinde** veya **fiyatı daha da düştüğünde** bildirim tetiklenir.
* **💸 Gelir Ortaklığı (Affiliate Entegrasyonu):**
  * Ürün bağlantılarına otomatik olarak `?tag=seninkodun-21` referans kodunu ekler.
* **📱 Zengin Bildirimler:**
  * **Telegram:** Ürün görseli, başlık, indirimli fiyat ve tek tıkla satın alma butonu (`sendPhoto` & Inline Keyboard).
  * **WhatsApp:** Harici webhook (Green API, Baileys HTTP köprüsü vb.) desteği.
* **🐳 Docker & Docker Compose:**
  * Tek bir komutla sunucuda veya bilgisayarda arka planda kesintisiz çalışma.
  * Kalıcı veritabanı (`./data` volume).

---

## 🚀 Hızlı Başlangıç (Docker ile)

### 1. Yapılandırma (`.env`)
Proje dizinindeki `.env` dosyasını bir metin düzenleyiciyle açın ve bilgilerinizi girin:

```env
# Telegram Ayarları (Zorunlu)
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjkLMNoPQRs
TELEGRAM_CHAT_ID=@firsat_kanaliniz

# Amazon Gelir Ortaklığı Kodunuz (İsteğe Bağlı)
AMAZON_AFFILIATE_TAG=firsatbot-21

# Tarama Sıklığı (Saniye)
CHECK_INTERVAL_SECONDS=60
```

> **Telegram Botu ve Chat ID Nasıl Alınır?**
> 1. Telegram'da `@BotFather` botunu açın ve `/newbot` komutunu gönderin.
> 2. Botunuza bir isim ve kullanıcı adı verin. Size verilen **API Token**'ı kopyalayıp `TELEGRAM_BOT_TOKEN=` karşısına yapıştırın.
> 3. Bir Telegram Kanalı veya Grubu oluşturun.
> 4. Oluşturduğunuz botu kanala/gruba **Yönetici (Admin)** olarak ekleyin.
> 5. Eğer kanalınız herkese açıksa `TELEGRAM_CHAT_ID=@kanal_kullanici_adiniz` şeklinde yazabilirsiniz.

---

### 2. Proxy Listesi (`proxies.txt`)
Verilen 10 adet proxy `proxies.txt` dosyasına eklenmiştir. Format:
```text
IP:PORT:KULLANICI_ADI:SIFRE
```
İleride yeni proxy eklemek isterseniz bu dosyaya yeni satırlar eklemeniz yeterlidir (Docker'ı yeniden build etmeniz gerekmez).

---

### 3. Docker ile Çalıştırma

Terminalden proje dizininde şu komutu verin:

```bash
docker compose up -d --build
```

Bot arka planda çalışmaya başlayacaktır. 

Canlı logları takip etmek için:
```bash
docker compose logs -f
```

Botu durdurmak için:
```bash
docker compose down
```

---

## 💻 Docker Olmadan Çalıştırma (Doğrudan Python)

Eğer Docker kullanmak istemiyorsanız yerel bilgisayarınızda şu adımlarla çalıştırabilirsiniz:

```bash
# 1. Sanal ortam oluşturun
python3 -m venv venv
source venv/bin/activate  # Windows için: venv\Scripts\activate

# 2. Gerekli kütüphaneleri yükleyin
pip install -r requirements.txt

# 3. Botu başlatın
python main.py
```

---

## 📁 Proje Dosya Yapısı

```text
├── data/                 # SQLite veritabanı (Docker volume)
├── proxies.txt           # Proxy listesi (IP:PORT:USER:PASS)
├── config.py             # Yapılandırma ve .env okuyucu
├── proxy_manager.py      # Proxy rotasyon ve CAPTCHA soğuma yöneticisi
├── database.py           # SQLite çift bildirim & fiyat takip motoru
├── scraper.py            # Amazon Depo ürün kazıyıcı ve parser
├── notifier.py           # Telegram & WhatsApp bildirim modülü
├── main.py               # Ana çalışma döngüsü (Jitter & hata toleranslı)
├── Dockerfile            # Docker imaj tanımı
├── docker-compose.yml    # Docker Compose orkestrasyonu
└── .env                  # Gizli anahtarlar ve ayarlar
```
