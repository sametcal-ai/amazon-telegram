import sqlite3
import logging
import json
from typing import Optional, Tuple
from pathlib import Path
import config

logger = logging.getLogger("Database")


class Database:
    """
    SQLite tabanlı ürün takip, dinamik kanal ve kategori yönetim hafızası.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.init_db()
        self.seed_initial_data()

    def _get_connection(self):
        return sqlite3.connect(self.db_path)

    def init_db(self):
        """Tabloları oluşturur ve şema güncellemelerini (migrations) uygular."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # 1. Deals (Ürünler) Tablosu
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS deals (
                    asin TEXT PRIMARY KEY,
                    title TEXT,
                    price_str TEXT,
                    price_num REAL,
                    url TEXT,
                    image_url TEXT,
                    condition TEXT,
                    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    notification_count INTEGER DEFAULT 1,
                    main_message_id INTEGER,
                    apple_message_id INTEGER,
                    channel_messages TEXT,
                    is_in_stock BOOLEAN DEFAULT 1,
                    missing_cycle_count INTEGER DEFAULT 0
                );
                """
            )
            cursor.execute("PRAGMA table_info(deals)")
            deals_cols = [c[1] for c in cursor.fetchall()]
            if "channel_messages" not in deals_cols:
                cursor.execute("ALTER TABLE deals ADD COLUMN channel_messages TEXT")
            if "main_message_id" not in deals_cols:
                cursor.execute("ALTER TABLE deals ADD COLUMN main_message_id INTEGER")
            if "apple_message_id" not in deals_cols:
                cursor.execute("ALTER TABLE deals ADD COLUMN apple_message_id INTEGER")
            if "is_in_stock" not in deals_cols:
                cursor.execute("ALTER TABLE deals ADD COLUMN is_in_stock BOOLEAN DEFAULT 1")
            if "missing_cycle_count" not in deals_cols:
                cursor.execute("ALTER TABLE deals ADD COLUMN missing_cycle_count INTEGER DEFAULT 0")

            # 2. Channels (Kayıtlı Kanallar) Tablosu
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS channels (
                    chat_id TEXT PRIMARY KEY,
                    title TEXT,
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

            # 3. Categories (Kategoriler ve Eşleşen Kanallar) Tablosu
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT UNIQUE,
                    label TEXT,
                    target_channels TEXT, -- JSON array of channel chat_ids
                    is_active BOOLEAN DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """
            )
            conn.commit()
        logger.info(f"SQLite veritabanı hazırlandı: {self.db_path}")

    def seed_initial_data(self):
        """Mevcut kanalları ve categories.txt dosyasındaki kategorileri veritabanına aktarır."""
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Varsayılan kanalları ekle (eğer boşsa)
            cursor.execute("SELECT COUNT(*) FROM channels")
            if cursor.fetchone()[0] == 0:
                if config.TELEGRAM_CHAT_ID:
                    cursor.execute("INSERT OR IGNORE INTO channels (chat_id, title) VALUES (?, ?)", 
                                   (str(config.TELEGRAM_CHAT_ID), "Scal Amazon (Ana Kanal)"))
                if config.TELEGRAM_APPLE_CHAT_ID:
                    cursor.execute("INSERT OR IGNORE INTO channels (chat_id, title) VALUES (?, ?)", 
                                   (str(config.TELEGRAM_APPLE_CHAT_ID), "Amazon Apple (VIP Kanal)"))
                conn.commit()

            # categories.txt dosyasındaki linkleri veritabanına aktar (eğer boşsa)
            cursor.execute("SELECT COUNT(*) FROM categories")
            if cursor.fetchone()[0] == 0:
                details = config.load_categories_with_details()
                for c in details:
                    lbl = c["label"]
                    url = c["url"]
                    # Eğer Apple/iPhone ise Apple kanalına ata, değilse ana kanala ata
                    is_apple = any(k in lbl.lower() or k in url.lower() for k in ["apple", "iphone"])
                    if is_apple and config.TELEGRAM_APPLE_CHAT_ID:
                        targets = [str(config.TELEGRAM_APPLE_CHAT_ID)]
                    elif config.TELEGRAM_CHAT_ID:
                        targets = [str(config.TELEGRAM_CHAT_ID)]
                    else:
                        targets = []

                    cursor.execute(
                        "INSERT OR IGNORE INTO categories (url, label, target_channels, is_active) VALUES (?, ?, ?, 1)",
                        (url, lbl, json.dumps(targets))
                    )
                conn.commit()

    # =========================================================================
    # KANAL (CHANNELS) YÖNETİMİ
    # =========================================================================

    def get_channels(self, active_only: bool = True) -> list[dict]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            query = "SELECT * FROM channels"
            if active_only:
                query += " WHERE is_active = 1"
            query += " ORDER BY created_at ASC"
            cursor.execute(query)
            return [dict(r) for r in cursor.fetchall()]

    def get_channel(self, chat_id: str) -> Optional[dict]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM channels WHERE chat_id = ?", (str(chat_id),))
            row = cursor.fetchone()
            return dict(row) if row else None

    def add_channel(self, chat_id: str, title: str) -> tuple[bool, str]:
        chat_id_str = str(chat_id).strip()
        title_str = title.strip() or f"Kanal {chat_id_str}"
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT chat_id FROM channels WHERE chat_id = ?", (chat_id_str,))
            if cursor.fetchone():
                cursor.execute("UPDATE channels SET title = ?, is_active = 1 WHERE chat_id = ?", (title_str, chat_id_str))
                conn.commit()
                return True, f"'{title_str}' kanal bilgisi güncellendi."
            cursor.execute("INSERT INTO channels (chat_id, title) VALUES (?, ?)", (chat_id_str, title_str))
            conn.commit()
            return True, f"'{title_str}' kanalı başarıyla eklendi."

    def delete_channel(self, chat_id: str) -> tuple[bool, str]:
        chat_id_str = str(chat_id).strip()
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT title FROM channels WHERE chat_id = ?", (chat_id_str,))
            row = cursor.fetchone()
            if not row:
                return False, "Kanal bulunamadı."
            title = row[0]
            cursor.execute("DELETE FROM channels WHERE chat_id = ?", (chat_id_str,))
            
            # Kategorilerden bu kanalı temizle
            cursor.execute("SELECT id, target_channels FROM categories")
            for cat_id, ch_json in cursor.fetchall():
                try:
                    ch_list = json.loads(ch_json) if ch_json else []
                    if chat_id_str in ch_list:
                        ch_list.remove(chat_id_str)
                        cursor.execute("UPDATE categories SET target_channels = ? WHERE id = ?", (json.dumps(ch_list), cat_id))
                except Exception:
                    pass
            conn.commit()
            return True, f"'{title}' kanalı silindi."

    # =========================================================================
    # KATEGORİ (CATEGORIES) YÖNETİMİ
    # =========================================================================

    def get_all_categories(self, active_only: bool = True) -> list[dict]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            query = "SELECT * FROM categories"
            if active_only:
                query += " WHERE is_active = 1"
            query += " ORDER BY id ASC"
            cursor.execute(query)
            cats = []
            for r in cursor.fetchall():
                d = dict(r)
                try:
                    d["target_channels"] = json.loads(d.get("target_channels") or "[]")
                except Exception:
                    d["target_channels"] = []
                cats.append(d)
            return cats

    def get_category(self, cat_id: int) -> Optional[dict]:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM categories WHERE id = ?", (cat_id,))
            row = cursor.fetchone()
            if not row:
                return None
            d = dict(row)
            try:
                d["target_channels"] = json.loads(d.get("target_channels") or "[]")
            except Exception:
                d["target_channels"] = []
            return d

    def add_category(self, url: str, label: str, target_channels: list[str]) -> tuple[bool, str, Optional[int]]:
        url_clean = url.strip()
        if not (url_clean.startswith("http://") or url_clean.startswith("https://")):
            return False, "Geçersiz URL formatı! URL 'http://' veya 'https://' ile başlamalıdır.", None

        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT id FROM categories WHERE url = ?", (url_clean,))
            if cursor.fetchone():
                return False, "Bu link zaten kategoriler arasında kayıtlı!", None

            target_channels_clean = [str(c) for c in target_channels]
            cursor.execute(
                "INSERT INTO categories (url, label, target_channels, is_active) VALUES (?, ?, ?, 1)",
                (url_clean, label, json.dumps(target_channels_clean))
            )
            cat_id = cursor.lastrowid
            conn.commit()
            return True, "Kategori başarıyla eklendi.", cat_id

    def update_category_channels(self, cat_id: int, target_channels: list[str]) -> tuple[bool, str]:
        target_channels_clean = [str(c) for c in target_channels]
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE categories SET target_channels = ? WHERE id = ?", (json.dumps(target_channels_clean), cat_id))
            conn.commit()
            return True, "Hedef kanallar başarıyla güncellendi."

    def delete_category(self, cat_id: int) -> tuple[bool, str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT label FROM categories WHERE id = ?", (cat_id,))
            row = cursor.fetchone()
            if not row:
                return False, "Kategori bulunamadı."
            label = row[0]
            cursor.execute("DELETE FROM categories WHERE id = ?", (cat_id,))
            conn.commit()
            return True, f"'{label}' kategorisi silindi."

    # =========================================================================
    # FIRSATLAR VE STOK TAKİBİ (DEALS)
    # =========================================================================

    def is_new_or_price_dropped(self, asin: str, current_price_num: Optional[float]) -> Tuple[bool, str]:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT price_num, is_in_stock FROM deals WHERE asin = ?", (asin,))
            row = cursor.fetchone()

            if not row:
                return True, "YENİ_ÜRÜN"

            old_price_num, is_in_stock = row

            if not is_in_stock:
                return True, "TEKRAR_STOKTA"

            if current_price_num is None or old_price_num is None:
                return False, "FİYATSIZ_VEYA_DEĞİŞMEMİŞ"

            if current_price_num < old_price_num - 0.99:
                return True, f"FİYAT_DÜŞTÜ (Eski: {old_price_num:.2f} TL -> Yeni: {current_price_num:.2f} TL)"

            return False, "AYNI_FİYAT"

    def save_or_update_deal(
        self,
        asin: str,
        title: str,
        price_str: str,
        price_num: Optional[float],
        url: str,
        image_url: Optional[str] = None,
        condition: Optional[str] = None,
        main_message_id: Optional[int] = None,
        apple_message_id: Optional[int] = None,
        channel_messages: Optional[dict] = None,
    ):
        with self._get_connection() as conn:
            cursor = conn.cursor()

            # Mevcut kayıtlı mesajları oku
            cursor.execute("SELECT channel_messages, main_message_id, apple_message_id FROM deals WHERE asin = ?", (asin,))
            existing = cursor.fetchone()
            existing_msgs = {}
            if existing:
                ch_msgs_str, m_mid, a_mid = existing
                if ch_msgs_str:
                    try:
                        existing_msgs = json.loads(ch_msgs_str)
                    except Exception:
                        pass
                if not existing_msgs:
                    if m_mid and config.TELEGRAM_CHAT_ID:
                        existing_msgs[str(config.TELEGRAM_CHAT_ID)] = m_mid
                    if a_mid and config.TELEGRAM_APPLE_CHAT_ID:
                        existing_msgs[str(config.TELEGRAM_APPLE_CHAT_ID)] = a_mid

            if channel_messages:
                existing_msgs.update({str(k): v for k, v in channel_messages.items()})

            ch_msgs_json = json.dumps(existing_msgs) if existing_msgs else None

            cursor.execute(
                """
                INSERT INTO deals (
                    asin, title, price_str, price_num, url, image_url, condition,
                    last_notified_at, notification_count, main_message_id, apple_message_id,
                    channel_messages, is_in_stock, missing_cycle_count
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, 1, ?, ?, ?, 1, 0)
                ON CONFLICT(asin) DO UPDATE SET
                    price_str = excluded.price_str,
                    price_num = excluded.price_num,
                    last_notified_at = CURRENT_TIMESTAMP,
                    notification_count = deals.notification_count + 1,
                    main_message_id = COALESCE(excluded.main_message_id, deals.main_message_id),
                    apple_message_id = COALESCE(excluded.apple_message_id, deals.apple_message_id),
                    channel_messages = excluded.channel_messages,
                    is_in_stock = 1,
                    missing_cycle_count = 0
                """,
                (asin, title, price_str, price_num, url, image_url, condition, main_message_id, apple_message_id, ch_msgs_json),
            )
            conn.commit()

    def get_in_stock_deals(self) -> list:
        with self._get_connection() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT * FROM deals 
                WHERE is_in_stock = 1 AND (channel_messages IS NOT NULL OR main_message_id IS NOT NULL OR apple_message_id IS NOT NULL)
                """
            )
            deals = []
            for r in cursor.fetchall():
                d = dict(r)
                msgs = {}
                if d.get("channel_messages"):
                    try:
                        msgs = json.loads(d["channel_messages"])
                    except Exception:
                        pass
                if not msgs:
                    if d.get("main_message_id") and config.TELEGRAM_CHAT_ID:
                        msgs[str(config.TELEGRAM_CHAT_ID)] = d["main_message_id"]
                    if d.get("apple_message_id") and config.TELEGRAM_APPLE_CHAT_ID:
                        msgs[str(config.TELEGRAM_APPLE_CHAT_ID)] = d["apple_message_id"]
                d["channel_messages"] = msgs
                deals.append(d)
            return deals

    def mark_out_of_stock(self, asin: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE deals SET is_in_stock = 0 WHERE asin = ?", (asin,))
            conn.commit()

    def increment_missing_cycle(self, asin: str) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE deals SET missing_cycle_count = missing_cycle_count + 1 WHERE asin = ?", (asin,))
            cursor.execute("SELECT missing_cycle_count FROM deals WHERE asin = ?", (asin,))
            row = cursor.fetchone()
            conn.commit()
            return row[0] if row else 0

    def reset_missing_cycle(self, asin: str):
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("UPDATE deals SET missing_cycle_count = 0, is_in_stock = 1 WHERE asin = ?", (asin,))
            conn.commit()

    def get_total_count(self) -> int:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM deals")
            return cursor.fetchone()[0]

    def get_stats(self) -> dict:
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM deals")
            total = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM deals WHERE is_in_stock = 1")
            in_stock = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM channels WHERE is_active = 1")
            channels_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM categories WHERE is_active = 1")
            categories_count = cursor.fetchone()[0]
            return {
                "total": total,
                "in_stock": in_stock,
                "out_of_stock": total - in_stock,
                "channels": channels_count,
                "categories": categories_count,
            }
