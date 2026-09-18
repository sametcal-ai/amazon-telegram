import time
import random
import logging
import sys
import threading
from datetime import datetime
import config
from proxy_manager import ProxyManager
from database import Database
from notifier import Notifier
from scraper import AmazonDepoScraper
from admin_bot import AdminBot, log_buffer

# Log yapılandırması (Docker ve konsol için canlı çıktı + Telegram yönetim paneli için tampon)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] (%(name)s) %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        log_buffer,
    ],
    force=True,
)

logger = logging.getLogger("Main")


def print_banner():
    banner = """
    ======================================================
    🔥 AMAZON DEPO FIRSAT & BİLDİRİM BOTU
    📦 Satıcı: Amazon Depo (Warehouse Deals)
    ⚡ Modül: Proxy Rotasyonu, Dedup, Telegram & WhatsApp
    ======================================================
    """
    print(banner, flush=True)


def run_cycle(scraper: AmazonDepoScraper, db: Database, notifier: Notifier):
    """Tek bir tarama döngüsünü yürütür."""
    logger.info("Tarama döngüsü başlatılıyor...")
    categories = db.get_all_categories(active_only=True)
    if not categories:
        target_urls = config.load_category_urls()
        categories = [{"url": u, "target_channels": [config.TELEGRAM_CHAT_ID]} for u in target_urls]

    deals = scraper.scrape_all(categories)

    if not deals:
        logger.info("Bu döngüde ürün bulunamadı veya sayfalar boş döndü.")
        return

    current_cycle_asins = set()
    new_deals_count = 0

    for deal in deals:
        asin = deal["asin"]
        current_cycle_asins.add(asin)
        title = deal.get("title", "")
        price_num = deal["price_num"]

        # Anahtar kelime/marka filtresi varsa kontrol et
        if config.FILTER_KEYWORDS:
            title_lower = title.lower()
            matched = any(kw in title_lower for kw in config.FILTER_KEYWORDS)
            if not matched:
                logger.debug(f"Kelime filtresine uymadığı için atlandı: {title}")
                continue

        should_notify, reason = db.is_new_or_price_dropped(asin, price_num)

        if should_notify:
            new_deals_count += 1
            deal["reason"] = reason

            # Sıfır perakende satış fiyatını ve tasarruf miktarını çek
            retail_str, retail_num = scraper.fetch_retail_price(asin, depo_price=price_num)
            deal["retail_price_str"] = retail_str
            deal["retail_price_num"] = retail_num

            logger.info(
                f"FIRSAT TESPİT EDİLDİ [{reason}]: {deal['title']} | Depo: {deal['price_str']} | Sıfır: {retail_str or 'N/A'}"
            )

            # Hedef kanallara dağıt
            channel_messages = notifier.broadcast(deal)

            # Veritabanına kaydet/güncelle (mesaj ID'leri dahil)
            db.save_or_update_deal(
                asin=asin,
                title=deal["title"],
                price_str=deal["price_str"],
                price_num=deal["price_num"],
                url=deal["url"],
                image_url=deal["image_url"],
                condition=deal["condition"],
                channel_messages=channel_messages,
            )

            # Telegram hız limitine takılmamak için kısa bekleme
            time.sleep(1.0)
        else:
            # Ürün zaten veritabanında var ve listede görülüyor -> sayacı sıfırla
            db.reset_missing_cycle(asin)

    # -------------------------------------------------------------
    # STOK TÜKENME KONTROLÜ (Out-of-Stock Verification)
    # -------------------------------------------------------------
    logger.info("Aktif ürünler için stok tükenme kontrolü yapılıyor...")
    active_deals = db.get_in_stock_deals()

    for active_deal in active_deals:
        active_asin = active_deal["asin"]
        # Eğer ürün mevcut taramada görülmediyse
        if active_asin not in current_cycle_asins:
            missing_count = db.increment_missing_cycle(active_asin)
            # En az 2 döngü arka arkaya görülmediyse doğrudan ürün sayfasından teyit et
            if missing_count >= 2:
                in_stock = scraper.check_depo_in_stock(active_asin)
                if not in_stock:
                    logger.info(f"ÜRÜNÜN DEPO STOĞU BİTTİ: {active_deal['title']} ({active_asin})")
                    notifier.mark_sold_out(active_deal)
                    db.mark_out_of_stock(active_asin)
                    time.sleep(1.0)
                else:
                    # Hala mevcut (arama sıralamasında geriye düşmüş olabilir), sayacı sıfırla
                    db.reset_missing_cycle(active_asin)

    total_tracked = db.get_total_count()
    logger.info(
        f"Döngü tamamlandı. Yeni/güncellenen fırsat: {new_deals_count} | Veritabanında kayıtlı toplam ürün: {total_tracked}"
    )


def main():
    print_banner()

    # 1. Proxy Yöneticisi
    proxy_manager = ProxyManager(config.PROXIES_FILE)

    # 2. SQLite Veritabanı
    db = Database(config.DATABASE_PATH)

    # 3. Bildirim Motoru
    notifier = Notifier()

    # 4. Amazon Depo Kazıyıcı
    scraper = AmazonDepoScraper(proxy_manager=proxy_manager)

    # 5. Anlık Tetikleme Olayı ve Telegram Yönetici Bot Paneli
    scan_trigger_event = threading.Event()
    admin_bot = AdminBot(db, scan_trigger_event)
    admin_bot.start()

    logger.info(f"Bot çalışmaya başladı. Temel bekleme süresi: {config.CHECK_INTERVAL_SECONDS} sn.")

    while True:
        try:
            now_str = datetime.now().strftime("%H:%M:%S")
            admin_bot.update_scan_status("Taranıyor... 🔍", now_str)
            run_cycle(scraper, db, notifier)
            admin_bot.update_scan_status("Beklemede 🟢", datetime.now().strftime("%H:%M:%S"))
        except KeyboardInterrupt:
            logger.info("Bot kullanıcı tarafından durduruldu (SIGINT). Çıkılıyor...")
            admin_bot.stop()
            break
        except Exception as e:
            logger.error(f"Döngü sırasında beklenmeyen hata oluştu: {e}", exc_info=True)
            admin_bot.update_scan_status("Hata / Beklemede ⚠️")

        # İnsan davranışını taklit etmek için rastgele jitter ekle
        jitter = random.randint(config.JITTER_MIN_SECONDS, config.JITTER_MAX_SECONDS)
        wait_time = config.CHECK_INTERVAL_SECONDS + jitter
        logger.info(f"Bir sonraki tarama için {wait_time} saniye bekleniyor...\n")

        # Zaman aşımını bekle veya Telegram'dan /tara komutu gelirse hemen uyan
        interrupted = scan_trigger_event.wait(timeout=wait_time)
        if interrupted:
            logger.info("Telegram üzerinden anlık tarama tetiklendi! Döngü başlatılıyor...")
        scan_trigger_event.clear()


if __name__ == "__main__":
    main()
