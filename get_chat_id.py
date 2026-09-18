import sys
import time
import requests
import config

TOKEN = config.TELEGRAM_BOT_TOKEN

if not TOKEN:
    print("Hata: .env dosyasında TELEGRAM_BOT_TOKEN tanımlı değil!")
    sys.exit(1)

print("=" * 60)
print("TELEGRAM KANAL ID BULUCU")
print("=" * 60)
print(f"Bot Token: {TOKEN[:10]}...{TOKEN[-5:]}")
print("\nLütfen şu adımları tamamlayın:")
print("1. Telegram kanalınıza gidin.")
print("2. @scal_amazon_bot botunu kanalınıza YÖNETİCİ (Admin) olarak ekleyin.")
print("3. Kanalda herhangi bir test mesajı yazın (Örn: 'test').")
print("\nKanal ID bekleniyor (kontrol ediliyor, çıkmak için Ctrl+C)...")

last_update_id = 0
found_chat_id = None

while not found_chat_id:
    try:
        url = f"https://api.telegram.org/bot{TOKEN}/getUpdates"
        res = requests.get(url, params={"offset": last_update_id + 1}, timeout=10)
        data = res.json()

        if not data.get("ok"):
            print(f"API Hatası: {data}")
            time.sleep(3)
            continue

        for update in data.get("result", []):
            last_update_id = update["update_id"]

            # Kanal mesajı kontrolü
            chat = None
            if "channel_post" in update:
                chat = update["channel_post"]["chat"]
            elif "message" in update:
                chat = update["message"]["chat"]
            elif "my_chat_member" in update:
                chat = update["my_chat_member"]["chat"]

            if chat:
                chat_id = chat.get("id")
                title = chat.get("title", chat.get("username", "Bilinmeyen"))
                print(f"\n🎉 TEBRİKLER! Kanal tespit edildi:")
                print(f"📌 Kanal Başlığı: {title}")
                print(f"🔑 Kanal Chat ID: {chat_id}")

                # .env dosyasına otomatik yaz
                env_path = config.BASE_DIR / ".env"
                if env_path.exists():
                    with open(env_path, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    with open(env_path, "w", encoding="utf-8") as f:
                        for line in lines:
                            if line.startswith("TELEGRAM_CHAT_ID="):
                                f.write(f"TELEGRAM_CHAT_ID={chat_id}\n")
                            else:
                                f.write(line)
                    print(f"✅ TELEGRAM_CHAT_ID={chat_id} değeri doğrudan .env dosyasına kaydedildi!")

                found_chat_id = chat_id
                break

    except KeyboardInterrupt:
        print("\nİşlem iptal edildi.")
        sys.exit(0)
    except Exception as e:
        print(f"Bağlantı hatası: {e}")

    time.sleep(2)

print("\nKurulum hazır! Artık 'docker compose up -d --build' ile botu başlatabilirsiniz.")
