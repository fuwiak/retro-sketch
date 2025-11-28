"""
AI Agent для выбора оптимального метода OCR на основе типа PDF
Определяет тип PDF (raster/vector) и выбирает метод от самого быстрого и точного
"""

import os
import base64
import io
import time
from typing import Dict, Optional, List
from enum import Enum
from services.logger import ocr_logger

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

# PaddleOCR опционален - импорт будет ленивым (только при использовании)
# Не импортируем на уровне модуля, так как он может требовать OpenCV
PADDLEOCR_AVAILABLE = None  # None означает "еще не проверяли"


class PDFType(Enum):
    """Тип PDF документа"""
    VECTOR = "vector"  # PDF с текстовым слоем
    RASTER = "raster"  # Сканированный PDF (изображение)
    MIXED = "mixed"    # Смешанный тип
    UNKNOWN = "unknown"


class OCRMethod(Enum):
    """Методы OCR обработки"""
    OPENROUTER_OLMOCR = "openrouter_olmocr"  # olmOCR через OpenRouter - лучший для raster PDF
    OPENROUTER_GOTOCR = "openrouter_gotocr"  # GOT-OCR 2.0 через OpenRouter
    OPENROUTER_MISTRAL = "openrouter_mistral"  # Mistral OCR через OpenRouter
    OPENROUTER_AUTO = "openrouter_auto"  # Автоматический выбор модели OpenRouter
    PADDLEOCR = "paddleocr"  # PaddleOCR - локальный, быстрый, точный (96.58%)
    TESSERACT = "tesseract"  # Tesseract - классический OCR
    PYPDF2 = "pypdf2"  # PyPDF2 - только для vector PDF
    AUTO = "auto"  # Автоматический выбор на основе типа PDF


class OCRQuality(Enum):
    """Качество OCR"""
    FAST = "fast"  # Быстрое (Tesseract, быстрые модели)
    BALANCED = "balanced"  # Сбалансированное (PaddleOCR, средние модели)
    ACCURATE = "accurate"  # Точное (OpenRouter специализированные модели)


class OCRSelectionAgent:
    """AI Agent для выбора оптимального метода OCR"""
    
    def __init__(self, openrouter_service=None):
        self.openrouter_service = openrouter_service
        self.paddleocr_available = False  # Будет проверено лениво
        self.tesseract_available = TESSERACT_AVAILABLE
        self.pypdf2_available = PYPDF2_AVAILABLE
        self.pdf2image_available = PDF2IMAGE_AVAILABLE
        
        # PaddleOCR будет инициализирован лениво (только при использовании)
        # чтобы избежать импорта OpenCV при загрузке модуля
        self.paddleocr_instance = None
    
    async def detect_pdf_type(self, file_content: bytes) -> PDFType:
        """
        Определяет тип PDF (vector/raster/mixed)
        """
        try:
            # Проверяем, что это PDF
            if not file_content[:4] == b'%PDF':
                return PDFType.UNKNOWN
            
            # Метод 1: Пробуем извлечь текст через PyPDF2
            if self.pypdf2_available:
                try:
                    pdf_reader = PyPDF2.PdfReader(io.BytesIO(file_content))
                    total_text_length = 0
                    pages_with_text = 0
                    
                    for page in pdf_reader.pages:
                        try:
                            page_text = page.extract_text()
                            if page_text and len(page_text.strip()) > 50:  # Минимум 50 символов
                                total_text_length += len(page_text)
                                pages_with_text += 1
                        except:
                            pass
                    
                    total_pages = len(pdf_reader.pages)
                    
                    if total_pages == 0:
                        return PDFType.UNKNOWN
                    
                    # Если больше 80% страниц содержат текст - это vector PDF
                    text_ratio = pages_with_text / total_pages
                    avg_text_per_page = total_text_length / total_pages if total_pages > 0 else 0
                    
                    if text_ratio > 0.8 and avg_text_per_page > 100:
                        ocr_logger.info(f"📄 PDF тип: VECTOR (текст найден на {pages_with_text}/{total_pages} страницах)")
                        return PDFType.VECTOR
                    elif text_ratio > 0.3:
                        ocr_logger.info(f"📄 PDF тип: MIXED (текст найден на {pages_with_text}/{total_pages} страницах)")
                        return PDFType.MIXED
                    else:
                        ocr_logger.info(f"📄 PDF тип: RASTER (текст найден на {pages_with_text}/{total_pages} страницах)")
                        return PDFType.RASTER
                except Exception as e:
                    ocr_logger.warning(f"⚠️ Ошибка при определении типа PDF через PyPDF2: {e}")
            
            # Метод 2: Анализ первой страницы как изображения
            if self.pdf2image_available:
                try:
                    images = convert_from_bytes(file_content, dpi=150, first_page=1, last_page=1)
                    if images:
                        # Если удалось конвертировать в изображение - вероятно raster
                        # Но это не точный метод, используем как fallback
                        ocr_logger.info("📄 PDF тип: RASTER (определено через конвертацию в изображение)")
                        return PDFType.RASTER
                except Exception as e:
                    ocr_logger.warning(f"⚠️ Ошибка при конвертации PDF: {e}")
            
            # По умолчанию считаем raster (более частый случай для сканированных документов)
            ocr_logger.info("📄 PDF тип: RASTER (по умолчанию)")
            return PDFType.RASTER
            
        except Exception as e:
            ocr_logger.error(f"❌ Ошибка при определении типа PDF: {e}")
            return PDFType.UNKNOWN
    
    def select_ocr_method(
        self,
        pdf_type: PDFType,
        user_method: str = "auto",
        quality: str = "balanced"
    ) -> OCRMethod:
        """
        Выбирает оптимальный метод OCR на основе типа PDF и настроек пользователя
        """
        # Если пользователь указал конкретный метод
        if user_method != "auto":
            try:
                return OCRMethod(user_method)
            except ValueError:
                ocr_logger.warning(f"⚠️ Неизвестный метод {user_method}, используем auto")
        
        # Автоматический выбор на основе типа PDF
        if pdf_type == PDFType.VECTOR:
            # Для vector PDF используем PyPDF2 (самый быстрый)
            if self.pypdf2_available:
                ocr_logger.info("🎯 Выбран метод: PYPDF2 (vector PDF)")
                return OCRMethod.PYPDF2
            else:
                # Fallback на OpenRouter
                ocr_logger.info("🎯 Выбран метод: OPENROUTER_AUTO (vector PDF, PyPDF2 недоступен)")
                return OCRMethod.OPENROUTER_AUTO
        
        elif pdf_type == PDFType.RASTER:
            # Для raster PDF выбираем на основе качества
            if quality == "accurate":
                # Самый точный - специализированные модели OpenRouter
                if self.openrouter_service and self.openrouter_service.is_available():
                    ocr_logger.info("🎯 Выбран метод: OPENROUTER_OLMOCR (raster PDF, accurate)")
                    return OCRMethod.OPENROUTER_OLMOCR
                elif self.paddleocr_available:
                    ocr_logger.info("🎯 Выбран метод: PADDLEOCR (raster PDF, accurate, OpenRouter недоступен)")
                    return OCRMethod.PADDLEOCR
                else:
                    ocr_logger.info("🎯 Выбран метод: TESSERACT (raster PDF, accurate, fallback)")
                    return OCRMethod.TESSERACT
            
            elif quality == "fast":
                # Самый быстрый - локальные методы
                if self.paddleocr_available:
                    ocr_logger.info("🎯 Выбран метод: PADDLEOCR (raster PDF, fast)")
                    return OCRMethod.PADDLEOCR
                elif self.tesseract_available:
                    ocr_logger.info("🎯 Выбран метод: TESSERACT (raster PDF, fast)")
                    return OCRMethod.TESSERACT
                else:
                    ocr_logger.info("🎯 Выбран метод: OPENROUTER_AUTO (raster PDF, fast, fallback)")
                    return OCRMethod.OPENROUTER_AUTO
            
            else:  # balanced
                # Сбалансированный - пробуем OpenRouter, затем локальные
                if self.openrouter_service and self.openrouter_service.is_available():
                    ocr_logger.info("🎯 Выбран метод: OPENROUTER_AUTO (raster PDF, balanced)")
                    return OCRMethod.OPENROUTER_AUTO
                elif self.paddleocr_available:
                    ocr_logger.info("🎯 Выбран метод: PADDLEOCR (raster PDF, balanced, OpenRouter недоступен)")
                    return OCRMethod.PADDLEOCR
                else:
                    ocr_logger.info("🎯 Выбран метод: TESSERACT (raster PDF, balanced, fallback)")
                    return OCRMethod.TESSERACT
        
        else:  # MIXED или UNKNOWN
            # Для mixed/unknown используем универсальный подход
            if self.openrouter_service and self.openrouter_service.is_available():
                ocr_logger.info("🎯 Выбран метод: OPENROUTER_AUTO (mixed/unknown PDF)")
                return OCRMethod.OPENROUTER_AUTO
            elif self.paddleocr_available:
                ocr_logger.info("🎯 Выбран метод: PADDLEOCR (mixed/unknown PDF, OpenRouter недоступен)")
                return OCRMethod.PADDLEOCR
            else:
                ocr_logger.info("🎯 Выбран метод: TESSERACT (mixed/unknown PDF, fallback)")
                return OCRMethod.TESSERACT
    
    async def process_with_paddleocr(
        self,
        file_content: bytes,
        file_type: str,
        languages: List[str]
    ) -> Optional[str]:
        """
        Обработка с помощью PaddleOCR (ленивая инициализация)
        """
        # Ленивая инициализация PaddleOCR (только при использовании)
        if self.paddleocr_instance is None:
            try:
                # Пробуем импортировать и инициализировать PaddleOCR
                from paddleocr import PaddleOCR
                # Поддержка русского и английского
                self.paddleocr_instance = PaddleOCR(use_angle_cls=True, lang='en+ru', use_gpu=False)
                self.paddleocr_available = True
                ocr_logger.info("✅ PaddleOCR инициализирован (rus+eng)")
            except (ImportError, OSError, Exception) as e:
                # OSError может возникнуть если OpenCV не может загрузиться (libGL.so.1)
                self.paddleocr_available = False
                self.paddleocr_instance = None
                ocr_logger.warning(f"⚠️ PaddleOCR недоступен: {e}")
                raise ValueError(f"PaddleOCR не доступен: {e}")
        
        if not self.paddleocr_available or not self.paddleocr_instance:
            raise ValueError("PaddleOCR не доступен")
        
        try:
            is_image = file_type.startswith("image/")
            
            if is_image:
                # Обработка изображения
                image = Image.open(io.BytesIO(file_content))
                # Конвертируем в numpy array для PaddleOCR
                import numpy as np
                img_array = np.array(image)
                
                result = self.paddleocr_instance.ocr(img_array, cls=True)
                
                # Извлекаем текст из результата
                text_parts = []
                if result and result[0]:
                    for line in result[0]:
                        if line and len(line) > 1:
                            text_parts.append(line[1][0])  # Текст из кортежа
                
                return "\n".join(text_parts) if text_parts else None
            else:
                # Обработка PDF
                if not self.pdf2image_available:
                    raise ValueError("pdf2image не доступен для обработки PDF")
                
                images = convert_from_bytes(file_content, dpi=400, fmt='png')
                all_text = []
                
                import numpy as np
                for page_num, img in enumerate(images, 1):
                    img_array = np.array(img)
                    result = self.paddleocr_instance.ocr(img_array, cls=True)
                    
                    text_parts = []
                    if result and result[0]:
                        for line in result[0]:
                            if line and len(line) > 1:
                                text_parts.append(line[1][0])
                    
                    if text_parts:
                        all_text.append(f"--- Страница {page_num} ---\n" + "\n".join(text_parts))
                
                return "\n\n".join(all_text) if all_text else None
                
        except Exception as e:
            ocr_logger.error(f"❌ Ошибка PaddleOCR: {e}")
            return None
