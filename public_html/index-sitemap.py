import os
import json
import requests
import xml.etree.ElementTree as ET
from urllib.parse import urlparse
from google.oauth2 import service_account
from google.auth.transport.requests import Request


# ---------- Конфигурация ----------
SITEMAP_URL = "https://dream-catcher-project.ru/sitemap.xml"   # замените на ваш URL
CREDENTIALS_FILE = "credentials.json"                  # путь к ключу сервисного аккаунта
MAX_URLS = 200                                         # лимит Google Indexing API
# ----------------------------------


def fetch_sitemap(url):
    """Загружает sitemap по URL (поддерживает сжатие gzip автоматически)"""
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    # Если контент сжат gzip, requests распакует автоматически при headers
    return resp.text


def parse_sitemap(xml_content, base_url=None):
    """
    Парсит sitemap.xml или sitemap index.
    Возвращает список URL (для обычного sitemap) или список URL дочерних sitemap (для индекса).
    """
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError:
        raise ValueError("Некорректный XML")

    # Пространства имён (обычно используются)
    ns = {'sitemap': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

    # Проверяем, есть ли дочерние элементы <sitemap> (признак индекса)
    sitemap_tags = root.findall('sitemap:sitemap', ns)
    if sitemap_tags:
        # Это sitemap index – собираем ссылки на вложенные sitemap
        urls = []
        for s in sitemap_tags:
            loc = s.find('sitemap:loc', ns)
            if loc is not None and loc.text:
                urls.append(loc.text)
        return urls, True  # (список, is_index=True)
    else:
        # Обычный sitemap – собираем URL из <url>/<loc>
        url_tags = root.findall('sitemap:url', ns)
        urls = []
        for u in url_tags:
            loc = u.find('sitemap:loc', ns)
            if loc is not None and loc.text:
                urls.append(loc.text)
        return urls, False


def get_all_urls_from_sitemap(start_url):
    """Рекурсивно обходит sitemap index и возвращает все URL страниц"""
    all_urls = []
    to_visit = [start_url]
    visited = set()

    while to_visit:
        current = to_visit.pop()
        if current in visited:
            continue
        visited.add(current)

        try:
            content = fetch_sitemap(current)
            urls, is_index = parse_sitemap(content, current)
            if is_index:
                # Добавляем дочерние sitemap в очередь
                to_visit.extend(urls)
            else:
                # Это конечные URL страниц
                all_urls.extend(urls)
        except Exception as e:
            print(f"⚠️ Ошибка при загрузке/парсинге {current}: {e}")

    return all_urls


def get_access_token(key_file):
    """Получает токен доступа из файла сервисного аккаунта"""
    SCOPES = ["https://www.googleapis.com/auth/indexing"]
    credentials = service_account.Credentials.from_service_account_file(
        key_file, scopes=SCOPES
    )
    credentials.refresh(Request())
    token = credentials.token
    if not token:
        raise RuntimeError("Не удалось получить токен")
    return token


def send_to_indexing_api(urls, access_token, max_urls=200):
    """Отправляет URL в Google Indexing API"""
    endpoint = "https://indexing.googleapis.com/v3/urlNotifications:publish"
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json',
    }
    success_count = 0

    for url in urls[:max_urls]:
        body = json.dumps({"url": url, "type": "URL_UPDATED"})
        try:
            resp = requests.post(endpoint, headers=headers, data=body, timeout=10)
            if resp.status_code == 200:
                print(f"✅ {url}")
                success_count += 1
            elif resp.status_code == 429:
                print(f"⚠️ Квота исчерпана (429), остановка на {url}")
                break
            else:
                print(f"❌ Ошибка {resp.status_code} для {url}: {resp.text[:150]}")
        except requests.RequestException as e:
            print(f"❌ Сбой сети на {url}: {e}")

    return success_count


def main():
    # 1. Проверяем наличие файла с ключами
    if not os.path.exists(CREDENTIALS_FILE):
        print("❌ Файл credentials.json не найден!")
        return

    # 2. Получаем токен
    try:
        token = get_access_token(CREDENTIALS_FILE)
        print("✅ Access Token получен")
    except Exception as e:
        print(f"❌ Ошибка авторизации: {e}")
        return

    # 3. Загружаем и парсим sitemap
    print(f"📥 Загружаем sitemap: {SITEMAP_URL}")
    try:
        all_urls = get_all_urls_from_sitemap(SITEMAP_URL)
    except Exception as e:
        print(f"❌ Ошибка при разборе sitemap: {e}")
        return

    if not all_urls:
        print("❌ В sitemap не найдено ни одного URL")
        return

    print(f"📋 Найдено URL: {len(all_urls)}")
    print(f"🚀 Отправляем первые {min(MAX_URLS, len(all_urls))} URL в Indexing API...")

    # 4. Отправляем
    sent = send_to_indexing_api(all_urls, token, MAX_URLS)
    print(f"\n🎉 Готово! Отправлено: {sent} из {len(all_urls)}")


if __name__ == "__main__":
    main()
