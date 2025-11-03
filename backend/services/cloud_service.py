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
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
    
    def parse_mailru_folder(self, url: str) -> Dict:
        """
        Parse Mail.ru Cloud public folder URL
        Returns list of files with their download URLs
        """
        try:
            api_logger.info(f"Fetching Mail.ru Cloud folder: {url}")
            
            # Extract folder hash from URL
            # Format: https://cloud.mail.ru/public/ZVeV/Mq5HoaFGX
            match = re.search(r'/public/([^/]+)/([^/]+)', url)
            if not match:
                raise ValueError("Invalid Mail.ru Cloud URL format")
            
            folder_hash = match.group(2)
            
            # Try to fetch folder page
            response = self.session.get(url, timeout=10)
            response.raise_for_status()
            
            # Parse HTML
            soup = BeautifulSoup(response.text, 'html.parser')
            
            files = []
            
            # Try to find file listings in the page
            # Mail.ru Cloud structure may vary, so we try multiple approaches
            
            # Approach 1: Look for script tags with JSON data
            scripts = soup.find_all('script')
            for script in scripts:
                if script.string:
                    # Look for file data in JavaScript
                    if 'files' in script.string.lower() or 'items' in script.string.lower():
                        # Try to extract JSON
                        json_match = re.search(r'\{.*"files".*\}', script.string, re.DOTALL)
                        if json_match:
                            try:
                                import json
                                data = json.loads(json_match.group(0))
                                if 'files' in data:
                                    files.extend(self._parse_json_files(data['files'], url))
                            except:
                                pass
            
            # Approach 2: Parse HTML links
            if not files:
                links = soup.find_all('a', href=True)
                for link in links:
                    href = link.get('href', '')
                    text = link.get_text(strip=True)
                    
                    # Check if it's a file link
                    if href and ('.pdf' in href.lower() or '.png' in href.lower() or 
                                 '.jpg' in href.lower() or '.jpeg' in href.lower()):
                        file_url = href if href.startswith('http') else f"https://cloud.mail.ru{href}"
                        files.append({
                            'name': text or href.split('/')[-1],
                            'url': file_url,
                            'download_url': file_url,
                            'path': ''
                        })
            
            # Approach 3: Direct API call (if we can determine the API endpoint)
            if not files:
                # Try Mail.ru Cloud API
                api_url = f"https://cloud.mail.ru/api/v2/folder?weblink={folder_hash}"
                try:
                    api_response = self.session.get(api_url, timeout=10)
                    if api_response.status_code == 200:
                        data = api_response.json()
                        if 'body' in data and 'list' in data['body']:
                            files.extend(self._parse_api_files(data['body']['list'], url))
                except:
                    pass
            
            api_logger.info(f"Found {len(files)} files in folder")
            return {'files': files, 'folder_url': url}
            
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
                # Construct download URL
                download_url = item.get('download_url') or item.get('url') or f"{base_url}/{name}"
                files.append({
                    'name': name,
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

