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
    
    def parse_mailru_folder(self, url: str, max_files: int = None) -> Dict:
        """
        Parse Mail.ru Cloud public folder URL
        Returns list of files with their download URLs
        max_files: Stop parsing after finding this many files (for pagination)
        """
        try:
            api_logger.info(f"Fetching Mail.ru Cloud folder: {url} (max_files={max_files})")
            
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
                                remaining_limit = max_files - len(files) if max_files else None
                                if 'files' in data:
                                    parsed = self._parse_json_files(data['files'], url, remaining_limit)
                                    files.extend(parsed)
                                    if max_files and len(files) >= max_files:
                                        break
                                elif 'body' in data and 'files' in data['body']:
                                    parsed = self._parse_json_files(data['body']['files'], url, remaining_limit)
                                    files.extend(parsed)
                                    if max_files and len(files) >= max_files:
                                        break
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
                                        
                                        # Parse items from list
                                        for item in list_data:
                                            if isinstance(item, dict):
                                                item_type = item.get('type') or item.get('kind', '')
                                                item_name = item.get('name', '')
                                                item_weblink = item.get('weblink', '')
                                                item_count = item.get('count', {})
                                                
                                                # Build URL
                                                if item_weblink:
                                                    item_url = f"https://cloud.mail.ru/public/{item_weblink}"
                                                else:
                                                    item_url = f"{url}/{item_name}"
                                                
                                                # If it's a folder, we can list it and potentially recurse later
                                                if item_type == 'folder':
                                                    # Store folder info - frontend can use this to navigate
                                                    # For now, we'll try to fetch files from this folder
                                                    # Limit recursion to avoid too many requests
                                                    # Stop if we've reached max_files limit
                                                    if max_files and len(files) >= max_files:
                                                        api_logger.debug(f"Reached max_files limit ({max_files}), stopping folder parsing")
                                                        break
                                                    try:
                                                        folder_files = self._fetch_folder_files(item_url, item_name, max_files - len(files) if max_files else None)
                                                        files.extend(folder_files)
                                                        api_logger.debug(f"Fetched {len(folder_files)} files from folder {item_name}")
                                                        # Stop if we've reached max_files limit
                                                        if max_files and len(files) >= max_files:
                                                            api_logger.debug(f"Reached max_files limit ({max_files}), stopping after folder {item_name}")
                                                            break
                                                    except Exception as e:
                                                        api_logger.warning(f"Error fetching folder {item_name}: {str(e)}")
                                                        # Continue with other folders even if one fails
                                                # If it's a file
                                                elif item_type == 'file' or (item_type != 'folder' and item_name):
                                                    # Build download URL
                                                    download_url = item_url
                                                    
                                                    files.append({
                                                        'name': item_name,
                                                        'path': '',
                                                        'url': download_url,
                                                        'download_url': download_url
                                                    })
                                                    
                                                    # Stop if we've reached max_files limit
                                                    if max_files and len(files) >= max_files:
                                                        api_logger.debug(f"Reached max_files limit ({max_files}), stopping file parsing")
                                                        break
                            except Exception as e:
                                api_logger.debug(f"Error parsing list array: {str(e)}")
                                pass
            
            # Approach 2: Parse HTML - look for file items in Mail.ru Cloud structure
            if not files:
                # Look for file items in common Mail.ru Cloud HTML structures
                # Try different selectors that Mail.ru Cloud might use
                file_elements = soup.find_all(['a', 'div'], class_=re.compile(r'file|item|entry', re.I))
                
                for elem in file_elements:
                    href = elem.get('href', '')
                    if not href:
                        # Try to find link inside
                        link = elem.find('a', href=True)
                        if link:
                            href = link.get('href', '')
                    
                    text = elem.get_text(strip=True)
                    if not text:
                        # Try to find name in data attributes or title
                        text = elem.get('title') or elem.get('data-name') or elem.get('data-title', '')
                    
                    # Check if it's a file link
                    if href and ('.pdf' in href.lower() or '.png' in href.lower() or 
                                 '.jpg' in href.lower() or '.jpeg' in href.lower() or
                                 '.gif' in href.lower() or '.bmp' in href.lower()):
                        if href.startswith('//'):
                            file_url = f"https:{href}"
                        elif href.startswith('/'):
                            file_url = f"https://cloud.mail.ru{href}"
                        elif not href.startswith('http'):
                            file_url = f"{url}/{href}" if not url.endswith('/') else f"{url}{href}"
                        else:
                            file_url = href
                        
                        file_name = text or href.split('/')[-1].split('?')[0]
                        if file_name:
                            files.append({
                                'name': file_name,
                                'url': file_url,
                                'download_url': file_url,
                                'path': ''
                            })
                            
                            # Stop if we've reached max_files limit
                            if max_files and len(files) >= max_files:
                                api_logger.debug(f"Reached max_files limit ({max_files}) in Approach 2")
                                break
                
                # Also try generic links
                if not files:
                    links = soup.find_all('a', href=True)
                    for link in links:
                        href = link.get('href', '')
                        text = link.get_text(strip=True)
                        
                        # Check if it's a file link
                        if href and ('.pdf' in href.lower() or '.png' in href.lower() or 
                                     '.jpg' in href.lower() or '.jpeg' in href.lower()):
                            if href.startswith('//'):
                                file_url = f"https:{href}"
                            elif href.startswith('/'):
                                file_url = f"https://cloud.mail.ru{href}"
                            elif not href.startswith('http'):
                                file_url = f"{url}/{href}" if not url.endswith('/') else f"{url}{href}"
                            else:
                                file_url = href
                            
                            files.append({
                                'name': text or href.split('/')[-1].split('?')[0],
                                'url': file_url,
                                'download_url': file_url,
                                'path': ''
                            })
                            
                            # Stop if we've reached max_files limit
                            if max_files and len(files) >= max_files:
                                api_logger.debug(f"Reached max_files limit ({max_files}) in Approach 2 links")
                                break
            
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
                            remaining_limit = max_files - len(files) if max_files else None
                            if 'body' in data:
                                if 'list' in data['body']:
                                    files.extend(self._parse_api_files(data['body']['list'], url, remaining_limit))
                                elif 'items' in data['body']:
                                    files.extend(self._parse_api_files(data['body']['items'], url, remaining_limit))
                            elif 'list' in data:
                                files.extend(self._parse_api_files(data['list'], url, remaining_limit))
                            elif 'items' in data:
                                files.extend(self._parse_api_files(data['items'], url, remaining_limit))
                            
                            # Stop if we've reached max_files limit
                            if max_files and len(files) >= max_files:
                                api_logger.debug(f"Reached max_files limit ({max_files}) in Approach 3 API")
                                break
                            
                            if files:
                                break
                    except Exception as e:
                        api_logger.debug(f"API endpoint {api_url} failed: {str(e)}")
                        continue
            
            # Truncate to max_files if specified
            if max_files and len(files) > max_files:
                files = files[:max_files]
                api_logger.info(f"Found {len(files)} files in folder (truncated to max_files={max_files})")
            else:
                api_logger.info(f"Found {len(files)} files in folder")
            return {'files': files, 'folder_url': url}
            
        except Exception as e:
            api_logger.error(f"Error parsing Mail.ru Cloud folder: {str(e)}")
            raise
    
    def _parse_json_files(self, file_list: List, base_url: str, max_files: int = None) -> List[Dict]:
        """Parse files from JSON structure"""
        files = []
        for item in file_list:
            # Stop if we've reached max_files limit
            if max_files and len(files) >= max_files:
                api_logger.debug(f"Reached max_files limit ({max_files}) in _parse_json_files")
                break
                
            if isinstance(item, dict):
                name = item.get('name', '')
                path = item.get('path', '')
                # Construct download URL
                download_url = item.get('download_url') or item.get('url') or f"{base_url}/{name}"
                files.append({
                    'name': name,
                    'path': path,
                    'url': download_url,
                    'download_url': download_url
                })
        return files
    
    def _parse_api_files(self, file_list: List, base_url: str, max_files: int = None) -> List[Dict]:
        """Parse files from API response"""
        files = []
        for item in file_list:
            # Stop if we've reached max_files limit
            if max_files and len(files) >= max_files:
                api_logger.debug(f"Reached max_files limit ({max_files}) in _parse_api_files")
                break
                
            if isinstance(item, dict):
                name = item.get('name', '')
                path = item.get('path', '')
                # Mail.ru Cloud API may return different structures
                download_url = item.get('weblink') or item.get('download_url') or item.get('url')
                if download_url and not download_url.startswith('http'):
                    download_url = f"https://cloud.mail.ru{download_url}"
                elif not download_url:
                    download_url = f"{base_url}/{name}"
                files.append({
                    'name': name,
                    'path': path,
                    'url': download_url,
                    'download_url': download_url
                })
        return files
    
    def _fetch_folder_files(self, folder_url: str, folder_name: str, max_files: int = None) -> List[Dict]:
        """Fetch files from a subfolder (recursive)
        max_files: Stop fetching after this many files (for pagination)
        """
        files = []
        try:
            api_logger.debug(f"Fetching files from folder: {folder_url}")
            response = self.session.get(folder_url, timeout=20)
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
                                        
                                        # Only get files, not subfolders (for now)
                                        if item_type == 'file' or (item_type != 'folder' and item_name):
                                            if item_weblink:
                                                download_url = f"https://cloud.mail.ru/public/{item_weblink}"
                                            else:
                                                download_url = f"{folder_url}/{item_name}"
                                            
                                            files.append({
                                                'name': item_name,
                                                'path': folder_name,  # Store parent folder name
                                                'url': download_url,
                                                'download_url': download_url
                                            })
                                            
                                            # Stop if we've reached max_files limit
                                            if max_files and len(files) >= max_files:
                                                api_logger.debug(f"Reached max_files limit ({max_files}) in subfolder {folder_name}")
                                                return files
                                break
                            except:
                                pass
        except Exception as e:
            api_logger.debug(f"Error fetching folder files: {str(e)}")
        
        return files
    
    def download_file(self, url: str) -> bytes:
        """Download file from URL"""
        try:
            api_logger.info(f"Downloading file: {url}")
            response = self.session.get(url, timeout=30, stream=True)
            response.raise_for_status()
            return response.content
        except Exception as e:
            api_logger.error(f"Error downloading file: {str(e)}")
            raise

