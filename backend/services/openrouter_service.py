"""
OpenRouter Service for sketch analysis and text extraction
Handles vision models for drawing analysis and text extraction
"""

import os
import base64
import json
import httpx
import re
from typing import Dict, Optional, List
from services.logger import api_logger

# OpenRouter API configuration
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_API_URL = os.getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions")

# Vision models for sketch analysis and text extraction
# Порядок попыток подключения к API для анализа чертежей и извлечения текста
# Топовые платные модели
DETECTION_FALLBACKS = [
    {"provider": "openrouter", "model": "openai/gpt-4o"},  # GPT-4o - лучшая для технических чертежей
    {"provider": "openrouter", "model": "anthropic/claude-3.5-sonnet"},  # Claude 3.5 Sonnet - баланс качества и стоимости
    {"provider": "openrouter", "model": "google/gemini-1.5-pro"},  # Gemini 1.5 Pro - сильные возможности обработки изображений
    # Бесплатные и бюджетные варианты
    {"provider": "openrouter", "model": "google/gemini-2.0-flash-exp"},  # Gemini 2.0 Flash Experimental (бесплатная)
    {"provider": "openrouter", "model": "qwen/qwen-2-vl-72b-instruct"},  # Qwen2-VL - высокая производительность
    {"provider": "openrouter", "model": "mistralai/pixtral-large"},  # Pixtral Large - 124B параметров
    {"provider": "openrouter", "model": "x-ai/grok-4.1-fast:free"},  # Grok 4.1 Fast (бесплатная)
    {"provider": "openrouter", "model": "google/gemini-2.0-flash-001"}  # Google Gemini 2.0 Flash
]

# Text models for translation
TEXT_MODELS = [
    {"provider": "openrouter", "model": "anthropic/claude-3.5-sonnet"},  # Best for translation
    {"provider": "openrouter", "model": "openai/gpt-4o"},  # GPT-4o
    {"provider": "openrouter", "model": "google/gemini-1.5-pro"},  # Gemini 1.5 Pro
    {"provider": "openrouter", "model": "google/gemini-2.0-flash-001"}  # Fast fallback
]

# Настройки моделей по умолчанию
DEFAULT_VISION_MODEL = "google/gemini-2.0-flash-001"  # Для анализа чертежей и извлечения текста
DEFAULT_TEXT_MODEL = "anthropic/claude-3.5-sonnet"  # Для перевода

# Legacy compatibility
VISION_MODELS = [m for m in DETECTION_FALLBACKS if m["provider"] == "openrouter"]


class OpenRouterService:
    """Service for OpenRouter API - sketch analysis and text extraction"""
    
    def __init__(self):
        self.api_key = OPENROUTER_API_KEY
        self.api_url = OPENROUTER_API_URL
        self.vision_models = [m["model"] for m in DETECTION_FALLBACKS if m["provider"] == "openrouter"]
        self.text_models = [m["model"] for m in TEXT_MODELS if m["provider"] == "openrouter"]
        self.detection_fallbacks = DETECTION_FALLBACKS
    
    def is_available(self) -> bool:
        """Check if OpenRouter service is available"""
        return bool(self.api_key)
    
    async def analyze_sketch_with_vision(
        self,
        image_base64: str,
        model: Optional[str] = None,
        temperature: float = 0.0,
        max_tokens: int = 2000
    ) -> Optional[Dict]:
        """
        Analyze technical drawing/sketch using vision model
        Extracts: materials, GOST/OST/TU standards, Ra values, fits, heat treatment
        """
        if not self.api_key:
            api_logger.warning("OpenRouter API key not found")
            return None
        
        # Remove data:image prefix if present
        if ',' in image_base64:
            image_base64 = image_base64.split(',')[1]
        
        # Use provided model or default
        model_to_use = model or DEFAULT_VISION_MODEL
        
        # СНАЧАЛА пробуем выбранную пользователем модель
        models_to_try = [model_to_use]
        api_logger.info(f"🎯 Приоритет: используем выбранную модель для анализа: {model_to_use}")
        
        # Затем добавляем fallback модели из DETECTION_FALLBACKS (кроме уже добавленной)
        for fallback in self.detection_fallbacks:
            if fallback["provider"] == "openrouter":
                model_name = fallback["model"]
                if model_name != model_to_use:  # Не добавляем, если уже есть
                    models_to_try.append(model_name)
        
        for model_name in models_to_try:
            try:
                api_logger.info(f"Пробуем OpenRouter vision модель: {model_name}")
                
                url = self.api_url
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://localhost:5000",
                    "X-Title": "Retro Drawing Analyzer"
                }
                
                prompt = """Ты специалист по техническим чертежам. Проанализируй это изображение чертежа и извлеки следующую информацию:

1. Материалы (materials) - марки сталей, металлов, сплавов
2. Стандарты (standards) - ГОСТ, ОСТ, ТУ с номерами
3. Шероховатость (raValues) - значения Ra (например, Ra 1.6, Ra 3.2)
4. Посадки (fits) - обозначения посадок (например, H7/f7, H8/d9)
5. Термообработка (heatTreatment) - виды термообработки (закалка, отжиг, нормализация и т.д.)
6. Весь текст на чертеже (rawText) - извлеки весь видимый текст на русском и английском языках

Верни результат в формате JSON с полями:
{
  "materials": ["список материалов"],
  "standards": ["список стандартов"],
  "raValues": [числовые значения Ra],
  "fits": ["список посадок"],
  "heatTreatment": ["список видов термообработки"],
  "rawText": "весь извлеченный текст"
}

Если какое-то поле не найдено, верни пустой массив или пустую строку."""
                
                payload = {
                    "model": model_name,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": prompt
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_base64}"
                                    }
                                }
                            ]
                        }
                    ],
                    "temperature": temperature,
                    "max_tokens": max_tokens
                }
                
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    
                    if response.status_code != 200:
                        error_text = response.text[:500] if response.text else "No error message"
                        api_logger.error(f"OpenRouter API error: HTTP {response.status_code}")
                        api_logger.error(f"Response: {error_text}")
                        continue
                    
                    result = response.json()
                    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                    
                    if not content:
                        api_logger.warning(f"Model {model_name} returned empty content")
                        continue
                    
                    # Try to parse JSON from response
                    try:
                        json_start = content.find("{")
                        json_end = content.rfind("}") + 1
                        if json_start >= 0 and json_end > json_start:
                            sketch_data = json.loads(content[json_start:json_end])
                        else:
                            # Try to parse from text
                            sketch_data = self._parse_sketch_data_from_text(content)
                    except json.JSONDecodeError as e:
                        api_logger.warning(f"Failed to parse JSON from {model_name}: {e}")
                        # Try to parse from text
                        sketch_data = self._parse_sketch_data_from_text(content)
                    
                    if sketch_data:
                        api_logger.info(f"✅ Successfully analyzed sketch with model: {model_name}")
                        return {
                            "data": sketch_data,
                            "model": model_name,
                            "provider": "openrouter"
                        }
                    
            except httpx.RequestException as e:
                api_logger.error(f"OpenRouter API request error with {model_name}: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    api_logger.error(f"HTTP {e.response.status_code}: {e.response.text[:500] if e.response.text else 'No error message'}")
                continue
            except Exception as e:
                api_logger.error(f"Unexpected error with {model_name}: {e}")
                continue
        
        api_logger.error("="*80)
        api_logger.error("❌ ОШИБКА: Все OpenRouter vision модели не сработали!")
        api_logger.error("   Проверьте:")
        api_logger.error("   1. API ключ OPENROUTER_API_KEY в переменных окружения Railway")
        api_logger.error("   2. Интернет-соединение")
        api_logger.error("   3. Доступность API провайдеров")
        api_logger.error("="*80)
        return None
    
    def _parse_sketch_data_from_text(self, text: str) -> Dict:
        """Parse sketch analysis data from text response"""
        result = {
            "materials": [],
            "standards": [],
            "raValues": [],
            "fits": [],
            "heatTreatment": [],
            "rawText": text
        }
        
        text_lower = text.lower()
        
        # Extract materials (steel grades, metals)
        material_patterns = [
            r"материал[ы]?[:\s]+([^\n]+)",
            r"сталь[:\s]+([^\n]+)",
            r"steel[:\s]+([^\n]+)",
            r"материал[ы]?\s*=\s*\[([^\]]+)\]"
        ]
        for pattern in material_patterns:
            matches = re.findall(pattern, text_lower, re.IGNORECASE)
            for match in matches:
                materials = [m.strip() for m in re.split(r'[,;]', match)]
                result["materials"].extend(materials)
        
        # Extract standards (GOST, OST, TU)
        standard_patterns = [
            r"(гост\s*\d+[\.\-]?\d*)",
            r"(ост\s*\d+[\.\-]?\d*)",
            r"(ту\s*\d+[\.\-]?\d*)",
            r"(gost\s*\d+[\.\-]?\d*)"
        ]
        for pattern in standard_patterns:
            matches = re.findall(pattern, text_lower, re.IGNORECASE)
            result["standards"].extend([m.strip() for m in matches])
        
        # Extract Ra values
        ra_patterns = [
            r"ra\s*[=:]?\s*(\d+\.?\d*)",
            r"шероховатость[:\s]+ra\s*(\d+\.?\d*)",
            r"roughness[:\s]+ra\s*(\d+\.?\d*)"
        ]
        for pattern in ra_patterns:
            matches = re.findall(pattern, text_lower, re.IGNORECASE)
            for match in matches:
                try:
                    result["raValues"].append(float(match))
                except:
                    pass
        
        # Extract fits
        fit_patterns = [
            r"посадка[ы]?[:\s]+([^\n]+)",
            r"fit[:\s]+([^\n]+)",
            r"([a-z]\d+[/\\][a-z]\d+)",  # H7/f7 format
        ]
        for pattern in fit_patterns:
            matches = re.findall(pattern, text_lower, re.IGNORECASE)
            result["fits"].extend([m.strip() for m in matches])
        
        # Extract heat treatment
        heat_patterns = [
            r"термообработка[:\s]+([^\n]+)",
            r"heat\s*treatment[:\s]+([^\n]+)",
            r"(закалка|отжиг|нормализация|отпуск)",
        ]
        for pattern in heat_patterns:
            matches = re.findall(pattern, text_lower, re.IGNORECASE)
            result["heatTreatment"].extend([m.strip() for m in matches])
        
        # Remove duplicates
        result["materials"] = list(set(result["materials"]))
        result["standards"] = list(set(result["standards"]))
        result["raValues"] = list(set(result["raValues"]))
        result["fits"] = list(set(result["fits"]))
        result["heatTreatment"] = list(set(result["heatTreatment"]))
        
        return result
    
    async def extract_text_from_image(
        self,
        image_base64: str,
        languages: List[str] = ["rus", "eng"],
        model: Optional[str] = None
    ) -> Optional[str]:
        """
        Extract text from sketch/drawing image using vision model
        Supports Russian and English text extraction
        """
        if not self.api_key:
            api_logger.warning("OpenRouter API key not found")
            return None
        
        # Remove data:image prefix if present
        if ',' in image_base64:
            image_base64 = image_base64.split(',')[1]
        
        # Use provided model or default
        model_to_use = model or DEFAULT_VISION_MODEL
        
        # СНАЧАЛА пробуем выбранную пользователем модель
        models_to_try = [model_to_use]
        api_logger.info(f"🎯 Приоритет: используем выбранную модель для извлечения текста: {model_to_use}")
        
        # Затем добавляем fallback модели из DETECTION_FALLBACKS (кроме уже добавленной)
        for fallback in self.detection_fallbacks:
            if fallback["provider"] == "openrouter":
                model_name = fallback["model"]
                if model_name != model_to_use:  # Не добавляем, если уже есть
                    models_to_try.append(model_name)
        
        lang_names = {
            "rus": "Russian",
            "ru": "Russian",
            "russian": "Russian",
            "eng": "English",
            "en": "English",
            "english": "English"
        }
        lang_list = ", ".join([lang_names.get(lang.lower(), lang) for lang in languages])
        
        for model_name in models_to_try:
            try:
                api_logger.info(f"Extracting text with OpenRouter model: {model_name}")
                
                url = self.api_url
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://localhost:5000",
                    "X-Title": "Retro Drawing Analyzer"
                }
                
                prompt = f"""Ты эксперт по OCR (оптическое распознавание символов). Извлеки весь текст из этого изображения технического чертежа.

Языки для распознавания: {lang_list}

Верни ТОЛЬКО извлеченный текст, сохраняя структуру и переносы строк.
Не добавляй объяснений или комментариев.
Извлекай текст на русском и английском языках, как он есть на чертеже."""
                
                payload = {
                    "model": model_name,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": prompt
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/jpeg;base64,{image_base64}"
                                    }
                                }
                            ]
                        }
                    ],
                    "temperature": 0.0,
                    "max_tokens": 4000
                }
                
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    
                    if response.status_code != 200:
                        api_logger.warning(f"Model {model_name} failed: HTTP {response.status_code}")
                        continue
                    
                    result = response.json()
                    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                    
                    if content:
                        api_logger.info(f"✅ Text extracted successfully with model: {model_name}")
                        return content
                    
            except httpx.RequestException as e:
                api_logger.error(f"OpenRouter API request error with {model_name}: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    api_logger.error(f"HTTP {e.response.status_code}: {e.response.text[:500] if e.response.text else 'No error message'}")
                continue
            except Exception as e:
                api_logger.error(f"Error extracting text with {model_name}: {e}")
                continue
        
        api_logger.error("="*80)
        api_logger.error("❌ ОШИБКА: Все модели не смогли извлечь текст!")
        api_logger.error("   Проверьте:")
        api_logger.error("   1. API ключ OPENROUTER_API_KEY в переменных окружения Railway")
        api_logger.error("   2. Интернет-соединение")
        api_logger.error("   3. Доступность API провайдеров")
        api_logger.error("="*80)
        return None
    
    async def translate_text(
        self,
        text: str,
        target_language: str = "en",
        model: Optional[str] = None,
        use_glossary: bool = True
    ) -> Optional[str]:
        """
        Translate text using OpenRouter text models
        Supports technical glossary for Russian to English translation
        """
        if not self.api_key:
            api_logger.warning("OpenRouter API key not found")
            return None
        
        # Apply technical glossary if needed
        if use_glossary:
            text = self._apply_technical_glossary(text)
        
        # Use provided model or default
        model_to_use = model or DEFAULT_TEXT_MODEL
        
        # Try models in priority order
        models_to_try = [model_to_use] + [m for m in self.text_models if m != model_to_use]
        
        target_lang_name = "English" if target_language.lower() in ["en", "eng", "english"] else "Russian"
        
        for model_name in models_to_try:
            try:
                api_logger.info(f"Translating with OpenRouter model: {model_name}")
                
                url = self.api_url
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://localhost:5000",
                    "X-Title": "Retro Drawing Analyzer"
                }
                
                prompt = f"""Ты специалист по техническому переводу. Переведи следующий текст с русского на {target_lang_name}, используя технический глоссарий для чертежей и машиностроения.

Сохрани технические термины, стандарты (ГОСТ, ОСТ, ТУ), обозначения (Ra, посадки) в правильном формате.

Текст для перевода:
{text}

Верни только переведенный текст без дополнительных объяснений."""
                
                payload = {
                    "model": model_name,
                    "messages": [
                        {
                            "role": "user",
                            "content": prompt
                        }
                    ],
                    "temperature": 0.3,
                    "max_tokens": 2000
                }
                
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    
                    if response.status_code != 200:
                        api_logger.warning(f"Model {model_name} failed: HTTP {response.status_code}")
                        continue
                    
                    result = response.json()
                    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                    
                    if content:
                        api_logger.info(f"✅ Translation completed with model: {model_name}")
                        return content
                    
            except Exception as e:
                api_logger.error(f"Error translating with {model_name}: {e}")
                continue
        
        api_logger.error("All models failed to translate")
        return None
    
    def _apply_technical_glossary(self, text: str) -> str:
        """Apply technical glossary for better translation"""
        glossary = {
            "материал": "material",
            "сталь": "steel",
            "ГОСТ": "GOST",
            "ОСТ": "OST",
            "ТУ": "TU",
            "посадка": "fit",
            "термообработка": "heat treatment",
            "шероховатость": "roughness",
            "Ra": "Ra",
            "точность": "accuracy",
            "допуск": "tolerance",
        }
        
        translated = text
        for ru_term, en_term in glossary.items():
            pattern = re.compile(r'\b' + re.escape(ru_term) + r'\b', re.IGNORECASE)
            translated = pattern.sub(en_term, translated)
        
        return translated


