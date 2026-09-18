import html
import time
import logging
import json
import requests
from typing import Dict, Any, Optional, Tuple
import config

logger = logging.getLogger("Notifier")


class Notifier:
    """
    Telegram ve WhatsApp kanallarına formatlı bildirim gönderen dinamik servis.
    Kategoriler üzerinden belirlenen hedef kanallara dağıtım yapar.
    """

    def __init__(self):
        self.telegram_token = config.TELEGRAM_BOT_TOKEN
        self.telegram_chat_id = str(config.TELEGRAM_CHAT_ID) if config.TELEGRAM_CHAT_ID else ""
        self.telegram_apple_chat_id = str(config.TELEGRAM_APPLE_CHAT_ID) if config.TELEGRAM_APPLE_CHAT_ID else ""
        self.whatsapp_webhook_url = config.WHATSAPP_WEBHOOK_URL

    def format_message_html(self, deal: Dict[str, Any]) -> str:
        """Kullanıcı dostu HTML formatlı bildirim metnini hazırlar."""
        title = html.escape(deal.get("title", "İsimsiz Ürün"))
        price_str = html.escape(deal.get("price_str", "Fiyat Belirtilmemiş"))
        price_num = deal.get("price_num")
        retail_price_str = deal.get("retail_price_str")
        retail_price_num = deal.get("retail_price_num")
        condition = html.escape(deal.get("condition") or "Amazon Depo (Açılmış Kutu/İade)")
        reason = deal.get("reason", "YENİ_ÜRÜN")
        url = deal.get("affiliate_url", deal.get("url", ""))

        header = "🔥 <b>YENİ AMAZON DEPO FIRSATI!</b>"
        if "FİYAT_DÜŞTÜ" in reason:
            header = f"📉 <b>DEPO FİYATI DÜŞTÜ!</b> ({html.escape(reason)})"

        lines = [
            header,
            "",
            f"📦 <b>Ürün:</b> {title}",
        ]

        # Sıfır fiyatı ve tasarruf hesabı (Yalnızca sıfır fiyatı depodan yüksekse ve indirim varsa göster)
        if retail_price_str and retail_price_num and price_num and retail_price_num > price_num:
            lines.append(f"🏷️ <b>Sıfır Fiyatı:</b> <s>{html.escape(retail_price_str)}</s>")
            lines.append(f"💰 <b>Depo Fiyatı:</b> <code>{price_str}</code>")
            savings_tl = retail_price_num - price_num
            savings_pct = round((savings_tl / retail_price_num) * 100)
            savings_formatted = f"{savings_tl:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            lines.append(f"💸 <b>Tasarruf:</b> <b>{savings_formatted} TL</b> (<i>%{savings_pct} İndirim</i>)")
        else:
            lines.append(f"💰 <b>Depo Fiyatı:</b> <code>{price_str}</code>")

        lines.extend([
            f"🔎 <b>Durum:</b> <i>{condition}</i>",
            "",
            f"🛒 <a href=\"{url}\">Amazon'da İncele / Satın Al</a>",
        ])
        return "\n".join(lines)

    def pin_message(self, chat_id: str, message_id: int):
        """Kanalda mesajı başa sabitler (PIN)."""
        try:
            pin_url = f"https://api.telegram.org/bot{self.telegram_token}/pinChatMessage"
            payload = {
                "chat_id": chat_id,
                "message_id": message_id,
                "disable_notification": False,
            }
            res = requests.post(pin_url, json=payload, timeout=10)
            if res.status_code == 200:
                logger.info(f"Mesaj başa sabitlendi (PIN): {message_id} -> {chat_id}")
        except Exception as e:
            logger.debug(f"Mesaj sabitlenirken hata: {e}")

    def unpin_message(self, chat_id: str, message_id: int):
        """Kanalda mesajın sabitlemesini kaldırır (UNPIN)."""
        try:
            unpin_url = f"https://api.telegram.org/bot{self.telegram_token}/unpinChatMessage"
            payload = {"chat_id": chat_id, "message_id": message_id}
            requests.post(unpin_url, json=payload, timeout=10)
        except Exception:
            pass

    def send_telegram(self, deal: Dict[str, Any], chat_id: str) -> Optional[int]:
        """Belirtilen Telegram kanalına resimli ve butonlu bildirim gönderir ve mesaj ID'sini döner."""
        if not self.telegram_token or not chat_id:
            logger.debug("Telegram bilgileri tanımlanmamış, bildirim atlanıyor.")
            return None

        caption = self.format_message_html(deal)
        image_url = deal.get("image_url")
        url = deal.get("affiliate_url", deal.get("url", ""))

        reply_markup = {
            "inline_keyboard": [
                [{"text": "🛒 Ürüne Git (Amazon)", "url": url}]
            ]
        }

        for attempt in range(2):
            try:
                if image_url and image_url.startswith("http"):
                    telegram_url = f"https://api.telegram.org/bot{self.telegram_token}/sendPhoto"
                    payload = {
                        "chat_id": chat_id,
                        "photo": image_url,
                        "caption": caption,
                        "parse_mode": "HTML",
                        "disable_notification": False,
                        "reply_markup": reply_markup,
                    }
                    resp = requests.post(telegram_url, json=payload, timeout=15)
                else:
                    telegram_url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
                    payload = {
                        "chat_id": chat_id,
                        "text": caption,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": False,
                        "reply_markup": reply_markup,
                    }
                    resp = requests.post(telegram_url, json=payload, timeout=15)

                if resp.status_code == 200:
                    data = resp.json()
                    msg_id = data.get("result", {}).get("message_id")
                    logger.info(f"Bildirim başarıyla gönderildi: {deal.get('asin')} -> Kanal {chat_id} (Msg ID: {msg_id})")
                    return msg_id
                elif resp.status_code == 429:
                    retry_after = resp.json().get("parameters", {}).get("retry_after", 3)
                    logger.warning(f"Telegram hız limiti (429)! {retry_after} sn bekleniyor...")
                    time.sleep(retry_after)
                    continue
                else:
                    logger.error(f"Telegram bildirim hatası ({resp.status_code}): {resp.text}")
                    return None

            except Exception as e:
                logger.error(f"Telegram bildirim gönderimi sırasında hata oluştu: {e}")
                time.sleep(2)

        return None

    def format_sold_out_html(self, deal: Dict[str, Any]) -> str:
        """Tükenen ürünler için kullanıcı dostu HTML güncelleme metni hazırlar."""
        title = html.escape(deal.get("title", "İsimsiz Ürün"))
        price_str = html.escape(deal.get("price_str", ""))
        url = deal.get("affiliate_url", deal.get("url", ""))

        lines = [
            "❌ <b>FIRSAT SONA ERDİ / STOK TÜKENDİ!</b> ❌",
            "",
            f"📦 <b>Ürün:</b> <s>{title}</s>",
        ]
        if price_str:
            lines.append(f"💰 <b>Son Fiyat:</b> <s>{price_str}</s>")
        lines.extend([
            "",
            "⏳ <i>Bu ürünün Amazon Depo stoğu tükenmiştir. Yeni fırsatlar gelince anında paylaşılacaktır!</i>",
            "",
            f"🔍 <a href=\"{url}\">Amazon'da Alternatifleri Gör</a>",
        ])
        return "\n".join(lines)

    def _edit_telegram_message(self, chat_id: str, message_id: int, text: str, reply_markup: dict) -> bool:
        """Kanalda gönderilmiş mesajı 'TÜKENDİ' olarak düzenler."""
        caption_url = f"https://api.telegram.org/bot{self.telegram_token}/editMessageCaption"
        payload_cap = {
            "chat_id": chat_id,
            "message_id": message_id,
            "caption": text,
            "parse_mode": "HTML",
            "reply_markup": reply_markup,
        }
        res_cap = requests.post(caption_url, json=payload_cap, timeout=10)
        if res_cap.status_code == 200:
            logger.info(f"Mesaj 'TÜKENDİ' olarak güncellendi (Görsel): {message_id} -> {chat_id}")
            return True

        text_url = f"https://api.telegram.org/bot{self.telegram_token}/editMessageText"
        payload_txt = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": "HTML",
            "reply_markup": reply_markup,
        }
        res_txt = requests.post(text_url, json=payload_txt, timeout=10)
        if res_txt.status_code == 200:
            logger.info(f"Mesaj 'TÜKENDİ' olarak güncellendi (Metin): {message_id} -> {chat_id}")
            return True

        return False

    def mark_sold_out(self, deal: Dict[str, Any]):
        """Ürün tükendiğinde iletildiği tüm Telegram kanallarındaki mesajları 'TÜKENDİ' olarak günceller."""
        caption = self.format_sold_out_html(deal)
        url = deal.get("affiliate_url", deal.get("url", ""))
        reply_markup = {
            "inline_keyboard": [
                [{"text": "❌ Fırsat Bitti / Stok Tükendi", "url": url}]
            ]
        }

        channel_messages = deal.get("channel_messages") or {}
        if isinstance(channel_messages, str):
            try:
                channel_messages = json.loads(channel_messages)
            except Exception:
                channel_messages = {}

        # Geriye dönük uyumluluk: eski sütunları da kontrol et
        if not channel_messages:
            if deal.get("main_message_id") and self.telegram_chat_id:
                channel_messages[self.telegram_chat_id] = deal["main_message_id"]
            if deal.get("apple_message_id") and self.telegram_apple_chat_id:
                channel_messages[self.telegram_apple_chat_id] = deal["apple_message_id"]

        for chat_id, message_id in channel_messages.items():
            if chat_id and message_id:
                self._edit_telegram_message(str(chat_id), int(message_id), caption, reply_markup)
                self.unpin_message(str(chat_id), int(message_id))
                time.sleep(0.5)

    def send_whatsapp(self, deal: Dict[str, Any]) -> bool:
        """Harici WhatsApp webhook köprüsüne istek atar."""
        if not self.whatsapp_webhook_url:
            return False

        payload = {
            "title": deal.get("title"),
            "price": deal.get("price_str"),
            "url": deal.get("affiliate_url", deal.get("url")),
            "image": deal.get("image_url"),
            "condition": deal.get("condition"),
            "text": self.format_message_html(deal).replace("<b>", "").replace("</b>", "").replace("<i>", "").replace("</i>", ""),
        }

        try:
            res = requests.post(self.whatsapp_webhook_url, json=payload, timeout=10)
            return res.status_code in (200, 201, 202)
        except Exception:
            return False

    def broadcast(self, deal: Dict[str, Any]) -> Dict[str, int]:
        """
        Kategoriye göre belirlenmiş hedef kanallara dağıtım yapar.
        Dönüş: {chat_id: message_id} sözlüğü
        """
        target_channels = deal.get("target_channels") or []
        if not target_channels:
            # Hedef kanal belirtilmemişse varsayılan ana kanala gönder
            if self.telegram_chat_id:
                target_channels = [self.telegram_chat_id]

        sent_messages = {}

        for chat_id in target_channels:
            chat_id_str = str(chat_id)
            msg_id = self.send_telegram(deal, chat_id=chat_id_str)
            if msg_id:
                sent_messages[chat_id_str] = msg_id
            time.sleep(1.0)

        # WhatsApp bildirimi (opsiyonel)
        self.send_whatsapp(deal)

        return sent_messages
