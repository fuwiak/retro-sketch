"""
OCR Service using OpenRouter and OCR fallback methods
Handles PDF and image OCR with OpenRouter first, then OCR fallbacks (PyPDF2, Tesseract)
Groq полностью отключен - используется только OpenRouter + OCR
"""

import base64
from typing import List, Dict, Optional
import io
import time

try:
    import pytesseract
    from PIL import Image
    try:
        from pdf2image import convert_from_bytes
        PDF2IMAGE_AVAILABLE = True
    except ImportError:
        PDF2IMAGE_AVAILABLE = False
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False
    PDF2IMAGE_AVAILABLE = False

from services.logger import ocr_logger, log_ocr_request, log_ocr_result

# OpenRouter будет использоваться через OpenRouterService
# Groq полностью отключен


class OCRService:
    """Service for OCR processing using OpenRouter + OCR fallbacks"""
    
    def __init__(self, openrouter_service=None):
        self.openrouter_service = openrouter_service  # Будет передан из main.py
        self.tesseract_available = TESSERACT_AVAILABLE
        self.pdf2image_available = PDF2IMAGE_AVAILABLE
    
    def is_available(self) -> bool:
        """Check if OCR service is available"""
        return self.tesseract_available or (self.openrouter_service and self.openrouter_service.is_available())
    
    def _file_to_base64(self, file_content: bytes) -> str:
        """Convert file content to base64 string"""
        return base64.b64encode(file_content).decode("utf-8")
    
    async def _process_with_tesseract(
        self,
        file_content: bytes,
        file_type: str,
        languages: List[str]
    ) -> str:
        """
        Process file with Tesseract OCR напрямую (fallback если OpenRouter недоступен)
        Использует preprocessing из OpenRouterService если доступен
        """
        if not self.tesseract_available:
            raise ValueError("Tesseract OCR not available")
        
        is_image = file_type.startswith("image/")
        
        # Map language codes to Tesseract format
        lang_map = {
            "rus": "rus",
            "ru": "rus",
            "russian": "rus",
            "eng": "eng",
            "en": "eng",
            "english": "eng"
        }
        tesseract_langs = "+".join([lang_map.get(lang.lower(), "eng") for lang in languages])
        
        if is_image:
            # Process image directly
            image = Image.open(io.BytesIO(file_content))
            
            # Используем preprocessing если доступен OpenRouterService
            if self.openrouter_service:
                image = self.openrouter_service._preprocess_image_for_ocr(image)
            
            text = pytesseract.image_to_string(image, lang=tesseract_langs, config='--psm 6 --oem 3')
            return text
        else:
            # Process PDF - convert to images first
            if not self.pdf2image_available:
                raise ValueError("pdf2image not available for PDF processing")
            
            # Конвертируем с высоким DPI
            images = convert_from_bytes(file_content, dpi=300, fmt='png')
            all_text = []
            
            for img in images:
                # Используем preprocessing если доступен OpenRouterService
                if self.openrouter_service:
                    img = self.openrouter_service._preprocess_image_for_ocr(img)
                
                text = pytesseract.image_to_string(img, lang=tesseract_langs, config='--psm 6 --oem 3')
                all_text.append(text)
            
            return "\n\n--- Page Break ---\n\n".join(all_text)
    
    async def process_file(
        self,
        file_content: bytes,
        file_type: str,
        languages: List[str] = ["rus", "eng"]
    ) -> Dict:
        """
        Process file with OCR using OpenRouter first, then OCR fallbacks
        Порядок: OpenRouter -> PyPDF2 -> Tesseract OCR
        Returns: {
            "text": str,
            "file_type": str,
            "pages": int,
            "metadata": dict,
            "processing_info": dict
        }
        """
        start_time = time.time()
        
        # Log request
        log_ocr_request(
            file_size=len(file_content),
            file_type=file_type,
            languages=languages
        )
        
        ocr_logger.info(f"Starting OCR processing - File size: {len(file_content) / 1024:.1f}KB")
        
        is_image = file_type.startswith("image/")
        
        # Определяем количество страниц
        pages = 1
        if not is_image:
            try:
                import PyPDF2
                pdf_reader = PyPDF2.PdfReader(io.BytesIO(file_content))
                pages = len(pdf_reader.pages)
            except:
                pass
        
        processing_info = {
            "method": "openrouter",
            "fallback_used": False
        }
        
        ocr_text = None
        
        # ШАГ 1: Пробуем OpenRouter (если доступен)
        if self.openrouter_service and self.openrouter_service.is_available():
            try:
                ocr_logger.info("🎯 Шаг 1: Пробуем извлечь текст через OpenRouter...")
                openrouter_start = time.time()
                
                # Для PDF конвертируем первую страницу в изображение для OpenRouter
                # Для изображений используем напрямую
                if is_image:
                    file_b64 = base64.b64encode(file_content).decode("utf-8")
                else:
                    # Для PDF конвертируем в изображение
                    if PDF2IMAGE_AVAILABLE:
                        try:
                            from pdf2image import convert_from_bytes
                            # Конвертируем первую страницу в изображение
                            images = convert_from_bytes(file_content, dpi=300, first_page=1, last_page=1)
                            if images:
                                # Конвертируем изображение в base64
                                img_buffer = io.BytesIO()
                                images[0].save(img_buffer, format='PNG')
                                img_buffer.seek(0)
                                file_b64 = base64.b64encode(img_buffer.getvalue()).decode("utf-8")
                                ocr_logger.info("   PDF конвертирован в изображение для OpenRouter")
                            else:
                                raise Exception("Не удалось конвертировать PDF в изображение")
                        except Exception as e:
                            ocr_logger.warning(f"   Не удалось конвертировать PDF: {e}, пропускаем OpenRouter")
                            file_b64 = None
                    else:
                        ocr_logger.warning("   pdf2image недоступен, пропускаем OpenRouter для PDF")
                        file_b64 = None
                
                if file_b64:
                    # Пробуем извлечь текст через OpenRouter
                    ocr_text = await self.openrouter_service.extract_text_from_image(
                        image_base64=file_b64,
                        languages=languages,
                        model=None  # Использует приоритетные модели
                    )
                    
                    openrouter_time = time.time() - openrouter_start
                    
                    if ocr_text and len(ocr_text.strip()) > 0:
                        processing_info["method"] = "openrouter"
                        processing_info["openrouter_time"] = openrouter_time
                        ocr_logger.info(f"✅ OpenRouter успешно извлек текст: {len(ocr_text)} символов за {openrouter_time:.2f}s")
                    else:
                        ocr_logger.warning("⚠️ OpenRouter вернул пустой результат, пробуем OCR fallback'и...")
                        ocr_text = None
                else:
                    ocr_text = None
                    
            except Exception as e:
                ocr_logger.warning(f"⚠️ OpenRouter не сработал: {e}, пробуем OCR fallback'и...")
                ocr_text = None
        
        # ШАГ 2: Если OpenRouter не сработал, используем OCR fallback'и
        if not ocr_text or len(ocr_text.strip()) == 0:
            processing_info["fallback_used"] = True
            ocr_logger.info("🔄 Шаг 2: Используем OCR fallback'и (PyPDF2, Tesseract)...")
            
            # Используем метод из OpenRouterService для OCR fallback
            if self.openrouter_service:
                try:
                    file_b64 = base64.b64encode(file_content).decode("utf-8")
                    ocr_text = await self.openrouter_service._extract_text_with_ocr_fallback(
                        image_base64=file_b64,
                        languages=languages
                    )
                    
                    if ocr_text and len(ocr_text.strip()) > 0:
                        processing_info["method"] = "ocr_fallback"
                        ocr_logger.info(f"✅ OCR fallback успешно извлек текст: {len(ocr_text)} символов")
                except Exception as e:
                    ocr_logger.error(f"❌ OCR fallback не сработал: {e}")
                    ocr_text = None
            else:
                # Прямой вызов Tesseract если OpenRouter service недоступен
                if self.tesseract_available:
                    try:
                        ocr_logger.info("Используем Tesseract OCR напрямую...")
                        ocr_text = await self._process_with_tesseract(file_content, file_type, languages)
                        if ocr_text:
                            processing_info["method"] = "tesseract_direct"
                    except Exception as e:
                        ocr_logger.error(f"Tesseract OCR не сработал: {e}")
                        ocr_text = None
        
        # Проверяем результат
        if not ocr_text or len(ocr_text.strip()) == 0:
            ocr_logger.error("❌ Все методы не смогли извлечь текст!")
            raise Exception("OCR processing failed: все методы (OpenRouter, PyPDF2, Tesseract) не смогли извлечь текст")
            
            actual_time = time.time() - start_time
            processing_info["actual_time"] = actual_time
            
            ocr_logger.info(
                f"OCR completed - Method: {processing_info['method']}, "
                f"Time: {actual_time:.2f}s, "
                f"Text length: {len(ocr_text)} chars, "
                f"Pages: {pages}"
            )
            
            # Log success
            log_ocr_result(
                method=processing_info["method"],
                success=True,
                time_taken=actual_time,
                pages=pages
            )
            
            return {
                "text": ocr_text,
                "file_type": "image" if is_image else "pdf",
                "pages": pages,
                "metadata": {
                    "languages": languages,
                    "file_type": file_type,
                    "method_used": processing_info["method"]
                },
                "processing_info": processing_info
            }
    
    async def process_image(
        self,
        image_content: bytes,
        languages: List[str] = ["rus", "eng"]
    ) -> str:
        """Process image with OCR"""
        result = await self.process_file(
            file_content=image_content,
            file_type="image/png",
            languages=languages
        )
        return result["text"]
    
    async def process_pdf(
        self,
        pdf_content: bytes,
        languages: List[str] = ["rus", "eng"]
    ) -> str:
        """Process PDF with OCR"""
        result = await self.process_file(
            file_content=pdf_content,
            file_type="application/pdf",
            languages=languages
        )
        return result["text"]

