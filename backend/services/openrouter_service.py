"""
OpenRouter Service for sketch analysis and text extraction
Handles vision models for drawing analysis and text extraction

СИСТЕМА FALLBACK МОДЕЛЕЙ:
- Приоритет отдается специализированным OCR моделям (Qwen, InternVL, GOT-OCR)
- Затем используются универсальные модели высокого качества (GPT-4o, Claude, Gemini)
- В конце идут бесплатные и бюджетные варианты
- Система пробует ВСЕ модели до получения результата - максимальная надежность извлечения текста
"""

import os
import base64
import json
import httpx
import re
import io
from typing import Dict, Optional, List
from services.logger import api_logger

# OCR Fallback libraries
try:
    import PyPDF2
    PYPDF2_AVAILABLE = True
except ImportError:
    PYPDF2_AVAILABLE = False

try:
    import pytesseract
    from PIL import Image
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

try:
    from pdf2image import convert_from_bytes
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False

# OpenRouter API configuration
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_API_URL = os.getenv("OPENROUTER_API_URL", "https://openrouter.ai/api/v1/chat/completions")

# Vision models for sketch analysis and text extraction
# Порядок попыток подключения к API для анализа чертежей и извлечения текста
# ПРИОРИТЕТ: специализированные OCR модели для максимально надежного извлечения текста
DETECTION_FALLBACKS = [
    # ===== СПЕЦИАЛИЗИРОВАННЫЕ OCR МОДЕЛИ (ВЫСШИЙ ПРИОРИТЕТ) =====
    {"provider": "openrouter", "model": "qwen/qwen3-vl-32b-instruct"},  # Qwen3-VL-32B - распознавание текста в 32 языках (rus/eng), контекст 256K
    {"provider": "openrouter", "model": "qwen/qwen2.5-vl-72b-instruct"},  # Qwen2.5-VL-72B - высокая производительность OCR, DocVQA
    {"provider": "openrouter", "model": "qwen/qwen2.5-vl-32b-instruct"},  # Qwen2.5-VL-32B - оптимизирован для визуальных задач, интерпретация текста
    {"provider": "openrouter", "model": "internvl/internvl2-78b"},  # InternVL 2.5 78B - отличные результаты в анализе структур документов
    {"provider": "openrouter", "model": "internvl/internvl2-26b"},  # InternVL 2.5 26B - высокий баланс скорости и качества
    {"provider": "openrouter", "model": "internvl/internvl2-8b"},  # InternVL 2.5 8B - быстрая версия для OCR
    {"provider": "openrouter", "model": "got-ocr/got-ocr-2.0"},  # GOT-OCR 2.0 - единая архитектура для текста, графиков, формул, таблиц
    
    # ===== УНИВЕРСАЛЬНЫЕ МОДЕЛИ (ВЫСОКОЕ КАЧЕСТВО) =====
    {"provider": "openrouter", "model": "openai/gpt-4o"},  # GPT-4o - лучшая для технических чертежей
    {"provider": "openrouter", "model": "anthropic/claude-3.5-sonnet"},  # Claude 3.5 Sonnet - баланс качества и стоимости
    {"provider": "openrouter", "model": "google/gemini-1.5-pro"},  # Gemini 1.5 Pro - сильные возможности обработки изображений
    
    # ===== БЕСПЛАТНЫЕ И БЮДЖЕТНЫЕ ВАРИАНТЫ =====
    {"provider": "openrouter", "model": "qwen/qwen-2-vl-72b-instruct"},  # Qwen2-VL-72B - legacy версия
    {"provider": "openrouter", "model": "google/gemini-2.0-flash-exp"},  # Gemini 2.0 Flash Experimental (бесплатная)
    {"provider": "openrouter", "model": "google/gemini-2.0-flash-001"},  # Google Gemini 2.0 Flash
    {"provider": "openrouter", "model": "mistralai/pixtral-large"},  # Pixtral Large - 124B параметров
    {"provider": "openrouter", "model": "x-ai/grok-4.1-fast:free"},  # Grok 4.1 Fast (бесплатная)
    {"provider": "openrouter", "model": "internvl/internvl2-1b"},  # InternVL 2.5 1B - минимальная версия для fine-tuning
]

# Text models for translation
TEXT_MODELS = [
    {"provider": "openrouter", "model": "anthropic/claude-3.5-sonnet"},  # Best for translation
    {"provider": "openrouter", "model": "openai/gpt-4o"},  # GPT-4o
    {"provider": "openrouter", "model": "google/gemini-1.5-pro"},  # Gemini 1.5 Pro
    {"provider": "openrouter", "model": "google/gemini-2.0-flash-001"}  # Fast fallback
]

# Настройки моделей по умолчанию
# Используем специализированную OCR модель по умолчанию для максимальной надежности
DEFAULT_VISION_MODEL = "qwen/qwen3-vl-32b-instruct"  # Qwen3-VL - лучшая для извлечения текста (rus/eng)
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
        
        api_logger.info(f"🔄 Начинаем извлечение текста - будет испробовано {len(models_to_try)} моделей")
        api_logger.info(f"   Первая попытка: {models_to_try[0]}")
        
        for idx, model_name in enumerate(models_to_try, 1):
            try:
                api_logger.info(f"📝 Попытка {idx}/{len(models_to_try)}: Извлечение текста с моделью {model_name}")
                
                url = self.api_url
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "http://localhost:5000",
                    "X-Title": "Retro Drawing Analyzer"
                }
                
                prompt = f"""Ты профессиональный OCR-система с высочайшей точностью распознавания текста. Твоя задача - извлечь ВЕСЬ текст из этого изображения технического чертежа.

КРИТИЧЕСКИ ВАЖНО:
- Языки для распознавания: {lang_list}
- Извлеки ВСЕ видимые символы, цифры, буквы, знаки
- Сохраняй точную структуру: переносы строк, абзацы, расположение
- Извлекай текст на русском и английском языках ТОЧНО как он написан
- Включай все надписи, размеры, обозначения, стандарты (ГОСТ, ОСТ, ТУ)
- Извлекай технические термины, марки материалов, номера деталей

Верни ТОЛЬКО извлеченный текст без каких-либо объяснений, комментариев или форматирования.
Текст должен быть максимально полным и точным - это критически важно для последующей обработки."""
                
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
                    "max_tokens": 8000  # Увеличен лимит для больших документов с множеством текста
                }
                
                async with httpx.AsyncClient(timeout=60.0) as client:
                    response = await client.post(url, headers=headers, json=payload)
                    
                    if response.status_code != 200:
                        error_text = response.text[:500] if response.text else "No error message"
                        api_logger.warning(f"Model {model_name} failed: HTTP {response.status_code}")
                        api_logger.warning(f"   Ошибка: {error_text}")
                        
                        # Проверяем, не является ли это ошибкой "cannot process PDF"
                        if "pdf" in error_text.lower() or "cannot process" in error_text.lower() or "not capable" in error_text.lower():
                            api_logger.warning(f"⚠️ Модель {model_name} не может обработать PDF, пропускаем...")
                        continue
                    
                    result = response.json()
                    content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                    
                    # Проверяем, не содержит ли ответ сообщение об ошибке
                    if content:
                        content_lower = content.lower()
                        error_phrases = [
                            "cannot process", "not capable", "i am not able", 
                            "unable to", "i'm not able", "cannot directly process",
                            "i'm a large language model", "i am a large language model",
                            "unfortunately", "i am not capable of directly processing",
                            "i'm not capable", "cannot directly", "unable to process"
                        ]
                        if any(phrase in content_lower for phrase in error_phrases):
                            api_logger.warning(f"⚠️ Модель {model_name} сообщает, что не может обработать данные")
                            api_logger.warning(f"   Ответ: {content[:300]}...")
                            continue
                    
                    if content and len(content.strip()) > 0:
                        api_logger.info(f"✅ УСПЕХ! Текст извлечен с моделью {model_name} (попытка {idx}/{len(models_to_try)})")
                        api_logger.info(f"   Извлечено символов: {len(content)}")
                        api_logger.info(f"   Превью: {content[:100]}...")
                        return content
                    else:
                        api_logger.warning(f"⚠️ Модель {model_name} вернула пустой результат")
                    
            except httpx.RequestException as e:
                api_logger.error(f"OpenRouter API request error with {model_name}: {e}")
                if hasattr(e, 'response') and e.response is not None:
                    api_logger.error(f"HTTP {e.response.status_code}: {e.response.text[:500] if e.response.text else 'No error message'}")
                continue
            except Exception as e:
                api_logger.error(f"Error extracting text with {model_name}: {e}")
                continue
        
        # Если все OpenRouter модели не сработали, пробуем OCR fallback'и
        api_logger.warning("="*80)
        api_logger.warning("⚠️ Все OpenRouter модели не смогли извлечь текст")
        api_logger.warning(f"   Испробовано моделей: {len(models_to_try)}")
        api_logger.warning("🔄 Переключаемся на OCR fallback'и (PyPDF2, Tesseract)...")
        api_logger.warning("="*80)
        
        # Попытка извлечь текст через OCR fallback'и
        ocr_text = await self._extract_text_with_ocr_fallback(image_base64, languages)
        if ocr_text:
            return ocr_text
        
        api_logger.error("="*80)
        api_logger.error("❌ КРИТИЧЕСКАЯ ОШИБКА: Все методы не смогли извлечь текст!")
        api_logger.error(f"   Испробовано OpenRouter моделей: {len(models_to_try)}")
        api_logger.error("   Испробованы OCR fallback'и: PyPDF2, Tesseract")
        api_logger.error("   Проверьте:")
        api_logger.error("   1. API ключ OPENROUTER_API_KEY в переменных окружения Railway")
        api_logger.error("   2. Интернет-соединение")
        api_logger.error("   3. Доступность API провайдеров")
        api_logger.error("   4. Формат данных (должен быть base64 изображение или PDF)")
        api_logger.error("="*80)
        return None
    
    async def _extract_text_with_ocr_fallback(
        self,
        image_base64: str,
        languages: List[str]
    ) -> Optional[str]:
        """
        Fallback методы OCR для извлечения текста, когда OpenRouter модели не сработали
        Использует PyPDF2 для PDF с текстом и Tesseract для изображений/сканированных PDF
        """
        api_logger.info("🔧 Используем OCR fallback'и...")
        
        try:
            # Декодируем base64
            try:
                image_data = base64.b64decode(image_base64)
            except Exception as e:
                api_logger.error(f"❌ Ошибка декодирования base64: {e}")
                return None
            
            # Проверяем, является ли это PDF
            is_pdf = image_data[:4] == b'%PDF'
            
            if is_pdf:
                api_logger.info("📄 Обнаружен PDF файл, пробуем извлечь текст...")
                
                # Метод 1: PyPDF2 для PDF с текстовым слоем
                if PYPDF2_AVAILABLE:
                    try:
                        api_logger.info("   Попытка 1: PyPDF2 (для PDF с текстом)...")
                        pdf_reader = PyPDF2.PdfReader(io.BytesIO(image_data))
                        text_parts = []
                        
                        for page_num, page in enumerate(pdf_reader.pages, 1):
                            try:
                                page_text = page.extract_text()
                                if page_text.strip():
                                    text_parts.append(f"--- Страница {page_num} ---\n{page_text}")
                            except Exception as e:
                                api_logger.warning(f"   Ошибка извлечения текста со страницы {page_num}: {e}")
                                continue
                        
                        if text_parts:
                            full_text = "\n\n".join(text_parts)
                            api_logger.info(f"✅ PyPDF2 успешно извлек текст: {len(full_text)} символов")
                            return full_text
                        else:
                            api_logger.warning("   PyPDF2 не нашел текста (возможно, сканированный PDF)")
                    except Exception as e:
                        api_logger.warning(f"   PyPDF2 не сработал: {e}")
                
                # Метод 2: Tesseract OCR для сканированных PDF
                if TESSERACT_AVAILABLE and PDF2IMAGE_AVAILABLE:
                    try:
                        api_logger.info("   Попытка 2: pdf2image + Tesseract OCR (для сканированных PDF)...")
                        
                        # Конвертируем PDF в изображения
                        images = convert_from_bytes(image_data)
                        api_logger.info(f"   PDF конвертирован в {len(images)} изображений")
                        
                        # Маппинг языков для Tesseract
                        lang_map = {
                            "rus": "rus", "ru": "rus", "russian": "rus",
                            "eng": "eng", "en": "eng", "english": "eng"
                        }
                        tesseract_langs = "+".join([lang_map.get(lang.lower(), "eng") for lang in languages])
                        
                        text_parts = []
                        for page_num, img in enumerate(images, 1):
                            try:
                                page_text = pytesseract.image_to_string(img, lang=tesseract_langs)
                                if page_text.strip():
                                    text_parts.append(f"--- Страница {page_num} ---\n{page_text}")
                            except Exception as e:
                                api_logger.warning(f"   Ошибка OCR на странице {page_num}: {e}")
                                continue
                        
                        if text_parts:
                            full_text = "\n\n".join(text_parts)
                            api_logger.info(f"✅ Tesseract успешно извлек текст: {len(full_text)} символов")
                            return full_text
                    except Exception as e:
                        api_logger.error(f"   Tesseract OCR не сработал: {e}")
            else:
                # Это изображение, используем Tesseract OCR
                if TESSERACT_AVAILABLE:
                    try:
                        api_logger.info("🖼️ Обнаружено изображение, используем Tesseract OCR...")
                        
                        # Открываем изображение
                        image = Image.open(io.BytesIO(image_data))
                        
                        # Маппинг языков
                        lang_map = {
                            "rus": "rus", "ru": "rus", "russian": "rus",
                            "eng": "eng", "en": "eng", "english": "eng"
                        }
                        tesseract_langs = "+".join([lang_map.get(lang.lower(), "eng") for lang in languages])
                        
                        text = pytesseract.image_to_string(image, lang=tesseract_langs)
                        
                        if text.strip():
                            api_logger.info(f"✅ Tesseract успешно извлек текст: {len(text)} символов")
                            return text
                        else:
                            api_logger.warning("   Tesseract не нашел текста в изображении")
                    except Exception as e:
                        api_logger.error(f"   Tesseract OCR не сработал: {e}")
            
        except Exception as e:
            api_logger.error(f"❌ Ошибка в OCR fallback: {e}")
        
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


