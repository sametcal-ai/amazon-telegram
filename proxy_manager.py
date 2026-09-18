import os
import time
import logging
from typing import List, Dict, Optional

logger = logging.getLogger("ProxyManager")


class ProxyManager:
    """
    Proxy havuzunu yöneten, CAPTCHA veya bağlantı hatası alındığında
    otomatik olarak sıradaki proxy'ye geçen ve başarısız proxy'leri
    soğumaya (cooldown) alan sınıf.
    """

    def __init__(self, proxy_file: str, cooldown_seconds: int = 300):
        self.proxy_file = proxy_file
        self.cooldown_seconds = cooldown_seconds
        self.proxies: List[str] = []
        self.cooldown_dict: Dict[str, float] = {}  # proxy_url -> expire_timestamp
        self.current_index = 0
        self.load_proxies()

    def load_proxies(self):
        """Proxy dosyasını okur ve standart formatına dönüştürür."""
        if not os.path.exists(self.proxy_file):
            logger.warning(f"Proxy dosyası bulunamadı: {self.proxy_file}. Doğrudan bağlantı kullanılacak.")
            self.proxies = []
            return

        parsed = []
        with open(self.proxy_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                formatted = self._format_proxy_line(line)
                if formatted:
                    parsed.append(formatted)

        self.proxies = parsed
        logger.info(f"Toplam {len(self.proxies)} adet proxy başarıyla yüklendi.")

    def _format_proxy_line(self, line: str) -> Optional[str]:
        """
        Gelen satırı standart 'http://user:pass@ip:port' veya 'http://ip:port' formatına çevirir.
        Kabul edilen formatlar:
        1. IP:PORT:USER:PASS (Kullanıcının ilettiği format)
        2. http://USER:PASS@IP:PORT
        3. IP:PORT
        """
        if line.startswith("http://") or line.startswith("https://"):
            return line

        parts = line.split(":")
        if len(parts) == 4:
            ip, port, user, password = parts
            return f"http://{user}:{password}@{ip}:{port}"
        elif len(parts) == 2:
            ip, port = parts
            return f"http://{ip}:{port}"
        else:
            logger.warning(f"Geçersiz proxy formatı atlandı: {line}")
            return None

    def get_proxy(self) -> Optional[Dict[str, str]]:
        """
        Kullanılabilir bir proxy döndürür.
        Tüm proxy'ler soğumadaysa en eski soğumaya gireni devreye sokar.
        """
        if not self.proxies:
            return None

        now = time.time()
        # Soğuma süresi dolanları temizle
        expired = [p for p, exp in self.cooldown_dict.items() if exp <= now]
        for p in expired:
            del self.cooldown_dict[p]

        # Kullanılabilir (soğumada olmayan) proxy'leri bul
        available = [p for p in self.proxies if p not in self.cooldown_dict]

        if not available:
            logger.warning("Tüm proxy'ler geçici soğuma havuzunda! Soğuması en yakın olan proxy seçiliyor...")
            # En erken süresi dolacak proxy'yi seç ve soğumadan erken çıkar
            oldest_p = min(self.cooldown_dict.keys(), key=lambda k: self.cooldown_dict[k])
            del self.cooldown_dict[oldest_p]
            selected = oldest_p
        else:
            # Sıradaki proxy'yi seç
            self.current_index = self.current_index % len(available)
            selected = available[self.current_index]

        return {
            "http": selected,
            "https": selected,
        }

    def rotate_proxy(self) -> Optional[Dict[str, str]]:
        """Sıradaki proxy'ye manuel geçiş yapar."""
        if not self.proxies:
            return None
        self.current_index = (self.current_index + 1) % len(self.proxies)
        logger.info(f"Proxy değiştirildi, sıradaki proxy seçiliyor...")
        return self.get_proxy()

    def mark_proxy_failed(self, proxy_dict: Optional[Dict[str, str]], reason: str = "Hata/CAPTCHA"):
        """Bir proxy CAPTCHA veya 503 aldığında çağrılır ve soğumaya alınır."""
        if not proxy_dict or "http" not in proxy_dict:
            return

        proxy_url = proxy_dict["http"]
        # Maskelenmiş proxy adresi (log için)
        safe_log_name = proxy_url.split("@")[-1] if "@" in proxy_url else proxy_url
        self.cooldown_dict[proxy_url] = time.time() + self.cooldown_seconds
        logger.warning(
            f"Proxy ({safe_log_name}) {reason} nedeniyle {self.cooldown_seconds} saniye soğumaya alındı. "
            f"Kalan aktif proxy sayısı: {len(self.proxies) - len(self.cooldown_dict)}/{len(self.proxies)}"
        )
        # Hemen sonrakine geçiş için indexi artır
        self.rotate_proxy()
