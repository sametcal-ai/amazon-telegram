import os
from pathlib import Path
from dotenv import load_dotenv

# .env dosyasını yükle
load_dotenv()

BASE_DIR = Path(__file__).resolve().parent

# Telegram Bildirim Ayarları
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_APPLE_CHAT_ID = os.getenv("TELEGRAM_APPLE_CHAT_ID", "").strip()
ADMIN_USER_IDS_RAW = os.getenv("ADMIN_USER_IDS", os.getenv("ADMIN_USER_ID", "")).strip()
ADMIN_USER_IDS = [
    int(uid.strip()) for uid in ADMIN_USER_IDS_RAW.split(",") if uid.strip().isdigit()
]

# WhatsApp Bildirim Ayarları (Opsiyonel Webhook)
WHATSAPP_WEBHOOK_URL = os.getenv("WHATSAPP_WEBHOOK_URL", "").strip()

# Amazon Gelir Ortaklığı (Affiliate) Takip Kodu
AMAZON_AFFILIATE_TAG = os.getenv("AMAZON_AFFILIATE_TAG", "").strip()

# Tarama ve Bekleme Aralıkları (Saniye)
CHECK_INTERVAL_SECONDS = int(os.getenv("CHECK_INTERVAL_SECONDS", "60"))
JITTER_MIN_SECONDS = int(os.getenv("JITTER_MIN_SECONDS", "5"))
JITTER_MAX_SECONDS = int(os.getenv("JITTER_MAX_SECONDS", "20"))
# Her kategori veya arama linki için taranacak sayfa derinliği
MAX_PAGES_PER_CATEGORY = int(os.getenv("MAX_PAGES_PER_CATEGORY", "3"))

# Proxy ve Dosya Yolları
PROXIES_FILE = os.getenv("PROXIES_FILE", str(BASE_DIR / "proxies.txt"))
DATABASE_PATH = os.getenv("DATABASE_PATH", str(BASE_DIR / "data" / "deals.db"))

# Veritabanı klasörünün varlığını garanti et
Path(DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)

# Hedef Amazon Depo URL'leri (Node: 44219324031 - Satıcı: A215JX4S9CANSO)
AMAZON_BASE_URL = os.getenv("AMAZON_BASE_URL", "https://www.amazon.com.tr")

# 1. En son eklenen Amazon Depo fırsatları (Tarihe göre sıralı)
DEPO_LATEST_URL = os.getenv(
    "DEPO_LATEST_URL",
    "https://www.amazon.com.tr/s?i=warehouse-deals&srs=44219324031&s=date-desc-rank"
)

# 2. Popüler Amazon Depo fırsatları
DEPO_POPULAR_URL = os.getenv(
    "DEPO_POPULAR_URL",
    "https://www.amazon.com.tr/s?i=warehouse-deals&srs=44219324031&s=popularity-rank"
)

# 3. Doğrudan Amazon Depo satıcı mağazası
DEPO_SELLER_URL = os.getenv(
    "DEPO_SELLER_URL",
    "https://www.amazon.com.tr/s?me=A215JX4S9CANSO"
)

# Anahtar Kelime / Marka Filtresi (İsteğe Bağlı)
# Örn: 'iphone,apple' yazılırsa sadece başlığında bu kelimeler geçen ürünler bildirilir.
# Boş bırakılırsa tüm Depo ürünleri bildirilir.
FILTER_KEYWORDS_RAW = os.getenv("FILTER_KEYWORDS", "").strip()
FILTER_KEYWORDS = [
    k.strip().lower() for k in FILTER_KEYWORDS_RAW.split(",") if k.strip()
]

# Kategori Dosyası (İsteğe bağlı categories.txt)
CATEGORIES_FILE = os.getenv("CATEGORIES_FILE", str(BASE_DIR / "categories.txt"))

def load_categories_with_details():
    """
    categories.txt dosyasını okur ve indeks, etiket ve URL içeren liste döner.
    Format: [{'index': 1, 'label': 'Apple Cep Telefonları', 'url': '...'}]
    """
    categories = []
    if not os.path.exists(CATEGORIES_FILE):
        return categories

    with open(CATEGORIES_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()

    last_comment = ""
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            clean_comment = stripped.lstrip("#").strip()
            if clean_comment and not clean_comment.startswith("="):
                last_comment = clean_comment
        elif stripped.startswith("http://") or stripped.startswith("https://"):
            label = last_comment if last_comment else f"Kategori {len(categories) + 1}"
            categories.append({
                "index": len(categories) + 1,
                "label": label,
                "url": stripped
            })
            last_comment = ""
    return categories

def load_category_urls():
    """
    Tarama için sadece aktif kategori URL'lerini döner.
    """
    details = load_categories_with_details()
    urls = [item["url"] for item in details]
    if not urls:
        urls = [DEPO_LATEST_URL, DEPO_POPULAR_URL]

    extra = os.getenv("EXTRA_TARGET_URLS", "")
    if extra:
        for u in extra.split(","):
            u_clean = u.strip()
            if u_clean and u_clean not in urls:
                urls.append(u_clean)
    return urls

def add_category_url(url: str, label: str = None) -> tuple[bool, str]:
    """
    categories.txt dosyasına yeni kategori URL'si ekler.
    """
    url = url.strip()
    if not (url.startswith("http://") or url.startswith("https://")):
        return False, "Geçersiz URL formatı! URL 'http://' veya 'https://' ile başlamalıdır."

    current_urls = load_category_urls()
    if url in current_urls:
        return False, "Bu link zaten kategoriler arasında ekli!"

    with open(CATEGORIES_FILE, "a", encoding="utf-8") as f:
        if label:
            f.write(f"\n# {label}\n{url}\n")
        else:
            f.write(f"\n# Telegram ile eklendi\n{url}\n")
    return True, "Kategori başarıyla eklendi."

def remove_category_url(index: int) -> tuple[bool, str]:
    """
    Belirtilen 1-tabanlı indeksteki kategoriyi categories.txt dosyasından kaldırır.
    """
    categories = load_categories_with_details()
    if index < 1 or index > len(categories):
        return False, f"Geçersiz numara! 1 ile {len(categories)} arasında bir numara seçmelisiniz."

    target = categories[index - 1]
    target_url = target["url"]

    if not os.path.exists(CATEGORIES_FILE):
        return False, "categories.txt dosyası bulunamadı."

    with open(CATEGORIES_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()

    new_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped == target_url:
            if new_lines and new_lines[-1].strip().startswith("#") and not new_lines[-1].strip().startswith("# ="):
                new_lines.pop()
            i += 1
            continue
        new_lines.append(line)
        i += 1

    with open(CATEGORIES_FILE, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    return True, f"'{target['label']}' başarıyla silindi."

# Başlangıçta geriye dönük uyumluluk için
TARGET_URLS = load_category_urls()
