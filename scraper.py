import re
import random
import logging
import requests
from bs4 import BeautifulSoup
from typing import List, Dict, Any, Optional
import config
from proxy_manager import ProxyManager

logger = logging.getLogger("Scraper")

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:124.0) Gecko/20100101 Firefox/124.0",
]


class AmazonDepoScraper:
    """
    Amazon Türkiye Depo sayfalarını proxy havuzu ve dinamik rotasyonla
    tarayıp indirimli ürünleri ayrıştıran kazıyıcı.
    """

    def __init__(self, proxy_manager: Optional[ProxyManager] = None):
        self.proxy_manager = proxy_manager
        self.session = requests.Session()
        self.current_proxy = None
        self._refresh_session()

    def _get_random_headers(self) -> Dict[str, str]:
        """Gerçek bir tarayıcıyı taklit eden güncel HTTP başlıkları oluşturur."""
        ua = random.choice(USER_AGENTS)
        is_windows = "Windows" in ua
        platform = '"Windows"' if is_windows else '"macOS"'

        return {
            "User-Agent": ua,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "tr-TR,tr;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.amazon.com.tr/",
            "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": platform,
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }

    def _refresh_session(self):
        """Oturumu ve proxy'yi yeniler."""
        self.session = requests.Session()
        self.session.headers.update(self._get_random_headers())
        if self.proxy_manager:
            self.current_proxy = self.proxy_manager.get_proxy()
            if self.current_proxy:
                self.session.proxies = self.current_proxy

    def is_bot_challenge(self, status_code: int, html_text: str) -> bool:
        """Amazon'un bot engeli, Akamai doğrulaması veya CAPTCHA gösterip göstermediğini doğrular."""
        if status_code in (503, 429, 403):
            return True

        if not html_text or len(html_text) < 500:
            return True

        html_lower = html_text.lower()

        indicators = [
            "validatecaptcha",
            "robot check",
            "tebessüm etmenizi sağlamak için buradayız",
            "enter the characters you see below",
            "automated access",
            "bm-verify",
            "ak_bmsc",
            "challenge-running",
            "server-side-verification",
        ]
        for ind in indicators:
            if ind in html_lower:
                return True

        if "<meta http-equiv=\"refresh\"" in html_lower and "verify" in html_lower:
            return True

        return False

    def clean_price(self, price_str: str) -> Optional[float]:
        """
        Türk Lirası fiyat metnini (Örn: '1.299,90 TL', '11.808,14\xa0TL', '₺549,00') sayısal float değere çevirir.
        """
        if not price_str:
            return None
        try:
            # Non-breaking space ve boşlukları temizle
            price_str = price_str.replace("\xa0", " ").strip()
            # Sadece rakamları, virgülü ve noktayı koru
            cleaned = re.sub(r"[^\d,\.]", "", price_str).strip()
            if "," in cleaned and "." in cleaned:
                cleaned = cleaned.replace(".", "").replace(",", ".")
            elif "," in cleaned:
                cleaned = cleaned.replace(",", ".")
            return float(cleaned)
        except Exception:
            return None

    def build_affiliate_url(self, asin: str) -> str:
        """
        Ürün ASIN kodundan doğrudan Amazon Depo teklifini açan link üretir.
        m=A215JX4S9CANSO parametresi ürünün doğrudan Amazon Depo teklifiyle açılmasını sağlar.
        """
        base = f"https://www.amazon.com.tr/dp/{asin}?m=A215JX4S9CANSO"
        if config.AMAZON_AFFILIATE_TAG:
            return f"{base}&tag={config.AMAZON_AFFILIATE_TAG}"
        return base

    def fetch_page(self, url: str, max_retries: int = 3) -> Optional[str]:
        """
        Sayfayı çeker. CAPTCHA veya bağlantı hatası alınırsa
        otomatik olarak sıradaki proxy'ye geçer ve yeniden dener.
        """
        for attempt in range(1, max_retries + 1):
            try:
                logger.info(f"Amazon sayfası taranıyor (Deneme {attempt}/{max_retries}): {url}")
                response = self.session.get(url, timeout=15)

                if self.is_bot_challenge(response.status_code, response.text):
                    logger.warning(f"Amazon CAPTCHA / Bot Engeli tespit edildi! (Kod: {response.status_code})")
                    if self.proxy_manager and self.current_proxy:
                        self.proxy_manager.mark_proxy_failed(self.current_proxy, "CAPTCHA")
                    self._refresh_session()
                    continue

                if response.status_code == 200:
                    return response.text
                else:
                    logger.warning(f"Beklenmeyen yanıt kodu: {response.status_code}")

            except (requests.RequestException, Exception) as e:
                logger.warning(f"İstek sırasında bağlantı hatası: {e}")
                if self.proxy_manager and self.current_proxy:
                    self.proxy_manager.mark_proxy_failed(self.current_proxy, "Bağlantı Hatası")
                self._refresh_session()

        logger.error(f"{max_retries} deneme sonrası sayfa çekilemedi: {url}")
        return None

    def fetch_retail_price(self, asin: str, depo_price: Optional[float] = None) -> tuple[Optional[str], Optional[float]]:
        """
        Ürünün sıfır (yeni) satış fiyatını ürün detay sayfasından çeker.
        İndirim ve tasarruf miktarını hesaplamak için kullanılır.
        Yalnızca gerçek sıfır satış fiyatı depo fiyatından yüksekse kabul eder.
        """
        try:
            url = f"https://www.amazon.com.tr/dp/{asin}"
            html = self.fetch_page(url, max_retries=3)
            if not html:
                return None, None

            soup = BeautifulSoup(html, "html.parser")

            # 1. Alakasız carousel, tavsiye ve benzer ürün bloklarını DOM'dan temizle
            for junk in soup.select(
                "#similarities_feature_div, .a-carousel, #rhf, #vtpsims, "
                "[data-feature-name='sims-consolidated'], .p13n-desktop-sims, "
                "#aod-ingress-link, .a-text-strike"
            ):
                junk.decompose()

            found_str, found_num = None, None

            # 2. Öncelik 1: Sıfır ürün buybox / accordion alanı
            candidate_selectors = [
                "#newAccordionRow .a-price .a-offscreen",
                "#tp_price_block_total_price_ww .a-price .a-offscreen",
                "div[data-csa-c-buying-option-type='NEW'] .a-price .a-offscreen",
                "#corePriceDisplay_desktop_feature_div:not([data-csa-c-slot-id='usedAccordionRow']) .a-price .a-offscreen",
                "#corePrice_feature_div:not([data-csa-c-slot-id='usedAccordionRow']) .a-price .a-offscreen",
                "#price_inside_buybox",
            ]

            for sel in candidate_selectors:
                for elem in soup.select(sel):
                    txt = elem.get_text(strip=True).replace("\xa0", " ")
                    if txt and ("TL" in txt or "₺" in txt) and re.search(r"\d", txt):
                        num = self.clean_price(txt)
                        if num and (depo_price is None or num > depo_price * 1.01):
                            found_str, found_num = txt, num
                            break
                if found_str:
                    break

            # 3. Öncelik 2: Parçalı fiyat etiketleri (.a-price-whole ve .a-price-fraction)
            if not found_str:
                core = soup.select_one(
                    "#corePriceDisplay_desktop_feature_div:not([data-csa-c-slot-id='usedAccordionRow']), "
                    "#newAccordionRow, #tp_price_block_total_price_ww"
                )
                if core:
                    w = core.select_one(".a-price-whole")
                    f = core.select_one(".a-price-fraction")
                    if w:
                        w_txt = w.get_text(strip=True).replace(".", "").replace(",", "")
                        f_txt = f.get_text(strip=True) if f else "00"
                        num = self.clean_price(f"{w_txt},{f_txt} TL")
                        if num and (depo_price is None or num > depo_price * 1.01):
                            found_str = f"{num:,.2f} TL".replace(",", "X").replace(".", ",").replace("X", ".")
                            found_num = num

            # 4. Öncelik 3: JSON displayString (eğer sıfır teklifiyse)
            if not found_str:
                json_matches = re.findall(r"&quot;displayString&quot;:&quot;([^&]+TL)&quot;", html)
                if json_matches:
                    for jm in json_matches:
                        clean_str = jm.replace("\xa0", " ").strip()
                        num = self.clean_price(clean_str)
                        if num and (depo_price is None or num > depo_price * 1.01):
                            found_str, found_num = clean_str, num
                            break

            if found_str and found_num:
                formatted_str = f"{found_num:,.2f} TL".replace(",", "X").replace(".", ",").replace("X", ".")
                logger.info(f"Sıfır fiyatı başarıyla bulundu ({asin}): {formatted_str}")
                return formatted_str, found_num

        except Exception as e:
            logger.debug(f"Sıfır fiyatı çekilirken hata oluştu ({asin}): {e}")

        logger.info(f"Ürünün sıfır fiyatı bulunamadı veya ikinci el ile aynı/düşük ({asin})")
        return None, None

    def parse_products(self, html: str) -> List[Dict[str, Any]]:
        """HTML içindeki gerçek Amazon Depo (İkinci El / Açık Kutu) ürünlerini ayrıştırır."""
        soup = BeautifulSoup(html, "html.parser")
        products = []

        # Amazon ürün kutucukları (data-asin özniteliğine sahip olanlar)
        items = soup.select("div[data-asin]")

        for item in items:
            asin = item.get("data-asin", "").strip()
            if not asin:
                continue

            # Başlık
            title_elem = item.select_one("h2 span, h2 a span, span.a-size-medium, span.a-size-base-plus")
            if not title_elem:
                continue
            title = title_elem.get_text(strip=True)
            if not title:
                continue

            # GERÇEK AMAZON DEPO DOĞRULAMASI
            # Kart içerisinde ikinci el/depo ibaresi veya satıcı kimliği yoksa (araya giren sıfır ürünse) ATLA
            item_raw_text = item.get_text(" ", strip=True).lower().replace("\xa0", " ")
            hrefs = " ".join([a.get("href", "") for a in item.select("a[href]")])

            is_depo = (
                "ikinci el" in item_raw_text or
                "kullanılmış" in item_raw_text or
                "diğer satın alma seçenekleri" in item_raw_text or
                "a215jx4s9canso" in hrefs.lower() or
                "condition=used" in hrefs.lower()
            )

            if not is_depo:
                continue

            # 1. Fiyat Ayrıştırma (Öncelik: Depo / İkinci El Fiyatı)
            price_str = None

            # a) "Diğer satın alma seçenekleri" altındaki Depo fiyatı
            # Genellikle: <span class="a-color-base">11.808,14 TL</span>
            for span in item.select("span.a-color-base"):
                text_val = span.get_text(strip=True).replace("\xa0", " ")
                if ("TL" in text_val or "₺" in text_val) and re.search(r"\d", text_val):
                    price_str = text_val
                    break

            # b) Doğrudan kutu içi fiyat (.a-price .a-offscreen)
            if not price_str:
                price_elem = item.select_one(".a-price .a-offscreen")
                if price_elem:
                    price_str = price_elem.get_text(strip=True).replace("\xa0", " ")

            # c) Regex ile kart içerisinden TL/₺ fiyatı yakalama
            if not price_str:
                card_text = item.get_text(" ", strip=True).replace("\xa0", " ")
                match = re.search(r"(\d{1,3}(?:\.\d{3})*,\d{2}\s*(?:TL|₺))", card_text)
                if match:
                    price_str = match.group(1).strip()

            if not price_str:
                continue

            price_num = self.clean_price(price_str)

            # Görsel URL
            img_elem = item.select_one("img.s-image")
            image_url = img_elem.get("src") if img_elem else None

            # 2. Ürün Durumu (Kondisyon) Ayrıştırma
            item_raw_text = item.get_text(" ", strip=True).lower()
            condition = "Amazon Depo (İkinci El / Açık Kutu)"

            if "kullanılmış - yeni gibi" in item_raw_text:
                condition = "Kullanılmış - Yeni Gibi"
            elif "kullanılmış - çok iyi" in item_raw_text:
                condition = "Kullanılmış - Çok İyi"
            elif "kullanılmış - iyi" in item_raw_text:
                condition = "Kullanılmış - İyi"
            elif "kullanılmış - kabul edilebilir" in item_raw_text:
                condition = "Kullanılmış - Kabul Edilebilir"
            elif "ikinci el" in item_raw_text:
                condition = "Amazon Depo (İkinci El)"

            # Ürün Linki ve Affiliate Linki
            # Doğrudan Depo teklif listesine veya ürün sayfasına yönlendirme
            affiliate_url = self.build_affiliate_url(asin)

            products.append({
                "asin": asin,
                "title": title,
                "price_str": price_str,
                "price_num": price_num,
                "url": f"https://www.amazon.com.tr/dp/{asin}",
                "affiliate_url": affiliate_url,
                "image_url": image_url,
                "condition": condition,
            })

        logger.info(f"Ayrıştırılan geçerli Amazon Depo ürünü sayısı: {len(products)}")
        return products

    def get_paged_url(self, base_url: str, page: int) -> str:
        """Hedef URL'e sayfalama (pagination) parametresi ekler veya günceller."""
        if page == 1:
            return base_url
        if "page=" in base_url:
            return re.sub(r"page=\d+", f"page={page}", base_url)
        separator = "&" if "?" in base_url else "?"
        return f"{base_url}{separator}page={page}"

    def scrape_all(self, target_categories: List[Any]) -> List[Dict[str, Any]]:
        """Tanımlı tüm hedef sayfaları sayfalama (pagination) derinliğiyle tarar."""
        all_deals = {}
        max_pages = getattr(config, "MAX_PAGES_PER_CATEGORY", 3)

        for cat in target_categories:
            if isinstance(cat, dict):
                base_url = cat.get("url")
                target_channels = cat.get("target_channels", [])
            else:
                base_url = str(cat)
                target_channels = []

            for page in range(1, max_pages + 1):
                paged_url = self.get_paged_url(base_url, page)
                logger.info(f"Kategori taranıyor [Sayfa {page}/{max_pages}]: {paged_url}")
                html = self.fetch_page(paged_url)
                if not html:
                    logger.warning(f"Sayfa çekilemedi, bir sonraki kategoriye geçiliyor: {paged_url}")
                    break

                deals = self.parse_products(html)
                if not deals:
                    logger.info(f"Bu sayfada ({page}) başka ürün bulunamadı, sonraki kategoriye geçiliyor.")
                    break

                for deal in deals:
                    asin = deal["asin"]
                    if asin in all_deals:
                        # Var olan ürünün hedef kanallarını birleştir
                        existing_channels = set(all_deals[asin].get("target_channels", []))
                        existing_channels.update(target_channels)
                        all_deals[asin]["target_channels"] = list(existing_channels)
                    else:
                        deal["target_channels"] = list(target_channels)
                        all_deals[asin] = deal

                # Eğer son sayfaya gelindiyse (pagination-next butonu yoksa veya devre dışıysa) döngüyü sonlandır
                soup = BeautifulSoup(html, "html.parser")
                next_btn = soup.select_one(".s-pagination-next")
                if next_btn and "s-pagination-disabled" in next_btn.get("class", []):
                    logger.info(f"Son sayfaya ulaşıldı: {base_url}")
                    break

        deals_list = list(all_deals.values())
        logger.info(f"Tüm kategorilerden toplanan benzersiz Depo ürünü sayısı: {len(deals_list)}")
        return deals_list

    def check_depo_in_stock(self, asin: str) -> bool:
        """
        Ürünün Amazon Depo'da hala mevcut olup olmadığını doğrular.
        Tükenmiş ürünleri kesin olarak belirlemek için kullanılır.
        """
        try:
            url = f"https://www.amazon.com.tr/dp/{asin}?m=A215JX4S9CANSO"
            html = self.fetch_page(url, max_retries=2)
            if not html:
                return False

            html_lower = html.lower()
            return (
                "a215jx4s9canso" in html_lower or
                "amazon depo" in html_lower or
                "ikinci el" in html_lower
            )
        except Exception as e:
            logger.debug(f"Stok kontrolü sırasında hata ({asin}): {e}")
            return False
