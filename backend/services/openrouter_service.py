"""
OpenRouter Service for sketch analysis and text extraction
Handles vision models for drawing analysis and text extraction
Nie 
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
    from PIL import Image, ImageEnhance, ImageFilter
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False

try:
    import numpy as np
    NUMPY_AVAILABLE = True
except ImportError:
    NUMPY_AVAILABLE = False

# OpenCV опционален - проверка будет ленивой (только при использовании)
# Не импортируем на уровне модуля, чтобы избежать ошибок при загрузке
OPENCV_AVAILABLE = None  # None означает "еще не проверяли"

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
    # ===== БЫСТРЫЕ И ЭФФЕКТИВНЫЕ OCR МОДЕЛИ (ВЫСШИЙ ПРИОРИТЕТ - ПРОВЕРЕНЫ) =====
    {"provider": "openrouter", "model": "qwen/qwen2.5-vl-72b-instruct"},  # Qwen2.5-VL-72B - быстрая, высокая производительность OCR, DocVQA
    {"provider": "openrouter", "model": "qwen/qwen2.5-vl-32b-instruct"},  # Qwen2.5-VL-32B - быстрая, оптимизирован для визуальных задач
    {"provider": "openrouter", "model": "google/gemini-2.0-flash-001"},  # Gemini 2.0 Flash - очень быстрая
    {"provider": "openrouter", "model": "google/gemini-2.0-flash-exp"},  # Gemini 2.0 Flash Experimental - быстрая, экспериментальная
    {"provider": "openrouter", "model": "internvl/internvl2-26b"},  # InternVL 2.5 26B - баланс скорости и качества
    {"provider": "openrouter", "model": "internvl/internvl2-8b"},  # InternVL 2.5 8B - быстрая версия для OCR
    
    # ===== ВЫСОКОЕ КАЧЕСТВО OCR (СРЕДНИЙ ПРИОРИТЕТ) =====
    {"provider": "openrouter", "model": "internvl/internvl2-78b"},  # InternVL 2.5 78B - отличные результаты в анализе структур документов
    {"provider": "openrouter", "model": "qwen/qwen-2-vl-72b-instruct"},  # Qwen2-VL-72B - legacy, но работает
    {"provider": "openrouter", "model": "openai/gpt-4o"},  # GPT-4o - лучшее качество для технических чертежей
    {"provider": "openrouter", "model": "anthropic/claude-3.5-sonnet"},  # Claude 3.5 Sonnet - баланс качества и стоимости
    
    # ===== УНИВЕРСАЛЬНЫЕ МОДЕЛИ (НИЗКИЙ ПРИОРИТЕТ) =====
    {"provider": "openrouter", "model": "google/gemini-1.5-pro"},  # Gemini 1.5 Pro - сильные возможности обработки изображений
    {"provider": "openrouter", "model": "mistralai/pixtral-large"},  # Pixtral Large - 124B параметров
]

# Text models for translation
TEXT_MODELS = [
    {"provider": "openrouter", "model": "anthropic/claude-3.5-sonnet"},  # Best for translation
    {"provider": "openrouter", "model": "openai/gpt-4o"},  # GPT-4o
    {"provider": "openrouter", "model": "google/gemini-1.5-pro"},  # Gemini 1.5 Pro
    {"provider": "openrouter", "model": "google/gemini-2.0-flash-001"}  # Fast fallback
]

# Настройки моделей по умолчанию
# Используем проверенную OCR модель для raster PDF по умолчанию
DEFAULT_VISION_MODEL = "qwen/qwen2.5-vl-72b-instruct"  # Qwen2.5-VL-72B - быстрая и точная для OCR
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
        self._cached_models = None  # Кэш для списка доступных моделей
    
    def is_available(self) -> bool:
        """Check if OpenRouter service is available"""
        return bool(self.api_key)
    
    async def get_available_models(self) -> Optional[List[Dict]]:
        """
        Получает список доступных моделей из OpenRouter API
        Returns: список моделей с информацией (id, name, pricing, context_length, etc.)
        """
        if not self.api_key:
            return None
        
        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "HTTP-Referer": "https://retro-sketch.app",
                "X-Title": "Retro Sketch Analyzer"
            }
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(
                    "https://openrouter.ai/api/v1/models",
                    headers=headers
                )
                
                if response.status_code == 200:
                    data = response.json()
                    models = data.get("data", [])
                    api_logger.info(f"✅ Получен список моделей: {len(models)} доступных моделей")
                    return models
                else:
                    api_logger.warning(f"⚠️ Не удалось получить список моделей: HTTP {response.status_code}")
                    return None
        except Exception as e:
            api_logger.error(f"❌ Ошибка при получении списка моделей: {e}")
            return None
    
    def _find_similar_model(self, model_name: str, available_models: List[Dict]) -> Optional[str]:
        """
        Находит наиболее похожую модель из списка доступных
        Использует fuzzy matching для поиска похожих названий
        """
        if not available_models:
            return None
        
        model_name_lower = model_name.lower()
        
        # Сначала ищем точное совпадение (case-insensitive)
        for model in available_models:
            model_id = model.get("id", "")
            if model_id.lower() == model_name_lower:
                return model_id
        
        # Затем ищем частичное совпадение
        # Разбиваем название модели на части
        parts = model_name_lower.replace("/", " ").replace("-", " ").split()
        
        best_match = None
        best_score = 0
        
        for model in available_models:
            model_id = model.get("id", "").lower()
            score = 0
            
            # Подсчитываем совпадения частей
            for part in parts:
                if part in model_id:
                    score += len(part)
            
            # Бонус за начало совпадения
            if model_id.startswith(parts[0]):
                score += 10
            
            # Если в названии модели есть OCR, vision, VL - бонус
            if any(keyword in model_id for keyword in ["ocr", "vision", "vl", "visual"]):
                score += 5
            
            if score > best_score:
                best_score = score
                best_match = model.get("id")
        
        return best_match if best_score > 0 else None
    
    async def validate_and_fix_model_name(self, model_name: str) -> Optional[str]:
        """
        Валидирует название модели и исправляет его на доступное, если нужно
        Returns: исправленное название модели или None если не найдено
        """
        if not self.api_key:
            return None
        
        # Кэшируем список моделей
        if not hasattr(self, '_cached_models') or self._cached_models is None:
            self._cached_models = await self.get_available_models()
        
        if not self._cached_models:
            return None
        
        # Ищем модель
        fixed_model = self._find_similar_model(model_name, self._cached_models)
        
        if fixed_model and fixed_model != model_name:
            api_logger.info(f"🔧 Модель '{model_name}' не найдена, исправлено на '{fixed_model}'")
        elif fixed_model:
            api_logger.debug(f"✅ Модель '{model_name}' валидна")
        
        return fixed_model
    
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
        
        # Для ускорения: если указана конкретная модель, используем только её (без fallback)
        # Это особенно важно для изображений PNG/JPG
        use_fallback = model is None  # Fallback только если модель не указана явно
        
        if use_fallback:
            # СНАЧАЛА пробуем выбранную пользователем модель
            models_to_try = [model_to_use]
            api_logger.info(f"🎯 Приоритет: используем выбранную модель для извлечения текста: {model_to_use}")
            
            # Затем добавляем fallback модели из DETECTION_FALLBACKS (кроме уже добавленной)
            for fallback in self.detection_fallbacks:
                if fallback["provider"] == "openrouter":
                    model_name = fallback["model"]
                    if model_name != model_to_use:  # Не добавляем, если уже есть
                        models_to_try.append(model_name)
            
            api_logger.info(f"🔄 Начинаем извлечение текста - будет испробовано {len(models_to_try)} моделей")
            api_logger.info(f"   Первая попытка: {models_to_try[0]}")
        else:
            # Используем только указанную модель (быстро для изображений)
            models_to_try = [model_to_use]
            api_logger.info(f"⚡ Используем только указанную модель для ускорения: {model_to_use} (без fallback)")
        
        lang_names = {
            "rus": "Russian",
            "ru": "Russian",
            "russian": "Russian",
            "eng": "English",
            "en": "English",
            "english": "English"
        }
        lang_list = ", ".join([lang_names.get(lang.lower(), lang) for lang in languages])
        
        for idx, model_name in enumerate(models_to_try, 1):
            try:
                # Валидируем и исправляем название модели
                validated_model = await self.validate_and_fix_model_name(model_name)
                if not validated_model:
                    api_logger.warning(f"⚠️ Модель '{model_name}' не найдена, пропускаем...")
                    continue
                
                if validated_model != model_name:
                    api_logger.info(f"🔧 Модель исправлена: '{model_name}' -> '{validated_model}'")
                    model_name = validated_model
                
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

ОБРАБОТКА РУКОПИСНОГО ТЕКСТА:
- Если текст написан от руки (handwritten) - примени специальное внимание к распознаванию
- Для рукописного текста важно сохранить все символы, даже если они не идеально написаны
- Распознавай рукописные цифры, буквы и технические обозначения максимально точно

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
                    
                    if response.status_code == 400 or response.status_code == 404:
                        # Модель не существует - пропускаем и пробуем следующую
                        error_text = response.text[:500] if response.text else "No error message"
                        api_logger.warning(f"Model {model_name} failed: HTTP {response.status_code}")
                        api_logger.warning(f"   Ошибка: {error_text}")
                        # Если модель не валидна, пропускаем её
                        continue
                    elif response.status_code != 200:
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
    
    def _preprocess_image_for_ocr(self, image: Image.Image) -> Image.Image:
        """
        Preprocessing изображения для улучшения качества OCR
        Улучшает контраст, резкость, убирает шум - особенно важно для русского текста
        """
        try:
            api_logger.info("   🔧 Применяем preprocessing для улучшения OCR...")
            
            # Конвертируем в RGB если нужно
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Метод 1: Увеличиваем разрешение (минимум 300 DPI для качественного OCR)
            original_size = image.size
            min_dpi = 400
            scale_factor = max(1.0, min_dpi / 72.0)  # Если изображение меньше 400 DPI
            if scale_factor > 1.0:
                new_size = (int(original_size[0] * scale_factor), int(original_size[1] * scale_factor))
                image = image.resize(new_size, Image.LANCZOS)
                api_logger.info(f"   📐 Увеличено разрешение: {original_size} → {new_size}")
            
            # Метод 2: Улучшаем контраст (критически важно для видимого текста)
            enhancer = ImageEnhance.Contrast(image)
            image = enhancer.enhance(2.0)  # Увеличиваем контраст в 2 раза
            api_logger.info("   🎨 Улучшен контраст")
            
            # Метод 3: Улучшаем резкость
            enhancer = ImageEnhance.Sharpness(image)
            image = enhancer.enhance(1.5)  # Увеличиваем резкость на 50%
            api_logger.info("   ✨ Улучшена резкость")
            
            # Метод 4: Коррекция яркости для лучшего распознавания
            enhancer = ImageEnhance.Brightness(image)
            # Определяем среднюю яркость
            pixels = list(image.getdata())
            avg_brightness = sum(sum(pixel) / 3 for pixel in pixels) / len(pixels)
            # Если слишком темное, осветляем; если слишком светлое, затемняем
            if avg_brightness < 128:
                image = enhancer.enhance(1.2)  # Осветляем
                api_logger.info("   💡 Осветлено изображение")
            elif avg_brightness > 200:
                image = enhancer.enhance(0.9)  # Затемняем
                api_logger.info("   🌙 Затемнено изображение")
            
            # Метод 5: Применяем фильтр для уменьшения шума
            image = image.filter(ImageFilter.MedianFilter(size=3))
            api_logger.info("   🧹 Применен фильтр для уменьшения шума")
            
            # Метод 6: Конвертируем в grayscale для лучшего OCR (если нужно)
            # Tesseract работает лучше с grayscale для технических чертежей
            if image.mode != 'L':
                # Сохраняем RGB для цветных изображений, но можно попробовать и grayscale
                # Для начала оставляем RGB, но добавляем метод конвертации в L (grayscale)
                pass
            
            api_logger.info("   ✅ Preprocessing завершен")
            return image
            
        except Exception as e:
            api_logger.warning(f"   ⚠️ Ошибка в preprocessing: {e}, используем оригинальное изображение")
            return image
    
    def _preprocess_image_advanced(self, image: Image.Image) -> Image.Image:
        """
        Расширенный preprocessing с бинаризацией для максимального качества OCR
        Используется для сложных случаев, когда текст плохо виден
        """
        try:
            api_logger.info("   🔬 Применяем расширенный preprocessing...")
            
            # Конвертируем в RGB
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Увеличиваем разрешение
            original_size = image.size
            scale_factor = max(2.0, 300 / 72.0)  # Минимум 2x для лучшего качества
            new_size = (int(original_size[0] * scale_factor), int(original_size[1] * scale_factor))
            image = image.resize(new_size, Image.LANCZOS)
            
            # Конвертируем в numpy array для обработки
            # Ленивая проверка OpenCV - только при использовании
            if NUMPY_AVAILABLE:
                # Проверяем доступность OpenCV только здесь
                # Ленивая проверка OpenCV - только при использовании
                try:
                    import cv2
                    _ = cv2.__version__
                    # Используем модульную переменную без global
                    import backend.services.openrouter_service as ors_module
                    ors_module.OPENCV_AVAILABLE = True
                    OPENCV_AVAILABLE = True
                except (ImportError, AttributeError, OSError) as e:
                    import backend.services.openrouter_service as ors_module
                    ors_module.OPENCV_AVAILABLE = False
                    OPENCV_AVAILABLE = False
                    api_logger.debug(f"OpenCV недоступен: {e}")
                
                if OPENCV_AVAILABLE:
                    try:
                        import cv2
                        import numpy as np
                        # Конвертируем PIL в numpy
                        img_array = np.array(image)
                        
                        # Конвертируем в grayscale
                        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
                        
                        # Применяем адаптивную бинаризацию (Оtsu или адаптивная)
                        # Это критически важно для чертежей с разным освещением
                        binary = cv2.adaptiveThreshold(
                            gray, 255, 
                            cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                            cv2.THRESH_BINARY, 
                            11, 2
                        )
                        
                        # Улучшаем контраст еще раз
                        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
                        binary = clahe.apply(binary)
                        
                        # Убираем шум
                        binary = cv2.medianBlur(binary, 3)
                        
                        # Конвертируем обратно в PIL
                        image = Image.fromarray(binary)
                        api_logger.info("   🔬 Применена адаптивная бинаризация (OpenCV)")
                    except (ImportError, OSError, AttributeError) as e:
                        api_logger.debug(f"   ⚠️ OpenCV недоступен для бинаризации: {e}")
                        # Fallback без OpenCV - используем PIL методы
            else:
                # Fallback без OpenCV - используем PIL методы
                image = image.convert('L')  # Grayscale
                
                # Применяем более агрессивную обработку
                enhancer = ImageEnhance.Contrast(image)
                image = enhancer.enhance(3.0)
                
                # Применяем threshold для бинаризации (черно-белое)
                threshold = 128
                image = image.point(lambda p: 255 if p > threshold else 0, mode='1')
                image = image.convert('L')
                api_logger.info("   🔬 Применена бинаризация (PIL)")
            
            return image
            
        except Exception as e:
            api_logger.warning(f"   ⚠️ Ошибка в расширенном preprocessing: {e}")
            return image
    
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
                
                # Метод 1: PyPDF2 для PDF с текстовым слоем (улучшенная обработка русского текста)
                if PYPDF2_AVAILABLE:
                    try:
                        api_logger.info("   Попытка 1: PyPDF2 (для PDF с текстом)...")
                        pdf_reader = PyPDF2.PdfReader(io.BytesIO(image_data))
                        text_parts = []
                        
                        for page_num, page in enumerate(pdf_reader.pages, 1):
                            try:
                                # Извлекаем текст с поддержкой кодировок
                                # Используем layout=True для лучшего извлечения текста с сохранением структуры
                                page_text = page.extract_text(layout=False)
                                
                                # Пробуем также с layout=True для сложных документов
                                if not page_text or len(page_text.strip()) < 10:
                                    page_text = page.extract_text(layout=True)
                                
                                # Улучшаем обработку русского текста
                                if page_text:
                                    # Очищаем текст, но сохраняем структуру
                                    lines = []
                                    for line in page_text.split('\n'):
                                        cleaned_line = line.strip()
                                        if cleaned_line:
                                            lines.append(cleaned_line)
                                    
                                    if lines:
                                        page_text = '\n'.join(lines)
                                        text_parts.append(f"--- Страница {page_num} ---\n{page_text}")
                                        
                            except Exception as e:
                                api_logger.warning(f"   Ошибка извлечения текста со страницы {page_num}: {e}")
                                continue
                        
                        if text_parts:
                            full_text = "\n\n".join(text_parts)
                            api_logger.info(f"✅ PyPDF2 успешно извлек текст: {len(full_text)} символов")
                            api_logger.info(f"   Превью: {full_text[:200]}...")
                            return full_text
                        else:
                            api_logger.warning("   PyPDF2 не нашел текста (возможно, сканированный PDF)")
                    except Exception as e:
                        api_logger.warning(f"   PyPDF2 не сработал: {e}")
                
                # Метод 2: Tesseract OCR для сканированных PDF
                if TESSERACT_AVAILABLE and PDF2IMAGE_AVAILABLE:
                    try:
                        api_logger.info("   Попытка 2: pdf2image + Tesseract OCR (для сканированных PDF)...")
                        
                        # Конвертируем PDF в изображения с высоким DPI для лучшего качества OCR
                        # DPI 400 - увеличен для лучшего распознавания технических чертежей
                        # Для технических чертежей нужно более высокое разрешение
                        images = convert_from_bytes(
                            image_data,
                            dpi=400,  # Увеличенное разрешение для лучшего OCR технических чертежей
                            fmt='png',  # PNG для лучшего качества
                            thread_count=4  # Параллельная обработка для скорости
                        )
                        api_logger.info(f"   PDF конвертирован в {len(images)} изображений (DPI 400)")
                        
                        # Маппинг языков для Tesseract
                        lang_map = {
                            "rus": "rus", "ru": "rus", "russian": "rus",
                            "eng": "eng", "en": "eng", "english": "eng"
                        }
                        tesseract_langs = "+".join([lang_map.get(lang.lower(), "eng") for lang in languages])
                        
                        text_parts = []
                        for page_num, img in enumerate(images, 1):
                            try:
                                # Применяем preprocessing для улучшения качества OCR
                                api_logger.info(f"   Обработка страницы {page_num}/{len(images)}...")
                                processed_img = self._preprocess_image_for_ocr(img)
                                
                                # Пробуем OCR с улучшенным изображением
                                # Для технических чертежей пробуем разные PSM режимы
                                page_text = ""
                                for psm_mode in [11, 6, 4, 12]:
                                    try:
                                        page_text = pytesseract.image_to_string(
                                            processed_img, 
                                            lang=tesseract_langs,
                                            config=f'--psm {psm_mode} --oem 3'
                                        )
                                        if page_text and len(page_text.strip()) > 10:
                                            api_logger.info(f"   ✅ Страница {page_num}: Tesseract PSM {psm_mode} успешно извлек текст ({len(page_text)} символов)")
                                            break
                                    except Exception as e:
                                        api_logger.debug(f"   PSM {psm_mode} не сработал: {e}")
                                        continue
                                
                                # Если не получилось, пробуем расширенный preprocessing
                                if not page_text or len(page_text.strip()) < 10:
                                    api_logger.info(f"   Попытка с расширенным preprocessing для страницы {page_num}...")
                                    advanced_img = self._preprocess_image_advanced(img)
                                    for psm_mode in [11, 6, 4]:
                                        try:
                                            page_text = pytesseract.image_to_string(
                                                advanced_img,
                                                lang=tesseract_langs,
                                                config=f'--psm {psm_mode} --oem 3'
                                            )
                                            if page_text and len(page_text.strip()) > 10:
                                                api_logger.info(f"   ✅ Страница {page_num}: Tesseract с расширенным preprocessing PSM {psm_mode} успешно извлек текст")
                                                break
                                        except:
                                            continue
                                
                                # Если все еще пусто, пробуем базовый режим
                                if not page_text or len(page_text.strip()) < 10:
                                    page_text = pytesseract.image_to_string(
                                        processed_img,
                                        lang=tesseract_langs,
                                        config='--psm 6 --oem 3'
                                    )
                                
                                if page_text and len(page_text.strip()) >= 5:
                                    # Очищаем и улучшаем извлеченный текст
                                    cleaned_text = '\n'.join(line.strip() for line in page_text.split('\n') if line.strip())
                                    if cleaned_text:
                                        text_parts.append(f"--- Страница {page_num} ---\n{cleaned_text}")
                                        api_logger.info(f"   ✅ Страница {page_num}: Извлечено {len(cleaned_text)} символов")
                                else:
                                    api_logger.warning(f"   ⚠️ Страница {page_num}: Не удалось извлечь текст (результат пустой или слишком короткий)")
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
                        
                        # Применяем preprocessing для улучшения качества OCR
                        api_logger.info("   Применяем preprocessing изображения...")
                        processed_image = self._preprocess_image_for_ocr(image)
                        
                        # Пробуем OCR с улучшенным изображением - множественные попытки с разными PSM режимами
                        text = ""
                        for psm_mode in [11, 6, 4, 12]:
                            try:
                                text = pytesseract.image_to_string(
                                    processed_image,
                                    lang=tesseract_langs,
                                    config=f'--psm {psm_mode} --oem 3'
                                )
                                if text and len(text.strip()) >= 10:
                                    api_logger.info(f"   ✅ Tesseract PSM {psm_mode} успешно извлек текст из изображения ({len(text)} символов)")
                                    break
                            except Exception as e:
                                api_logger.debug(f"   PSM {psm_mode} не сработал: {e}")
                                continue
                        
                        # Если не получилось, пробуем расширенный preprocessing
                        if not text or len(text.strip()) < 10:
                            api_logger.info("   Попытка с расширенным preprocessing...")
                            advanced_image = self._preprocess_image_advanced(image)
                            for psm_mode in [11, 6, 4]:
                                try:
                                    text = pytesseract.image_to_string(
                                        advanced_image,
                                        lang=tesseract_langs,
                                        config=f'--psm {psm_mode} --oem 3'
                                    )
                                    if text and len(text.strip()) >= 10:
                                        api_logger.info(f"   ✅ Tesseract с расширенным preprocessing PSM {psm_mode} успешно извлек текст")
                                        break
                                except:
                                    continue
                        
                        # Если все еще пусто, пробуем базовый режим
                        if not text or len(text.strip()) < 10:
                            text = pytesseract.image_to_string(
                                processed_image,
                                lang=tesseract_langs,
                                config='--psm 6 --oem 3'
                            )
                        
                        if text and len(text.strip()) >= 5:
                            # Очищаем и улучшаем извлеченный текст
                            cleaned_text = '\n'.join(line.strip() for line in text.split('\n') if line.strip())
                            if cleaned_text:
                                api_logger.info(f"✅ Tesseract успешно извлек текст: {len(cleaned_text)} символов")
                                api_logger.info(f"   Превью: {cleaned_text[:200]}...")
                                return cleaned_text
                        
                        api_logger.warning("   ⚠️ Tesseract не нашел текста в изображении (результат пустой или слишком короткий)")
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
    
    async def ask_question(self, prompt: str, model: Optional[str] = None) -> Optional[str]:
        """
        Задать вопрос через OpenRouter текстовую модель
        """
        if not self.api_key:
            api_logger.warning("OpenRouter API key not found")
            return None
        
        model_to_use = model or DEFAULT_TEXT_MODEL
        
        try:
            url = self.api_url
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:5000",
                "X-Title": "Retro Drawing Analyzer"
            }
            
            payload = {
                "model": model_to_use,
                "messages": [
                    {
                        "role": "system",
                        "content": "Ты эксперт по техническим чертежам и машиностроению. Отвечай подробно и точно на вопросы о чертежах, используя информацию из предоставленного текста."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.3,
                "max_tokens": 2000
            }
            
            api_logger.info(f"Задаем вопрос через модель {model_to_use}")
            
            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                
                if response.status_code != 200:
                    api_logger.error(f"Model {model_to_use} failed: HTTP {response.status_code}")
                    return None
                
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                
                if content:
                    api_logger.info(f"✅ Ответ получен: {len(content)} символов")
                    return content
                
                return None
                
        except Exception as e:
            api_logger.error(f"Error asking question: {e}")
            return None
    
    async def extract_structured_data(self, ocr_text: str) -> Optional[dict]:
        """
        Извлекает структурированные данные из OCR текста используя OpenRouter
        Аналогично методу из чата, но для структурированного извлечения данных
        Возвращает словарь с materials, standards, raValues, fits, heatTreatment
        """
        if not self.api_key:
            api_logger.warning("OpenRouter API key not found")
            return None
        
        model_to_use = DEFAULT_TEXT_MODEL  # Claude 3.5 Sonnet - та же модель, что в чате
        
        try:
            url = self.api_url
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "http://localhost:5000",
                "X-Title": "Retro Drawing Analyzer"
            }
            
            prompt = f"""Ты эксперт по извлечению структурированных данных из технических чертежей.

Извлеки из следующего OCR текста технического чертежа все данные и верни ТОЛЬКО валидный JSON без объяснений:

{{
  "materials": ["массив марок материалов, например: сталь 45, 40Х"],
  "standards": ["массив стандартов ГОСТ/ОСТ/ТУ, например: ГОСТ 1050, ОСТ 12"],
  "raValues": [массив чисел - значения шероховатости Ra, например: 1.6, 3.2],
  "fits": ["массив посадок, например: H7/f7, H8/g7"],
  "heatTreatment": ["массив термообработки, например: HRC 45-50, закалка"]
}}

Извлеки ВСЕ экземпляры каждого типа данных. Верни ТОЛЬКО валидный JSON.

OCR текст:
{ocr_text[:5000]}"""

            payload = {
                "model": model_to_use,
                "messages": [
                    {
                        "role": "system",
                        "content": "Ты эксперт по машиностроению и техническим чертежам. Извлекай структурированные данные и возвращай только валидный JSON без объяснений."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                "temperature": 0.1,
                "max_tokens": 2000,
                "response_format": {"type": "json_object"}
            }
            
            api_logger.info(f"📊 Извлечение структурированных данных через {model_to_use}")
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(url, headers=headers, json=payload)
                
                if response.status_code != 200:
                    api_logger.error(f"Model {model_to_use} failed: HTTP {response.status_code}")
                    return None
                
                result = response.json()
                content = result.get("choices", [{}])[0].get("message", {}).get("content", "")
                
                if content:
                    try:
                        import json
                        data = json.loads(content)
                        
                        # Проверяем и нормализуем структуру
                        extracted = {
                            "materials": data.get("materials", []) if isinstance(data.get("materials"), list) else [],
                            "standards": data.get("standards", []) if isinstance(data.get("standards"), list) else [],
                            "raValues": [float(x) for x in data.get("raValues", []) if isinstance(x, (int, float))] if isinstance(data.get("raValues"), list) else [],
                            "fits": data.get("fits", []) if isinstance(data.get("fits"), list) else [],
                            "heatTreatment": data.get("heatTreatment", []) if isinstance(data.get("heatTreatment"), list) else []
                        }
                        
                        api_logger.info(f"✅ Извлечено: {len(extracted['materials'])} материалов, {len(extracted['standards'])} стандартов, {len(extracted['raValues'])} Ra значений")
                        return extracted
                    except json.JSONDecodeError as e:
                        api_logger.error(f"Ошибка парсинга JSON: {e}")
                        # Пытаемся извлечь JSON из текста
                        import re
                        json_match = re.search(r'\{[\s\S]*\}', content)
                        if json_match:
                            try:
                                data = json.loads(json_match.group(0))
                                return {
                                    "materials": data.get("materials", []),
                                    "standards": data.get("standards", []),
                                    "raValues": data.get("raValues", []),
                                    "fits": data.get("fits", []),
                                    "heatTreatment": data.get("heatTreatment", [])
                                }
                            except:
                                pass
                        return None
                
                return None
                
        except Exception as e:
            api_logger.error(f"Error extracting structured data: {e}")
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


