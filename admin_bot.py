import json
import logging
import re
import threading
import time
import html
from collections import deque
from typing import Optional, Dict, Any
import requests

import config

logger = logging.getLogger("AdminBot")

# Son log kayıtlarını bellekte tutmak için dairesel tampon (ring buffer)
class LogBufferHandler(logging.Handler):
    def __init__(self, capacity: int = 50):
        super().__init__()
        self.buffer = deque(maxlen=capacity)

    def emit(self, record):
        try:
            msg = self.format(record)
            self.buffer.append(msg)
        except Exception:
            pass

    def get_logs(self, count: int = 15) -> list[str]:
        return list(self.buffer)[-count:]

# Global log tamponu ve formatter
log_buffer = LogBufferHandler(capacity=50)
log_buffer.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] (%(name)s) %(message)s", datefmt="%H:%M:%S"))


class AdminBot:
    """
    Yalnızca yetkili yönetici (ADMIN_USER_ID) ile 1-e-1 Telegram özel sohbeti
    üzerinden çalışan dinamik kanal ve kategori kontrol paneli.
    """

    def __init__(self, db, scan_trigger_event: threading.Event):
        self.db = db
        self.scan_trigger_event = scan_trigger_event
        self.token = config.TELEGRAM_BOT_TOKEN
        self.api_url = f"https://api.telegram.org/bot{self.token}"
        self.admin_user_ids = set(config.ADMIN_USER_IDS)
        self.last_update_id = 0
        self.is_running = False
        self.thread: Optional[threading.Thread] = None

        # Kullanıcı etkileşim ve sihirbaz durumları
        self.user_states: Dict[int, str] = {}
        self.cat_wizard: Dict[int, Dict[str, Any]] = {}

        # Bot istatistikleri
        self.start_time = time.time()
        self.last_scan_time: Optional[str] = None
        self.current_scan_status = "Beklemede 🟢"

        # Log buffer'ın root logger'da kayıtlı olduğundan emin ol
        root_logger = logging.getLogger()
        if log_buffer not in root_logger.handlers:
            root_logger.addHandler(log_buffer)

    def is_authorized(self, user_id: int) -> bool:
        """Kullanıcının yetkili yönetici olup olmadığını doğrular."""
        if not self.admin_user_ids:
            return False
        return user_id in self.admin_user_ids

    def update_scan_status(self, status: str, scan_time: Optional[str] = None):
        """Ana tarayıcı döngüsünden durum bilgisini günceller."""
        self.current_scan_status = status
        if scan_time:
            self.last_scan_time = scan_time

    # =========================================================================
    # TELEGRAM API YARDIMCI METOTLARI
    # =========================================================================

    def _api_call(self, method: str, payload: dict) -> Optional[dict]:
        url = f"{self.api_url}/{method}"
        try:
            resp = requests.post(url, json=payload, timeout=25)
            data = resp.json()
            if not data.get("ok"):
                desc = data.get("description", "")
                if "message is not modified" not in desc:
                    logger.warning(f"Telegram API uyarısı ({method}): {desc}")
            return data
        except Exception as e:
            logger.error(f"Telegram API çağrısı hatası ({method}): {e}")
            return None

    def send_message(self, chat_id: int, text: str, reply_markup: Optional[dict] = None) -> Optional[dict]:
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._api_call("sendMessage", payload)

    def edit_message(self, chat_id: int, message_id: int, text: str, reply_markup: Optional[dict] = None) -> Optional[dict]:
        payload = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        return self._api_call("editMessageText", payload)

    def answer_callback(self, callback_id: str, text: Optional[str] = None, show_alert: bool = False):
        payload = {"callback_query_id": callback_id}
        if text:
            payload["text"] = text
            payload["show_alert"] = show_alert
        self._api_call("answerCallbackQuery", payload)

    # =========================================================================
    # MENÜ VE BUTON ŞABLONLARI
    # =========================================================================

    def get_main_keyboard(self) -> dict:
        return {
            "inline_keyboard": [
                [
                    {"text": "📋 Kategoriler", "callback_data": "btn_categories"},
                    {"text": "➕ Kategori Ekle", "callback_data": "btn_add_category"},
                ],
                [
                    {"text": "📢 Kanallar", "callback_data": "btn_channels"},
                    {"text": "📊 Sistem Durumu", "callback_data": "btn_status"},
                ],
                [
                    {"text": "⚡ Şimdi Tara", "callback_data": "btn_scan_now"},
                    {"text": "📜 Son Loglar", "callback_data": "btn_logs"},
                ],
                [
                    {"text": "ℹ️ Yardım", "callback_data": "btn_help"},
                ],
            ]
        }

    def render_panel_text(self) -> str:
        uptime_sec = int(time.time() - self.start_time)
        hours = uptime_sec // 3600
        minutes = (uptime_sec % 3600) // 60
        uptime_str = f"{hours}s {minutes}dk" if hours > 0 else f"{minutes}dk"

        stats = self.db.get_stats()
        return (
            "🛠️ <b>Amazon Depo Takipçisi - Yönetici Paneli</b>\n\n"
            f"👤 <b>Yönetici:</b> Yetkilendirildi ✅\n"
            f"🟢 <b>Durum:</b> {self.current_scan_status}\n"
            f"⏱️ <b>Çalışma Süresi:</b> {uptime_str}\n"
            f"📦 <b>Kayıtlı Ürün:</b> {stats['total']} (Stokta: {stats['in_stock']})\n"
            f"📋 <b>Kategori Sayısı:</b> {stats.get('categories', 0)}\n"
            f"📢 <b>Bağlı Kanal:</b> {stats.get('channels', 0)}\n\n"
            "Yönetmek istediğiniz bölümü seçebilirsiniz:"
        )

    # =========================================================================
    # KANAL YÖNETİMİ GÖRÜNÜMÜ (CHANNELS)
    # =========================================================================

    def _show_channels(self, chat_id: int, message_id: Optional[int] = None):
        channels = self.db.get_channels()
        if not channels:
            text = "📢 <b>Bağlı Kanal Bulunamadı!</b>\n\nLütfen aşağıdaki butondan en az bir bildirim kanalı ekleyin."
            kb = {
                "inline_keyboard": [
                    [{"text": "➕ Yeni Kanal Ekle", "callback_data": "btn_add_channel"}],
                    [{"text": "🔙 Ana Menü", "callback_data": "btn_main_menu"}],
                ]
            }
        else:
            text = f"📢 <b>Bağlı Bildirim Kanalları ({len(channels)} adet):</b>\n\n"
            buttons = []
            for i, ch in enumerate(channels, 1):
                text += f"<b>{i}️⃣ {html.escape(ch['title'])}</b>\n🆔 <code>{ch['chat_id']}</code>\n\n"
                buttons.append([
                    {"text": f"🗑️ Kaldır: {ch['title'][:20]}", "callback_data": f"btn_del_ch_{ch['chat_id']}"}
                ])

            buttons.append([
                {"text": "➕ Yeni Kanal Ekle", "callback_data": "btn_add_channel"},
                {"text": "🔙 Ana Menü", "callback_data": "btn_main_menu"},
            ])
            kb = {"inline_keyboard": buttons}

        if message_id:
            self.edit_message(chat_id, message_id, text, kb)
        else:
            self.send_message(chat_id, text, kb)

    # =========================================================================
    # KATEGORİ LİSTESİ GÖRÜNÜMÜ (CATEGORIES)
    # =========================================================================

    def _show_categories(self, chat_id: int, message_id: Optional[int] = None):
        cats = self.db.get_all_categories()
        channels = {str(c["chat_id"]): c["title"] for c in self.db.get_channels()}

        if not cats:
            text = "📋 <b>Kayıtlı Kategori Yok!</b>\n\nHenüz takip listesinde arama veya kategori bulunmuyor."
            kb = {
                "inline_keyboard": [
                    [{"text": "➕ Yeni Kategori Ekle", "callback_data": "btn_add_category"}],
                    [{"text": "🔙 Ana Menü", "callback_data": "btn_main_menu"}],
                ]
            }
        else:
            text = f"📋 <b>Aktif Takip Kategorileri ({len(cats)} adet):</b>\n\n"
            buttons = []
            for i, c in enumerate(cats, 1):
                target_names = [channels.get(str(ch_id), f"ID:{ch_id}") for ch_id in c.get("target_channels", [])]
                channel_str = ", ".join(target_names) if target_names else "<i>Hiçbir kanal seçilmedi ⚠️</i>"
                clean_url = c['url'][:50] + "..." if len(c['url']) > 50 else c['url']

                text += (
                    f"<b>{i}️⃣ {html.escape(c['label'])}</b>\n"
                    f"📢 <b>Hedef:</b> {channel_str}\n"
                    f"🔗 <code>{clean_url}</code>\n\n"
                )
                buttons.append([
                    {"text": f"✏️ Kanal Seç: #{i}", "callback_data": f"btn_edit_ch_{c['id']}"},
                    {"text": f"🗑️ Sil: #{i}", "callback_data": f"btn_del_cat_{c['id']}"}
                ])

            buttons.append([
                {"text": "➕ Yeni Kategori Ekle", "callback_data": "btn_add_category"},
                {"text": "🔙 Ana Menü", "callback_data": "btn_main_menu"},
            ])
            kb = {"inline_keyboard": buttons}

        if message_id:
            self.edit_message(chat_id, message_id, text, kb)
        else:
            self.send_message(chat_id, text, kb)

    # =========================================================================
    # İNTERAKTİF KANAL SEÇİM SİHİRBAZI (CHANNEL SELECTOR WIZARD)
    # =========================================================================

    def _render_channel_selector(self, chat_id: int, message_id: Optional[int], user_id: int):
        wizard = self.cat_wizard.get(user_id)
        if not wizard:
            self.send_message(chat_id, "⚠️ Sihirbaz oturumu sona erdi.", self.get_main_keyboard())
            return

        channels = self.db.get_channels()
        if not channels:
            self.send_message(chat_id, "⚠️ Sistemde kayıtlı kanal bulunamadı! Önce bir kanal eklemelisiniz.", self.get_main_keyboard())
            return

        selected = set(wizard.get("selected_channels", []))
        label = wizard.get("label", "Kategori")

        text = (
            f"🎯 <b>Kategori Hedef Kanal Seçimi</b>\n\n"
            f"🏷️ <b>Kategori:</b> {html.escape(label)}\n"
            f"🔗 <b>Link:</b> <code>{wizard['url'][:60]}...</code>\n\n"
            "Bu kategoride yeni bir Depo fırsatı çıktığında <b>hangi kanallara bildirim gitsin?</b>\n"
            "Aşağıdaki kanallara tıklayarak seçin/kaldırın, ardından <b>Kaydet</b> butonuna basın:"
        )

        buttons = []
        for ch in channels:
            ch_id = str(ch["chat_id"])
            is_sel = ch_id in selected
            icon = "✅" if is_sel else "⬜"
            buttons.append([
                {"text": f"{icon} {ch['title'][:25]}", "callback_data": f"wiz_toggle_{ch_id}"}
            ])

        buttons.append([
            {"text": "💾 Kaydet ve Tamamla", "callback_data": "wiz_save"},
            {"text": "❌ İptal", "callback_data": "wiz_cancel"},
        ])
        kb = {"inline_keyboard": buttons}

        if message_id:
            self.edit_message(chat_id, message_id, text, kb)
        else:
            self.send_message(chat_id, text, kb)

    # =========================================================================
    # MESAJ & ETKİLEŞİM İŞLEYİCİSİ
    # =========================================================================

    def handle_message(self, message: dict):
        chat = message.get("chat", {})
        from_user = message.get("from", {})
        user_id = from_user.get("id")
        text = (message.get("text") or "").strip()

        if not self.is_authorized(user_id):
            return

        chat_id = chat.get("id")
        state = self.user_states.get(user_id)

        # -------------------------------------------------------------
        # 1. Kanal Ekleme Durumu
        # -------------------------------------------------------------
        if state == "waiting_for_channel_info":
            if text.lower() in ["/iptal", "iptal", "cancel"]:
                self.user_states.pop(user_id, None)
                self.send_message(chat_id, "❌ Kanal ekleme iptal edildi.", self.get_main_keyboard())
                return

            ch_id = None
            ch_title = None

            # İletilen mesaj kontrolü (forwarded message from channel)
            forward_chat = message.get("forward_from_chat")
            if not forward_chat and "forward_origin" in message:
                forward_chat = message.get("forward_origin", {}).get("chat")

            if forward_chat and forward_chat.get("type") in ["channel", "group", "supergroup"]:
                ch_id = str(forward_chat.get("id"))
                ch_title = forward_chat.get("title") or f"Kanal {ch_id}"
            else:
                # Metin olarak ID ve isim girilmişse: "-100... İsim"
                parts = text.split(maxsplit=1)
                if parts and (parts[0].startswith("-") or parts[0].isdigit()):
                    ch_id = parts[0].strip()
                    ch_title = parts[1].strip() if len(parts) > 1 else f"Kanal {ch_id}"

            if not ch_id:
                self.send_message(
                    chat_id,
                    "⚠️ Kanal ID'si tespit edilemedi.\n\n"
                    "Lütfen kanaldan bir mesaj iletin veya <code>-100xxxxxxx Kanal İsmi</code> şeklinde yazın.\n"
                    "<i>(İptal için /iptal yazabilirsiniz)</i>"
                )
                return

            # Telegram API üzerinden botun bu kanala erişimi var mı doğrula
            chat_info = self._api_call("getChat", {"chat_id": ch_id})
            if not chat_info or not chat_info.get("ok"):
                self.send_message(
                    chat_id,
                    f"⚠️ <b>Bot bu kanala erişemedi!</b> (ID: <code>{ch_id}</code>)\n\n"
                    "Lütfen botu (<code>@scal_amazon_bot</code>) o kanala <b>Yönetici (Admin)</b> olarak eklediğinizden ve mesaj yazma yetkisi verdiğinizden emin olun."
                )
                return

            official_title = chat_info.get("result", {}).get("title") or ch_title
            success, msg = self.db.add_channel(ch_id, official_title)
            self.user_states.pop(user_id, None)

            if success:
                self.send_message(
                    chat_id,
                    f"✅ <b>Kanal Başarıyla Bağlandı!</b>\n\n"
                    f"📢 <b>Başlık:</b> {html.escape(official_title)}\n"
                    f"🆔 <b>Kanal ID:</b> <code>{ch_id}</code>\n\n"
                    "Artık kategorilerinizi eklerken bu kanala yönlendirebilirsiniz!",
                    self.get_main_keyboard()
                )
            else:
                self.send_message(chat_id, f"⚠️ {msg}", self.get_main_keyboard())
            return

        # -------------------------------------------------------------
        # 2. Kategori Linki Gönderme Durumu
        # -------------------------------------------------------------
        if state == "waiting_for_category_url":
            if text.lower() in ["/iptal", "iptal", "cancel"]:
                self.user_states.pop(user_id, None)
                self.cat_wizard.pop(user_id, None)
                self.send_message(chat_id, "❌ Kategori ekleme iptal edildi.", self.get_main_keyboard())
                return

            if not (text.startswith("http://") or text.startswith("https://")):
                self.send_message(chat_id, "⚠️ Geçerli bir URL girmelisiniz (https:// ile başlayan) veya /iptal yazın.")
                return

            if "amazon.com.tr" not in text:
                self.send_message(chat_id, "⚠️ Lütfen amazon.com.tr uzantılı bir link gönderin.")
                return

            # Başlık tahmini
            label = "Amazon Depo Araması"
            if "k=" in text:
                match = re.search(r"[?&]k=([^&]+)", text)
                if match:
                    label = f"Arama: {match.group(1).replace('+', ' ')}"
            elif "node=" in text or "rh=" in text:
                label = "Kategori Sayfası"

            # Sihirbazı başlat
            all_channels = self.db.get_channels()
            default_channels = [str(all_channels[0]["chat_id"])] if all_channels else []

            self.cat_wizard[user_id] = {
                "mode": "new",
                "url": text,
                "label": label,
                "selected_channels": set(default_channels)
            }
            self.user_states.pop(user_id, None)
            self._render_channel_selector(chat_id, None, user_id)
            return

        # -------------------------------------------------------------
        # 3. Genel Komutlar
        # -------------------------------------------------------------
        if text.startswith("/start") or text.startswith("/panel"):
            self.send_message(chat_id, self.render_panel_text(), self.get_main_keyboard())

        elif text.startswith("/kanallar"):
            self._show_channels(chat_id)

        elif text.startswith("/kategoriler"):
            self._show_categories(chat_id)

        elif text.startswith("/kategori_ekle"):
            self.user_states[user_id] = "waiting_for_category_url"
            self.send_message(
                chat_id,
                "➕ <b>Yeni Kategori / Arama Ekle</b>\n\n"
                "Lütfen eklemek istediğiniz Amazon Depo arama veya kategori linkini bu sohbete gönderin:\n"
                "<i>(İptal etmek için /iptal yazabilirsiniz)</i>"
            )

        elif text.startswith("/durum"):
            self._show_status(chat_id)

        elif text.startswith("/tara"):
            self.scan_trigger_event.set()
            self.send_message(chat_id, "⚡ <b>Anlık Tarama Başlatıldı!</b>\nTarayıcı bekleme süresini atlayıp hemen taranmaya başladı.")

        elif text.startswith("/log"):
            self._show_logs(chat_id)

        elif text.startswith("/yardim") or text.startswith("/help"):
            self._show_help(chat_id)

        else:
            self.send_message(chat_id, self.render_panel_text(), self.get_main_keyboard())

    # =========================================================================
    # CALLBACK QUERY İŞLEYİCİSİ (BUTON TIKLAMALARI)
    # =========================================================================

    def handle_callback(self, callback_query: dict):
        cb_id = callback_query.get("id")
        from_user = callback_query.get("from", {})
        user_id = from_user.get("id")
        data = callback_query.get("data", "")
        message = callback_query.get("message", {})
        chat_id = message.get("chat", {}).get("id")
        message_id = message.get("message_id")

        if not self.is_authorized(user_id):
            self.answer_callback(cb_id, "⛔ Yetkiniz yok!", show_alert=True)
            return

        # Ana Menü
        if data == "btn_main_menu":
            self.answer_callback(cb_id)
            self.edit_message(chat_id, message_id, self.render_panel_text(), self.get_main_keyboard())

        # Kanallar
        elif data == "btn_channels":
            self.answer_callback(cb_id)
            self._show_channels(chat_id, message_id)

        elif data == "btn_add_channel":
            self.answer_callback(cb_id)
            self.user_states[user_id] = "waiting_for_channel_info"
            self.send_message(
                chat_id,
                "📢 <b>Yeni Bildirim Kanalı Bağlama</b>\n\n"
                "1. Botu (<code>@scal_amazon_bot</code>) eklemek istediğiniz kanala <b>Yönetici (Admin)</b> yapın.\n"
                "2. Ardından o kanaldan buraya <b>herhangi bir mesaj iletin</b> (veya <code>-100xxxx Kanal Adı</code> şeklinde yazın).\n\n"
                "<i>(İptal için /iptal yazabilirsiniz)</i>"
            )

        elif data.startswith("btn_del_ch_"):
            target_ch_id = data.replace("btn_del_ch_", "")
            success, msg = self.db.delete_channel(target_ch_id)
            self.answer_callback(cb_id, msg, show_alert=True)
            self._show_channels(chat_id, message_id)

        # Kategoriler
        elif data == "btn_categories":
            self.answer_callback(cb_id)
            self._show_categories(chat_id, message_id)

        elif data == "btn_add_category":
            self.answer_callback(cb_id)
            self.user_states[user_id] = "waiting_for_category_url"
            self.send_message(
                chat_id,
                "➕ <b>Yeni Kategori / Arama Ekleme</b>\n\n"
                "Lütfen eklemek istediğiniz Amazon Depo linkini bu sohbete gönderin:\n"
                "<i>(İptal etmek için /iptal yazabilirsiniz)</i>"
            )

        elif data.startswith("btn_del_cat_"):
            cat_id = int(data.replace("btn_del_cat_", ""))
            success, msg = self.db.delete_category(cat_id)
            self.answer_callback(cb_id, msg, show_alert=True)
            self._show_categories(chat_id, message_id)

        elif data.startswith("btn_edit_ch_"):
            cat_id = int(data.replace("btn_edit_ch_", ""))
            cat = self.db.get_category(cat_id)
            if not cat:
                self.answer_callback(cb_id, "Kategori bulunamadı!", show_alert=True)
                return

            self.answer_callback(cb_id)
            self.cat_wizard[user_id] = {
                "mode": f"edit_{cat_id}",
                "cat_id": cat_id,
                "url": cat["url"],
                "label": cat["label"],
                "selected_channels": set(cat.get("target_channels", []))
            }
            self._render_channel_selector(chat_id, message_id, user_id)

        # Sihirbaz Butonları
        elif data.startswith("wiz_toggle_"):
            toggle_id = data.replace("wiz_toggle_", "")
            if user_id in self.cat_wizard:
                sel = self.cat_wizard[user_id].setdefault("selected_channels", set())
                if toggle_id in sel:
                    sel.remove(toggle_id)
                else:
                    sel.add(toggle_id)
                self.answer_callback(cb_id)
                self._render_channel_selector(chat_id, message_id, user_id)

        elif data == "wiz_save":
            wizard = self.cat_wizard.get(user_id)
            if not wizard:
                self.answer_callback(cb_id, "Oturum bulunamadı!", show_alert=True)
                return

            sel_channels = list(wizard.get("selected_channels", []))
            if not sel_channels:
                self.answer_callback(cb_id, "⚠️ Lütfen en az bir hedef kanal seçin!", show_alert=True)
                return

            mode = wizard.get("mode", "new")
            if mode == "new":
                success, msg, cat_id = self.db.add_category(wizard["url"], wizard["label"], sel_channels)
                if success:
                    self.answer_callback(cb_id, "✅ Kategori kaydedildi!", show_alert=True)
                else:
                    self.answer_callback(cb_id, f"⚠️ {msg}", show_alert=True)
            elif mode.startswith("edit_"):
                cat_id = wizard.get("cat_id")
                self.db.update_category_channels(cat_id, sel_channels)
                self.answer_callback(cb_id, "✅ Hedef kanallar güncellendi!", show_alert=True)

            self.cat_wizard.pop(user_id, None)
            self._show_categories(chat_id, message_id)

        elif data == "wiz_cancel":
            self.cat_wizard.pop(user_id, None)
            self.answer_callback(cb_id, "İptal edildi.")
            self._show_categories(chat_id, message_id)

        # Durum, Tarama ve Log
        elif data == "btn_status":
            self.answer_callback(cb_id)
            self._show_status(chat_id, message_id)

        elif data == "btn_scan_now":
            self.scan_trigger_event.set()
            self.answer_callback(cb_id, "⚡ Anlık tarama başlatıldı!", show_alert=True)
            self.send_message(chat_id, "⚡ <b>Anlık Tarama Başlatıldı!</b>\nTarama şu anda yürütülüyor...")

        elif data == "btn_logs":
            self.answer_callback(cb_id, "Loglar getirildi 📜")
            self._show_logs(chat_id, message_id)

        elif data == "btn_help":
            self.answer_callback(cb_id)
            self._show_help(chat_id, message_id)

    # =========================================================================
    # GÖRÜNÜM YARDIMCILARI
    # =========================================================================

    def _show_status(self, chat_id: int, message_id: Optional[int] = None):
        uptime_sec = int(time.time() - self.start_time)
        hours = uptime_sec // 3600
        minutes = (uptime_sec % 3600) // 60
        uptime_str = f"{hours} saat {minutes} dakika" if hours > 0 else f"{minutes} dakika"

        stats = self.db.get_stats()

        text = (
            "📊 <b>Sistem ve Tarayıcı Durumu</b>\n\n"
            f"🟢 <b>Durum:</b> {self.current_scan_status}\n"
            f"⏱️ <b>Son Tarama Zamanı:</b> {self.last_scan_time or 'İlk tarama yapılıyor'}\n"
            f"⏳ <b>Açık Kalma Süresi:</b> {uptime_str}\n"
            f"📦 <b>Toplam Kayıtlı Ürün:</b> {stats['total']}\n"
            f"🛒 <b>Stokta Takip Edilen:</b> {stats['in_stock']}\n"
            f"❌ <b>Tükenen Ürün:</b> {stats['out_of_stock']}\n"
            f"📋 <b>Aktif Kategori Sayısı:</b> {stats.get('categories', 0)}\n"
            f"📢 <b>Bağlı Kanal Sayısı:</b> {stats.get('channels', 0)}\n"
            f"⏱️ <b>Tarama Aralığı:</b> {config.CHECK_INTERVAL_SECONDS} sn (+Jitter)\n"
            f"📄 <b>Sayfa Derinliği:</b> {config.MAX_PAGES_PER_CATEGORY} sayfa/kategori\n"
        )
        kb = {
            "inline_keyboard": [
                [{"text": "🔄 Yenile", "callback_data": "btn_status"}, {"text": "⚡ Şimdi Tara", "callback_data": "btn_scan_now"}],
                [{"text": "🔙 Ana Menü", "callback_data": "btn_main_menu"}],
            ]
        }
        if message_id:
            self.edit_message(chat_id, message_id, text, kb)
        else:
            self.send_message(chat_id, text, kb)

    def _show_logs(self, chat_id: int, message_id: Optional[int] = None):
        logs = log_buffer.get_logs(15)
        now_str = time.strftime("%H:%M:%S")
        if not logs:
            text = (
                "📜 <b>Son Sistem Logları:</b>\n\n"
                "<i>Log kuyruğunda henüz kayıt bulunamadı. Yeni bir tarama döngüsü başladığında loglar buraya yansıyacaktır.</i>\n\n"
                f"⏱️ <i>Kontrol saati: {now_str}</i>"
            )
        else:
            raw_text = "\n".join(logs)
            if len(raw_text) > 3200:
                raw_text = raw_text[-3200:]
            escaped_logs = html.escape(raw_text)
            text = (
                f"📜 <b>Son Sistem Logları ({len(logs)} satır):</b>\n\n"
                f"<pre>{escaped_logs}</pre>\n"
                f"⏱️ <i>Son güncelleme: {now_str}</i>"
            )

        kb = {
            "inline_keyboard": [
                [{"text": "🔄 Yenile", "callback_data": "btn_logs"}],
                [{"text": "🔙 Ana Menü", "callback_data": "btn_main_menu"}],
            ]
        }
        if message_id:
            res = self.edit_message(chat_id, message_id, text, kb)
            if not res or not res.get("ok"):
                self.send_message(chat_id, text, kb)
        else:
            self.send_message(chat_id, text, kb)

    def _show_help(self, chat_id: int, message_id: Optional[int] = None):
        text = (
            "ℹ️ <b>Yönetici Komutları Kılavuzu:</b>\n\n"
            "🔘 <b>/panel</b> veya <b>/start</b> - Kontrol panelini açar.\n"
            "📢 <b>/kanallar</b> - Bağlı bildirim kanallarını yönetir.\n"
            "📋 <b>/kategoriler</b> - Kayıtlı kategorileri ve hedef kanallarını listeler.\n"
            "➕ <b>/kategori_ekle</b> - Yeni Amazon linki ekler ve hedef kanalları seçtirir.\n"
            "📊 <b>/durum</b> - Veritabanı ve tarama durumunu gösterir.\n"
            "⚡ <b>/tara</b> - Beklemeden o anda tarama başlatır.\n"
            "📜 <b>/log</b> - Canlı sistem loglarını getirir.\n\n"
            "<i>Not: Güvenliğiniz için bu bot yalnızca yetkili yönetici hesabından gelen mesajlara yanıt verir.</i>"
        )
        kb = {"inline_keyboard": [[{"text": "🔙 Ana Menü", "callback_data": "btn_main_menu"}]]}
        if message_id:
            self.edit_message(chat_id, message_id, text, kb)
        else:
            self.send_message(chat_id, text, kb)

    # =========================================================================
    # ARKA PLAN DİNLEYİCİSİ (LONG POLLING)
    # =========================================================================

    def _poll_loop(self):
        logger.info(f"Telegram Yönetici Bot dinleyicisi başlatıldı (Yetkili Admin ID: {self.admin_user_ids})")
        session = requests.Session()

        try:
            init_resp = session.get(f"{self.api_url}/getUpdates?offset=-1", timeout=10).json()
            if init_resp.get("ok") and init_resp.get("result"):
                self.last_update_id = init_resp["result"][-1]["update_id"]
        except Exception:
            pass

        while self.is_running:
            try:
                url = f"{self.api_url}/getUpdates"
                params = {
                    "offset": self.last_update_id + 1,
                    "timeout": 15,
                    "allowed_updates": json.dumps(["message", "callback_query"]),
                }
                resp = session.get(url, params=params, timeout=25)
                data = resp.json()

                if not data.get("ok"):
                    time.sleep(2)
                    continue

                for update in data.get("result", []):
                    self.last_update_id = update["update_id"]

                    if "message" in update:
                        self.handle_message(update["message"])
                    elif "callback_query" in update:
                        self.handle_callback(update["callback_query"])

            except requests.exceptions.Timeout:
                continue
            except requests.exceptions.RequestException:
                time.sleep(3)
            except Exception as e:
                logger.error(f"Telegram polling döngüsü hatası: {e}", exc_info=True)
                time.sleep(2)

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.thread = threading.Thread(target=self._poll_loop, daemon=True, name="AdminBotListener")
        self.thread.start()

    def stop(self):
        self.is_running = False
