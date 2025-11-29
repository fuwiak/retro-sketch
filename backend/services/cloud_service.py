"""
Cloud folder service for Mail.ru Cloud
"""
import requests
from bs4 import BeautifulSoup
import re
from typing import List, Dict
from services.logger import api_logger

class CloudService:
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        })
    
    def parse_mailru_folder_structure(self, url: str) -> Dict:
        """
        Parse Mail.ru Cloud public folder URL - LAZY: only get structure (folders and file names)
        Returns list of folders and file names WITHOUT fetching files from subfolders
        This is fast and doesn't cause timeouts
        """
        try:
            api_logger.info(f"Fetching Mail.ru Cloud folder structure: {url}")
            
            # Extract folder hash from URL
            # Format: https://cloud.mail.ru/public/ZVeV/Mq5HoaFGX
            match = re.search(r'/public/([^/]+)/([^/]+)', url)
            if not match:
                raise ValueError("Invalid Mail.ru Cloud URL format")
            
            folder_hash = match.group(2)
            
            # Try to fetch folder page with longer timeout for large folders
            response = self.session.get(url, timeout=30)
            response.raise_for_status()
            
            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            files = []
            
            # Try to find file listings in the page
            # Mail.ru Cloud structure may vary, so we try multiple approaches
            
            # Approach 1: Look for script tags with JSON data (window.__INITIAL_STATE__ or list array)
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string:
                    script_content = script.string
                    
                    # Try to find window.__INITIAL_STATE__ or similar
                    json_patterns = [
                        r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\});',
                        r'window\.__DATA__\s*=\s*(\{.*?\});',
                        r'var\s+__INITIAL_STATE__\s*=\s*(\{.*?\});',
                    ]
                    
                    for pattern in json_patterns:
                        match = re.search(pattern, script_content, re.DOTALL)
                        if match:
                            try:
                                import json
                                data = json.loads(match.group(1))
                                # Look for files in nested structure
                                if 'files' in data:
                                    files.extend(self._parse_json_files(data['files'], url))
                                elif 'body' in data and 'files' in data['body']:
                                    files.extend(self._parse_json_files(data['body']['files'], url))
                            except:
                                pass
                    
                    # Look for "list" array with folder/file structure
                    if 'weblink' in script_content.lower() and 'list' in script_content.lower():
                        # Find the list array
                        list_match = re.search(r'"list"\s*:\s*(\[.*?\])', script_content, re.DOTALL)
                        if list_match:
                            try:
                                import json
                                # Try to extract full array
                                start_idx = script_content.find('"list"')
                                if start_idx != -1:
                                    array_start = script_content.find('[', start_idx)
                                    if array_start != -1:
                                        # Find matching closing bracket
                                        bracket_count = 1
                                        array_end = array_start + 1
                                        for i in range(array_start + 1, len(script_content)):
                                            if script_content[i] == '[':
                                                bracket_count += 1
                                            elif script_content[i] == ']':
                                                bracket_count -= 1
                                                if bracket_count == 0:
                                                    array_end = i + 1
                                                    break
                                        
                                        array_str = script_content[array_start:array_end]
                                        list_data = json.loads(array_str)
                                        
                                        # Parse items from list - SIMPLE: just get all files
                                        for item in list_data:
                                            if isinstance(item, dict):
                                                item_type = item.get('type') or item.get('kind', '')
                                                item_name = item.get('name', '')
                                                item_weblink = item.get('weblink', '')
                                                
                                                # Build URL
                                                if item_weblink:
                                                    item_url = f"https://cloud.mail.ru/public/{item_weblink}"
                                                else:
                                                    item_url = f"{url}/{item_name}"
                                                
                                                # LAZY: If it's a folder, just store it as folder (don't fetch files yet)
                                                if item_type == 'folder':
                                                    files.append({
                                                        'name': item_name,
                                                        'type': 'folder',
                                                        'path': '',
                                                        'url': item_url,
                                                        'download_url': item_url  # Folder URL for fetching files later
                                                    })
                                                # If it's a file, add it
                                                elif item_type == 'file' or (item_type != 'folder' and item_name):
                                                    # Для файлов используем API endpoint для прямого скачивания
                                                    if item_weblink:
                                                        # Используем API endpoint с weblink для прямого скачивания
                                                        download_url = f"https://cloud.mail.ru/api/v2/file/download?weblink={item_weblink}"
                                                    else:
                                                        # Fallback на публичную ссылку
                                                        download_url = item_url
                                                    files.append({
                                                        'name': item_name,
                                                        'type': 'file',
                                                        'path': '',
                                                        'url': download_url,
                                                        'download_url': download_url,
                                                        'weblink': item_weblink  # Сохраняем weblink для использования
                                                    })
                            except Exception as e:
                                api_logger.debug(f"Error parsing list array: {str(e)}")
                                pass
            
            # Approach 2: Parse HTML - look for file items in Mail.ru Cloud structure
            # SKIP HTML parsing to avoid finding promotional/advertisement files
            # Mail.ru Cloud HTML often contains promotional PDFs that are not part of the folder
            # We should only use JSON structure parsing (Approach 1) or API (Approach 3)
            if not files:
                api_logger.warning("HTML parsing skipped to avoid promotional files. Using only JSON/API methods.")
                # We skip HTML parsing entirely to avoid false positives from promotional content
                pass
            
            # Approach 3: Try Mail.ru Cloud API with proper structure
            if not files:
                # Mail.ru Cloud API endpoints
                api_endpoints = [
                    f"https://cloud.mail.ru/api/v2/folder?weblink={folder_hash}",
                    f"https://cloud.mail.ru/api/v2/folder?home={folder_hash}",
                    f"https://cloud.mail.ru/api/v2/folder?token={folder_hash}",
                ]
                
                for api_url in api_endpoints:
                    try:
                        api_response = self.session.get(api_url, timeout=20)
                        if api_response.status_code == 200:
                            data = api_response.json()
                            # Try different response structures
                            if 'body' in data:
                                if 'list' in data['body']:
                                    files.extend(self._parse_api_files(data['body']['list'], url))
                                elif 'items' in data['body']:
                                    files.extend(self._parse_api_files(data['body']['items'], url))
                            elif 'list' in data:
                                files.extend(self._parse_api_files(data['list'], url))
                            elif 'items' in data:
                                files.extend(self._parse_api_files(data['items'], url))
                            
                            if files:
                                break
                    except Exception as e:
                        api_logger.debug(f"API endpoint {api_url} failed: {str(e)}")
                        continue
            
            api_logger.info(f"Found {len(files)} items in folder structure (folders + files)")
            return {'items': files, 'folder_url': url}
            
        except Exception as e:
            api_logger.error(f"Error parsing Mail.ru Cloud folder: {str(e)}")
            raise
    
    def _parse_json_files(self, file_list: List, base_url: str) -> List[Dict]:
        """Parse files from JSON structure"""
        files = []
        for item in file_list:
            if isinstance(item, dict):
                name = item.get('name', '')
                path = item.get('path', '')
                item_type = item.get('type') or item.get('kind', 'file')  # Get type from JSON or default to 'file'
                # Construct download URL
                download_url = item.get('download_url') or item.get('url') or f"{base_url}/{name}"
                files.append({
                    'name': name,
                    'type': 'folder' if item_type == 'folder' else 'file',
                    'path': path,
                    'url': download_url,
                    'download_url': download_url
                })
        return files
    
    def _parse_api_files(self, file_list: List, base_url: str) -> List[Dict]:
        """Parse files from API response"""
        files = []
        for item in file_list:
            if isinstance(item, dict):
                name = item.get('name', '')
                path = item.get('path', '')
                item_type = item.get('type') or item.get('kind', 'file')  # Get type from API or default to 'file'
                # Mail.ru Cloud API may return different structures
                download_url = item.get('weblink') or item.get('download_url') or item.get('url')
                if download_url and not download_url.startswith('http'):
                    download_url = f"https://cloud.mail.ru{download_url}"
                elif not download_url:
                    download_url = f"{base_url}/{name}"
                files.append({
                    'name': name,
                    'type': 'folder' if item_type == 'folder' else 'file',
                    'path': path,
                    'url': download_url,
                    'download_url': download_url
                })
        return files
    
    def fetch_folder_files(self, folder_url: str, folder_name: str = "") -> List[Dict]:
        """
        Fetch files from a subfolder - LAZY: called on demand
        Returns list of files (and subfolders) from this folder only
        """
        items = []
        try:
            api_logger.info(f"Fetching files from folder: {folder_url}")
            response = self.session.get(folder_url, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            scripts = soup.find_all('script')
            
            for script in scripts:
                if script.string and 'list' in script.string.lower():
                    script_content = script.string
                    start_idx = script_content.find('"list"')
                    if start_idx != -1:
                        array_start = script_content.find('[', start_idx)
                        if array_start != -1:
                            bracket_count = 1
                            array_end = array_start + 1
                            for i in range(array_start + 1, len(script_content)):
                                if script_content[i] == '[':
                                    bracket_count += 1
                                elif script_content[i] == ']':
                                    bracket_count -= 1
                                    if bracket_count == 0:
                                        array_end = i + 1
                                        break
                            
                            try:
                                import json
                                array_str = script_content[array_start:array_end]
                                list_data = json.loads(array_str)
                                
                                for item in list_data:
                                    if isinstance(item, dict):
                                        item_type = item.get('type') or item.get('kind', '')
                                        item_name = item.get('name', '')
                                        item_weblink = item.get('weblink', '')
                                        
                                        # Build URL
                                        if item_weblink:
                                            item_url = f"https://cloud.mail.ru/public/{item_weblink}"
                                        else:
                                            item_url = f"{folder_url}/{item_name}"
                                        
                                        # Add folder or file
                                        if item_type == 'folder':
                                            items.append({
                                                'name': item_name,
                                                'type': 'folder',
                                                'path': folder_name,
                                                'url': item_url,
                                                'download_url': item_url
                                            })
                                        elif item_type == 'file' or (item_type != 'folder' and item_name):
                                            # Для файлов используем weblink для прямого скачивания
                                            # Mail.ru Cloud использует формат: https://cloud.mail.ru/api/v2/file/download?weblink={weblink}
                                            if item_weblink:
                                                # Используем weblink напрямую - это хеш файла для API
                                                download_url = f"https://cloud.mail.ru/api/v2/file/download?weblink={item_weblink}"
                                            else:
                                                # Fallback на публичную ссылку
                                                download_url = item_url
                                            
                                            items.append({
                                                'name': item_name,
                                                'type': 'file',
                                                'path': folder_name,
                                                'url': download_url,
                                                'download_url': download_url,
                                                'weblink': item_weblink  # Сохраняем weblink для использования
                                            })
                                break
                            except Exception as e:
                                api_logger.debug(f"Error parsing folder JSON: {str(e)}")
                                pass
        except Exception as e:
            api_logger.error(f"Error fetching folder files: {str(e)}")
            raise
        
        api_logger.info(f"Found {len(items)} items in folder {folder_name or folder_url}")
        return items
    
    def download_file(self, url: str, expected_filename: str = None) -> bytes:
        """Download file from URL"""
        try:
            api_logger.info(f"Downloading file: {url}")
            
            # Если URL уже является публичным URL, пробуем его напрямую сначала
            # Публичные URL часто работают лучше для файлов с кириллицей
            if '/public/' in url and '/api/v2/file/download' not in url:
                api_logger.info("URL is a public URL, trying direct download first")
                try:
                    direct_response = self.session.get(url, timeout=30, stream=True, allow_redirects=True, headers={
                        'Referer': 'https://cloud.mail.ru/',
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                    })
                    if direct_response.status_code == 200:
                        direct_content = direct_response.content
                        direct_content_type = direct_response.headers.get('Content-Type', '').lower()
                        # Проверяем, что это файл, а не HTML
                        if len(direct_content) > 1000:
                            first_bytes_direct = direct_content[:4]
                            if not (first_bytes_direct[0:2] == b'<!' or b'<html' in direct_content[:100].lower()) and 'text/html' not in direct_content_type:
                                api_logger.info("Successfully downloaded via direct public URL")
                                return direct_content
                except Exception as e:
                    api_logger.debug(f"Direct public URL download failed: {str(e)}")
            
            # Если URL уже является API endpoint, пробуем его, но если 403 или HTML - fallback на публичный URL
            if '/api/v2/file/download' in url:
                api_logger.info("URL is already an API endpoint, trying it directly")
                # Добавляем дополнительные заголовки для Mail.ru Cloud API
                headers = {
                    'Referer': 'https://cloud.mail.ru/',
                    'Origin': 'https://cloud.mail.ru'
                }
                response = self.session.get(url, timeout=30, stream=True, allow_redirects=True, headers=headers)
                
                # Проверяем статус перед raise_for_status() чтобы обработать 403
                if response.status_code == 403:
                    api_logger.warning(f"API endpoint returned 403 Forbidden, trying public URL fallback")
                    # Извлекаем weblink и пробуем публичный URL
                    import re
                    weblink_match = re.search(r'weblink=([^&]+)', url)
                    if weblink_match:
                        weblink = weblink_match.group(1)
                        # Декодируем weblink (может содержать URL-encoded символы)
                        from urllib.parse import unquote
                        try:
                            weblink_decoded = unquote(weblink)
                            api_logger.info(f"Decoded weblink: {weblink_decoded}")
                        except:
                            weblink_decoded = weblink
                        
                        # Пробуем публичный URL с декодированным weblink
                        public_url = f"https://cloud.mail.ru/public/{weblink_decoded}"
                        api_logger.info(f"Trying public URL fallback: {public_url}")
                        # Продолжим обработку как обычный URL ниже - выходим из блока API endpoint
                        url = public_url
                    else:
                        # Если не удалось извлечь weblink, пробуем обработать как обычный URL
                        api_logger.warning(f"Could not extract weblink from API URL, continuing with original URL")
                elif response.status_code == 200:
                    # Успешный ответ от API endpoint
                    content = response.content
                    
                    # Проверяем, что это файл, а не HTML
                    if len(content) > 4:
                        first_bytes = content[:4]
                        if not (first_bytes[0:2] == b'<!' or b'<html' in content[:100].lower()):
                            api_logger.info("Successfully downloaded via API endpoint")
                            return content
                        else:
                            api_logger.warning("API endpoint returned HTML instead of file")
                            # HTML вместо файла - пробуем fallback
                            import re
                            weblink_match = re.search(r'weblink=([^&]+)', url)
                            if weblink_match:
                                weblink = weblink_match.group(1)
                                from urllib.parse import unquote
                                try:
                                    weblink_decoded = unquote(weblink)
                                except:
                                    weblink_decoded = weblink
                                public_url = f"https://cloud.mail.ru/public/{weblink_decoded}"
                                api_logger.info(f"Trying public URL fallback: {public_url}")
                                url = public_url
                else:
                    # Другая ошибка - пробуем fallback
                    api_logger.warning(f"API endpoint returned status {response.status_code}, trying public URL fallback")
                    import re
                    weblink_match = re.search(r'weblink=([^&]+)', url)
                    if weblink_match:
                        weblink = weblink_match.group(1)
                        from urllib.parse import unquote
                        try:
                            weblink_decoded = unquote(weblink)
                        except:
                            weblink_decoded = weblink
                        public_url = f"https://cloud.mail.ru/public/{weblink_decoded}"
                        api_logger.info(f"Trying public URL fallback: {public_url}")
                        url = public_url
            
            # Обычная загрузка через URL
            response = self.session.get(url, timeout=30, stream=True, allow_redirects=True)
            response.raise_for_status()
            
            # Check if response is actually a file or HTML error page
            content_type = response.headers.get('Content-Type', '').lower()
            content = response.content
            
            # Check first bytes to detect HTML
            if len(content) > 4:
                first_bytes = content[:4]
                is_html = first_bytes[0:2] == b'<!' or first_bytes[0:2] == b'<h' or b'<html' in content[:100].lower()
                
                if is_html or 'text/html' in content_type:
                    api_logger.warning(f"Received HTML instead of file. Content-Type: {content_type}, First bytes: {first_bytes}")
                    api_logger.warning(f"HTML preview: {content[:500].decode('utf-8', errors='ignore')}")
                    
                    # Если это публичный URL, попробуем разные варианты для прямого доступа к файлу
                    if '/public/' in url:
                        api_logger.info(f"Public URL returned HTML, trying different URL encodings and direct file access")
                        
                        from urllib.parse import quote, unquote, urlparse, parse_qs
                        import urllib.parse
                        
                        # Извлекаем путь после /public/
                        match = re.search(r'/public/(.+)$', url)
                        if match:
                            weblink_path = match.group(1)
                            api_logger.info(f"Extracted weblink path: {weblink_path}")
                            
                            # Попробуем разные варианты URL-кодирования
                            variants = []
                            
                            # Вариант 1: Оригинальный путь (уже декодированный)
                            variants.append((weblink_path, "original"))
                            
                            # Вариант 2: URL-кодированный путь (кодируем только специальные символы, не '/')
                            variants.append((quote(weblink_path, safe='/'), "quote safe /"))
                            
                            # Вариант 3: Полностью URL-кодированный
                            variants.append((quote(weblink_path, safe=''), "fully quoted"))
                            
                            # Вариант 4: Двойное кодирование
                            variants.append((quote(quote(weblink_path, safe='/'), safe='/'), "double quoted"))
                            
                            # Вариант 5: Если есть expected_filename, попробуем заменить имя файла
                            if expected_filename:
                                # Разделяем путь на части
                                parts = weblink_path.split('/')
                                if len(parts) > 0:
                                    # Заменяем последнюю часть (имя файла) на expected_filename
                                    parts[-1] = expected_filename
                                    new_path = '/'.join(parts)
                                    variants.append((new_path, "with expected filename"))
                                    variants.append((quote(new_path, safe='/'), "with expected filename quoted"))
                            
                            # Пробуем каждый вариант
                            for variant_path, variant_name in variants:
                                # Пробуем прямую ссылку на публичный файл
                                public_url_variant = f"https://cloud.mail.ru/public/{variant_path}"
                                api_logger.info(f"Trying public URL variant ({variant_name}): {public_url_variant[:150]}")
                                
                                try:
                                    variant_response = self.session.get(public_url_variant, timeout=30, stream=True, allow_redirects=True, headers={
                                        'Referer': 'https://cloud.mail.ru/',
                                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
                                    })
                                    
                                    if variant_response.status_code == 200:
                                        variant_content = variant_response.content
                                        variant_content_type = variant_response.headers.get('Content-Type', '').lower()
                                        
                                        # Проверяем, что это файл, а не HTML
                                        if len(variant_content) > 1000:
                                            first_bytes_variant = variant_content[:4]
                                            if not (first_bytes_variant[0:2] == b'<!' or b'<html' in variant_content[:100].lower()) and 'text/html' not in variant_content_type:
                                                api_logger.info(f"Successfully downloaded using public URL variant ({variant_name})")
                                                return variant_content
                                        elif variant_response.status_code == 404:
                                            api_logger.debug(f"Variant {variant_name} returned 404")
                                except Exception as e:
                                    api_logger.debug(f"Variant {variant_name} failed: {str(e)}")
                                    continue
                    
                    # Try to extract direct download link from HTML
                    soup = BeautifulSoup(content, 'html.parser')
                    
                    # Look for download links or redirects
                    download_links = []
                    
                    # Try to find direct download links
                    # Filter out promotional/advertisement links
                    promotional_keywords = ['акция', 'литрес', 'mail space', 'promo', 'реклама', 'advertisement']
                    for link in soup.find_all('a', href=True):
                        href = link.get('href', '')
                        # Skip promotional links
                        if any(keyword in href.lower() for keyword in promotional_keywords):
                            continue
                        if href and ('download' in href.lower() or href.endswith('.pdf') or href.endswith('.png') or href.endswith('.jpg')):
                            if href.startswith('http'):
                                download_links.append(href)
                            elif href.startswith('/'):
                                # Make absolute URL
                                from urllib.parse import urljoin
                                download_links.append(urljoin(url, href))
                    
                    # Try to find meta refresh or redirect
                    meta_refresh = soup.find('meta', attrs={'http-equiv': re.compile('refresh', re.I)})
                    if meta_refresh and meta_refresh.get('content'):
                        redirect_url = re.search(r'url=(.+)', meta_refresh.get('content', ''), re.I)
                        if redirect_url:
                            download_links.insert(0, redirect_url.group(1))
                    
                    # Try to find script tags with download URLs
                    # Filter out promotional/advertisement URLs immediately
                    promotional_domains = [
                        'promoimages.hb.ru-msk.vkcloud-storage.ru',
                        'vkcloud-storage.ru',
                        'imgs2.imgsmail.ru',
                        'imgsmail.ru',
                        'promo',
                        'advertising',
                        'реклама',
                        'nestle'
                    ]
                    promotional_keywords = [
                        'акция',
                        'литрес',
                        'mail space',
                        'promo',
                        'реклама',
                        'advertisement',
                        'action_mailspace',
                        'pet-34',
                        'static/cloud',
                        'desktop'
                    ]
                    
                    for script in soup.find_all('script'):
                        if script.string:
                            # Look for URLs in script - расширенный поиск для Mail.ru Cloud
                            # Ищем любые URL с расширениями файлов, но фильтруем рекламу
                            urls = re.findall(r'https?://[^\s"\'<>\)]+\.(?:pdf|png|jpg|jpeg|jpe)', script.string, re.I)
                            # Filter out promotional URLs
                            for url in urls:
                                url_lower = url.lower()
                                # Skip promotional domains
                                if any(domain in url_lower for domain in promotional_domains):
                                    api_logger.debug(f"Filtered promotional URL (domain): {url[:80]}")
                                    continue
                                # Skip promotional keywords
                                if any(keyword in url_lower for keyword in promotional_keywords):
                                    api_logger.debug(f"Filtered promotional URL (keyword): {url[:80]}")
                                    continue
                                download_links.append(url)
                            
                            # Также ищем ссылки на API Mail.ru Cloud (these are more reliable)
                            api_urls = re.findall(r'https?://cloud\.mail\.ru/api/v\d+/file/download[^\s"\'<>\)]+', script.string, re.I)
                            # API URLs get priority
                            for api_url in api_urls:
                                if api_url not in download_links:
                                    download_links.insert(0, api_url)  # Priority to API links
                            
                            # Ищем ссылки с параметрами weblink (only API-related)
                            weblink_urls = re.findall(r'https?://cloud\.mail\.ru/api/[^\s"\'<>\)]+', script.string, re.I)
                            for weblink_url in weblink_urls:
                                if weblink_url not in download_links:
                                    download_links.insert(0, weblink_url)  # Priority to API links
                    
                    # Try alternative: use /download endpoint for files in subfolders
                    if '/public/' in url:
                        # Extract path after /public/ to get weblink
                        # Format: https://cloud.mail.ru/public/2RNv/faZLz1cLQ/0002/filename.pdf
                        # weblink = 2RNv/faZLz1cLQ/0002/filename.pdf
                        match = re.search(r'/public/(.+)$', url)
                        if match:
                            weblink_path = match.group(1)
                            
                            # Try direct download through API with full weblink path (includes subfolders)
                            # URL encode the weblink path for proper handling
                            from urllib.parse import quote, unquote
                            
                            # Try with encoded weblink
                            encoded_weblink = quote(weblink_path, safe='/')
                            download_url = f"https://cloud.mail.ru/api/v2/file/download?weblink={encoded_weblink}"
                            api_logger.info(f"Trying API download URL with full weblink path: {download_url[:150]}")
                            try:
                                headers = {
                                    'Referer': 'https://cloud.mail.ru/',
                                    'Origin': 'https://cloud.mail.ru'
                                }
                                alt_response = self.session.get(download_url, timeout=30, stream=True, allow_redirects=True, headers=headers)
                            if alt_response.status_code == 200:
                                alt_content = alt_response.content
                                # Check if it's actually a file
                                    if len(alt_content) > 1000 and not (alt_content[:2] == b'<!' or b'<html' in alt_content[:100].lower()):
                                        api_logger.info(f"Successfully downloaded using API URL with full weblink path")
                                    return alt_content
                                elif alt_response.status_code == 403:
                                    api_logger.warning(f"API returned 403 for weblink, will try direct public URL")
                            except Exception as e:
                                api_logger.warning(f"API download URL failed: {str(e)}")
                            
                            # Try direct public URL download (for files that are publicly accessible)
                            api_logger.info(f"Trying direct public URL download: {url}")
                            try:
                                direct_response = self.session.get(url, timeout=30, stream=True, allow_redirects=True)
                                if direct_response.status_code == 200:
                                    direct_content = direct_response.content
                                    # Check if it's actually a file
                                    if len(direct_content) > 1000 and not (direct_content[:2] == b'<!' or b'<html' in direct_content[:100].lower()):
                                        api_logger.info(f"Successfully downloaded using direct public URL")
                                        return direct_content
                            except Exception as e:
                                api_logger.warning(f"Direct public URL download failed: {str(e)}")
                            
                            # Try with original weblink (unencoded, but properly formatted)
                            download_url2 = f"https://cloud.mail.ru/api/v2/file/download?weblink={weblink_path}"
                            api_logger.info(f"Trying API download URL with original weblink: {download_url2[:150]}")
                            try:
                                headers = {
                                    'Referer': 'https://cloud.mail.ru/',
                                    'Origin': 'https://cloud.mail.ru'
                                }
                                alt_response2 = self.session.get(download_url2, timeout=30, stream=True, allow_redirects=True, headers=headers)
                                if alt_response2.status_code == 200:
                                    alt_content2 = alt_response2.content
                                    if len(alt_content2) > 1000 and not (alt_content2[:2] == b'<!' or b'<html' in alt_content2[:100].lower()):
                                        api_logger.info(f"Successfully downloaded using API URL with original weblink")
                                        return alt_content2
                            except Exception as e:
                                api_logger.warning(f"API download URL (original weblink) failed: {str(e)}")
                    
                    # If we found download links, filter out promotional ones and try them
                    promotional_domains = [
                        'promoimages.hb.ru-msk.vkcloud-storage.ru',
                        'vkcloud-storage.ru',
                        'imgs2.imgsmail.ru',
                        'imgsmail.ru',
                        'r.mradx.net',
                        'mradx.net',
                        'nestle',
                        'promo',
                        'advertising',
                        'реклама'
                    ]
                    promotional_keywords = [
                        'акция',
                        'литрес',
                        'mail space',
                        'promo',
                        'реклама',
                        'advertisement',
                        'action_mailspace',
                        'pet-34',
                        'desktop',
                        'static/cloud',
                        '/img/',
                        '9aac10'
                    ]
                    
                    # Filter out promotional links - STRICT: only Mail.ru Cloud links
                    filtered_links = []
                    for link in download_links:
                        link_lower = link.lower()
                        
                        # STRICT: Only allow Mail.ru Cloud links
                        if 'cloud.mail.ru' not in link_lower:
                            api_logger.debug(f"Filtered out external link (not Mail.ru Cloud): {link[:100]}")
                            continue
                        
                        # Skip if contains promotional domain
                        if any(domain in link_lower for domain in promotional_domains):
                            api_logger.debug(f"Filtered out promotional link (domain): {link[:100]}")
                            continue
                        # Skip if contains promotional keywords
                        if any(keyword in link_lower for keyword in promotional_keywords):
                            api_logger.debug(f"Filtered out promotional link (keyword): {link[:100]}")
                            continue
                        # Prefer Mail.ru Cloud API links over public links
                        if 'cloud.mail.ru/api' in link_lower:
                            filtered_links.insert(0, link)  # Priority to API links
                        elif 'cloud.mail.ru/public' in link_lower:
                            filtered_links.append(link)  # Public links as fallback
                    
                    if filtered_links:
                        api_logger.info(f"Found {len(filtered_links)} filtered download links (from {len(download_links)} total), trying them...")
                        for i, download_link in enumerate(filtered_links[:5]):  # Пробуем первые 5 отфильтрованных ссылок
                            try:
                                api_logger.info(f"Trying download link {i+1}/{min(len(filtered_links), 5)}: {download_link[:100]}...")
                                alt_response = self.session.get(download_link, timeout=30, stream=True, allow_redirects=True)
                                if alt_response.status_code == 200:
                        alt_content = alt_response.content
                                    # Additional check: verify file size is reasonable (not a tiny HTML page)
                                    if len(alt_content) > 1000 and not (alt_content[:2] == b'<!' or b'<html' in alt_content[:100].lower()):
                                        # Check link type - prefer Mail.ru Cloud, but allow external if matches filename
                                        download_link_lower = download_link.lower()
                                        
                                        # Filter external links more carefully
                                        if 'cloud.mail.ru' not in download_link_lower:
                                            # External link - check if it's promotional or matches expected filename
                                            if any(domain in download_link_lower for domain in ['r.mradx.net', 'imgs2.imgsmail.ru', 'promoimages']):
                                                api_logger.warning(f"Skipping promotional external link: {download_link[:100]}")
                                                continue
                                            # If expected filename provided, check if it matches
                                            if expected_filename:
                                                expected_name_base = expected_filename.lower().split('.')[0][:10]
                                                if expected_name_base not in download_link_lower.replace('%', '').replace('-', '_'):
                                                    api_logger.warning(f"Skipping external link (filename mismatch): {download_link[:100]}")
                                                    continue
                                            else:
                                                # No expected filename - skip external links
                                                api_logger.warning(f"Skipping external link (no filename check): {download_link[:100]}")
                                                continue
                                        
                                        # Skip promotional files by checking URL again
                                        promotional_check_domains = [
                                            'promoimages.hb.ru-msk.vkcloud-storage.ru',
                                            'vkcloud-storage.ru',
                                            'imgs2.imgsmail.ru',
                                            'imgsmail.ru',
                                            'r.mradx.net',
                                            'mradx.net',
                                            'nestle'
                                        ]
                                        promotional_check_keywords = [
                                            'action_mailspace',
                                            'pet-34',
                                            'static/cloud',
                                            'desktop',
                                            '/img/',
                                            '9aac10'
                                        ]
                                        
                                        if any(domain in download_link_lower for domain in promotional_check_domains):
                                            api_logger.warning(f"Skipping promotional file (domain): {download_link[:100]}")
                                            continue
                                        
                                        if any(keyword in download_link_lower for keyword in promotional_check_keywords):
                                            api_logger.warning(f"Skipping promotional file (keyword): {download_link[:100]}")
                                            continue
                                        
                                        # CRITICAL: Check file extension matches expected filename
                                        if expected_filename:
                                            # Get expected file extension
                                            expected_ext = expected_filename.lower().split('.')[-1] if '.' in expected_filename else ''
                                            # Get URL file extension
                                            url_ext = download_link_lower.split('.')[-1].split('?')[0].split('/')[-1] if '.' in download_link_lower else ''
                                            
                                            # If expected is PDF, but URL is PNG/JPG - skip (likely advertisement)
                                            if expected_ext == 'pdf' and url_ext in ['png', 'jpg', 'jpeg', 'gif']:
                                                api_logger.warning(f"Extension mismatch: expected PDF, but URL is {url_ext.upper()}: {download_link[:100]}")
                                                continue
                                            
                                            # If expected is PNG/JPG, but URL is PDF - might be OK, but log
                                            if expected_ext in ['png', 'jpg', 'jpeg'] and url_ext == 'pdf':
                                                api_logger.warning(f"Extension mismatch: expected {expected_ext.upper()}, but URL is PDF: {download_link[:100]}")
                                                # Continue anyway - might be valid conversion
                                            
                                            # Extract base name from expected filename
                                            expected_name_base = expected_filename.lower().split('.')[0].replace(' ', '').replace('-', '').replace('_', '').replace('/', '')
                                            # Decode URL to check filename
                                            from urllib.parse import unquote
                                            decoded_url = unquote(download_link_lower)
                                            # Check if expected filename is in the URL
                                            if expected_name_base and len(expected_name_base) > 3:
                                                if expected_name_base[:5] not in decoded_url.replace('%', '').replace('-', '').replace('_', '').replace(' ', ''):
                                                    api_logger.warning(f"Filename mismatch: expected '{expected_name_base[:10]}', URL: {download_link[:100]}")
                                                    continue  # Skip if filename doesn't match
                                        
                                        api_logger.info(f"Successfully downloaded using extracted link {i+1} (size: {len(alt_content)} bytes)")
                            return alt_content
                                    else:
                                        api_logger.warning(f"Download link {i+1} returned invalid content (too small or HTML)")
                            except Exception as e:
                                api_logger.warning(f"Download link {i+1} failed: {str(e)}")
                                continue
                    
                    # Если ничего не сработало, пробуем извлечь информацию из HTML для создания прямого URL
                    api_logger.error(f"Failed to extract download link from HTML. Content-Type: {content_type}")
                    api_logger.error(f"HTML content preview: {content[:1000].decode('utf-8', errors='ignore')[:500]}")
                    raise ValueError(f"Mail.ru Cloud вернул HTML вместо файла. Файл может быть не публичным или URL неверный. Content-Type: {content_type}")
            
            # Validate it's actually a file (not HTML)
            if len(content) > 4:
                first_bytes = content[:4]
                if first_bytes[0:2] == b'<!' or b'<html' in content[:100].lower():
                    raise ValueError(f"Server returned HTML instead of file. First bytes: {first_bytes.hex()}")
            
            return content
        except Exception as e:
            api_logger.error(f"Error downloading file: {str(e)}")
            raise

