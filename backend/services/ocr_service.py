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
    from pdf2image import convert_from_bytes
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False

from services.logger import ocr_logger, log_ocr_request, log_ocr_result
from services.ocr_agent import OCRSelectionAgent, PDFType, OCRMethod, OCRQuality, TextType

# OpenRouter будет использоваться через OpenRouterService
# Groq полностью отключен


class OCRService:
    """Service for OCR processing using OpenRouter + OCR fallbacks"""
    
    def __init__(self, openrouter_service=None):
        self.openrouter_service = openrouter_service  # Будет передан из main.py
        self.tesseract_available = TESSERACT_AVAILABLE
        self.pdf2image_available = PDF2IMAGE_AVAILABLE
        self.agent = OCRSelectionAgent(openrouter_service=openrouter_service)  # AI агент для выбора метода
    
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
            
            # Пробуем разные PSM режимы для технических чертежей
            # PSM 11 - разреженный текст (хорошо для чертежей)
            # PSM 6 - единый блок текста
            # PSM 4 - одна колонка текста
            text = ""
            for psm_mode in [11, 6, 4]:
                try:
                    text = pytesseract.image_to_string(
                        image, 
                        lang=tesseract_langs, 
                        config=f'--psm {psm_mode} --oem 3'
                    )
                    if text and len(text.strip()) > 10:
                        ocr_logger.info(f"   ✅ Tesseract успешно извлек текст с PSM {psm_mode}")
                        break
                except:
                    continue
            
            return text if text else pytesseract.image_to_string(image, lang=tesseract_langs, config='--psm 6 --oem 3')
        else:
            # Process PDF - convert to images first
            if not self.pdf2image_available:
                raise ValueError("pdf2image not available for PDF processing")
            
            # АДАПТИВНАЯ ОБРАБОТКА PDF: пробуем разные комбинации параметров (DPI, контраст, preprocessing)
            # Агент автоматически подбирает оптимальные параметры для максимального качества OCR
            
            # Стратегии обработки от простых к сложным
            strategies = [
                {"dpi": 300, "preprocess": True, "contrast": 1.5, "psm": [6]},
                {"dpi": 400, "preprocess": True, "contrast": 2.0, "psm": [11, 6, 4]},
                {"dpi": 500, "preprocess": True, "contrast": 2.5, "psm": [11, 6, 4, 3]},
                {"dpi": 600, "preprocess": True, "contrast": 3.0, "psm": [11, 6]},
            ]
            
            for strategy_idx, strategy in enumerate(strategies, 1):
                try:
                    ocr_logger.info(f"🔄 Стратегия {strategy_idx}/{len(strategies)}: DPI={strategy['dpi']}, контраст={strategy['contrast']}, PSM={strategy['psm']}")
                    
                    # Конвертируем PDF в изображения с заданным DPI
                    images = convert_from_bytes(file_content, dpi=strategy['dpi'], fmt='png')
                    all_text = []
                    
                    for page_num, img in enumerate(images, 1):
                        page_text = None
                        
                        # Preprocessing с настраиваемым контрастом
                        if strategy['preprocess'] and self.openrouter_service:
                            processed_img = self._enhance_image_for_ocr(img, contrast=strategy['contrast'])
                        else:
                            processed_img = img
                        
                        # Пробуем разные PSM режимы
                        for psm_mode in strategy['psm']:
                            try:
                                text = pytesseract.image_to_string(
                                    processed_img,
                                    lang=tesseract_langs,
                                    config=f'--psm {psm_mode} --oem 3'
                                )
                                
                                if text and len(text.strip()) > 10:
                                    page_text = text
                                    ocr_logger.info(f"   ✅ Страница {page_num}: извлечено {len(text)} символов (PSM {psm_mode})")
                                    break
                            except Exception as e:
                                ocr_logger.debug(f"   ⚠️ PSM {psm_mode} не сработал: {e}")
                                continue
                        
                        if page_text:
                            all_text.append(page_text)
                        else:
                            # Fallback: пробуем без preprocessing
                            try:
                                text = pytesseract.image_to_string(img, lang=tesseract_langs, config='--psm 6 --oem 3')
                                if text and len(text.strip()) > 0:
                                    all_text.append(text)
                            except:
                                pass
                    
                    # Если получили текст хотя бы с одной страницы, возвращаем результат
                    if all_text:
                        result = "\n\n--- Page Break ---\n\n".join(all_text)
                        ocr_logger.info(f"✅ Адаптивная обработка успешна (стратегия {strategy_idx}): извлечено {len(result)} символов со {len(all_text)} страниц")
                        return result
                    else:
                        ocr_logger.warning(f"⚠️ Стратегия {strategy_idx} не дала результата, пробуем следующую...")
                        
                except Exception as e:
                    ocr_logger.warning(f"⚠️ Ошибка в стратегии {strategy_idx}: {e}, пробуем следующую...")
                    continue
            
            # Если все стратегии не сработали, возвращаем пустой результат
            ocr_logger.error("❌ Все стратегии адаптивной обработки не дали результата")
            return ""
    
    def _enhance_image_for_ocr(self, image: Image.Image, contrast: float = 2.0) -> Image.Image:
        """
        Улучшение изображения для OCR с настраиваемыми параметрами (контраст, резкость, яркость)
        """
        try:
            # Конвертируем в RGB если нужно
            if image.mode != 'RGB':
                image = image.convert('RGB')
            
            # Увеличиваем контраст (настраиваемый параметр)
            enhancer = ImageEnhance.Contrast(image)
            image = enhancer.enhance(contrast)
            
            # Улучшаем резкость
            enhancer = ImageEnhance.Sharpness(image)
            image = enhancer.enhance(1.5)
            
            # Коррекция яркости
            enhancer = ImageEnhance.Brightness(image)
            pixels = list(image.getdata())
            if pixels:
                avg_brightness = sum(sum(pixel) / 3 for pixel in pixels) / len(pixels)
                if avg_brightness < 128:
                    image = enhancer.enhance(1.2)  # Осветляем
                elif avg_brightness > 200:
                    image = enhancer.enhance(0.9)  # Затемняем
            
            # Применяем фильтр для уменьшения шума
            image = image.filter(ImageFilter.MedianFilter(size=3))
            
            return image
        except Exception as e:
            ocr_logger.warning(f"⚠️ Ошибка улучшения изображения: {e}, используем оригинал")
            return image
    async def process_file(
        self,
        file_content: bytes,
        file_type: str,
        languages: List[str] = ["rus"],
        ocr_method: str = "auto",
        ocr_quality: str = "balanced"
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
        pdf_type = None
        text_type = None
        
        # Определяем тип текста (печатный/рукописный) - ОТКЛЮЧЕНО для ускорения
        # Это занимает дополнительное время (3+ минуты), поэтому пропускаем
        # По умолчанию считаем печатным для ускорения обработки
        text_type = TextType.PRINTED
        
        if not is_image:
            try:
                import PyPDF2
                pdf_reader = PyPDF2.PdfReader(io.BytesIO(file_content))
                pages = len(pdf_reader.pages)
                
                # Определяем тип PDF через AI агента
                ocr_logger.info("🔍 Определяем тип PDF...")
                pdf_type = await self.agent.detect_pdf_type(file_content)
                ocr_logger.info(f"📄 Тип PDF: {pdf_type.value}")
            except:
                pass
        
        # Для изображений используем более быстрые методы по умолчанию
        if is_image:
            # Для изображений приоритет - быстрые локальные методы или быстрые модели
            if ocr_method == "auto":
                # Для изображений автоматически выбираем быстрый метод
                if ocr_quality == "fast":
                    selected_method = OCRMethod.TESSERACT
                elif ocr_quality == "accurate":
                    selected_method = OCRMethod.OPENROUTER_AUTO
                else:  # balanced
                    # Сначала пробуем Tesseract (быстро), если не сработает - OpenRouter
                    selected_method = OCRMethod.TESSERACT
            else:
                # Используем выбранный пользователем метод
                try:
                    selected_method = OCRMethod(ocr_method)
                except ValueError:
                    selected_method = OCRMethod.TESSERACT
            ocr_logger.info(f"🖼️ Для изображения выбран метод: {selected_method.value} (quality: {ocr_quality})")
        else:
            # Для PDF используем стандартную логику
            selected_method = self.agent.select_ocr_method(
                pdf_type=pdf_type if pdf_type else PDFType.RASTER,
                user_method=ocr_method,
                quality=ocr_quality
            )
            ocr_logger.info(f"🎯 Выбранный метод OCR: {selected_method.value}")
        
        processing_info = {
            "method": selected_method.value,
            "pdf_type": pdf_type.value if pdf_type else "image",
            "fallback_used": False
        }
        
        ocr_text = None
        
        # Обрабатываем в зависимости от выбранного метода
        if selected_method == OCRMethod.PYPDF2:
            # Для vector PDF используем PyPDF2
            if not is_image and PYPDF2_AVAILABLE:
                try:
                    import PyPDF2
                    ocr_logger.info("📄 Используем PyPDF2 для vector PDF...")
                    pdf_reader = PyPDF2.PdfReader(io.BytesIO(file_content))
                    text_parts = []
                    for page_num, page in enumerate(pdf_reader.pages, 1):
                        try:
                            page_text = page.extract_text()
                            if page_text.strip():
                                text_parts.append(f"--- Страница {page_num} ---\n{page_text}")
                        except:
                            pass
                    if text_parts:
                        ocr_text = "\n\n".join(text_parts)
                        ocr_logger.info(f"✅ PyPDF2 извлек текст: {len(ocr_text)} символов")
                except Exception as e:
                    ocr_logger.error(f"❌ PyPDF2 не сработал: {e}")
        
        elif selected_method == OCRMethod.PADDLEOCR:
            # Используем PaddleOCR
            try:
                ocr_logger.info("🚀 Используем PaddleOCR...")
                ocr_text = await self.agent.process_with_paddleocr(file_content, file_type, languages)
                if ocr_text:
                    ocr_logger.info(f"✅ PaddleOCR извлек текст: {len(ocr_text)} символов")
            except Exception as e:
                ocr_logger.error(f"❌ PaddleOCR не сработал: {e}")
        
        elif selected_method == OCRMethod.TESSERACT:
            # Используем Tesseract
            try:
                ocr_logger.info("🔧 Используем Tesseract OCR...")
                ocr_text = await self._process_with_tesseract(file_content, file_type, languages)
                if ocr_text:
                    ocr_logger.info(f"✅ Tesseract извлек текст: {len(ocr_text)} символов")
            except Exception as e:
                ocr_logger.error(f"❌ Tesseract не сработал: {e}")
        
        # Для изображений: если локальный метод не сработал, пробуем OpenRouter как fallback
        if is_image and (not ocr_text or len(ocr_text.strip()) <= 10):
            ocr_logger.info("🔄 Локальный OCR не дал результата, пробуем OpenRouter для изображения...")
            if self.openrouter_service and self.openrouter_service.is_available():
                try:
                    ocr_logger.info("🎯 Извлечение текста из изображения через OpenRouter...")
                    openrouter_start = time.time()
                    file_b64 = base64.b64encode(file_content).decode("utf-8")
                    
                    # Используем быструю модель для изображений (только одну, без всех fallback для ускорения)
                    ocr_logger.info("   Используем быструю модель qwen/qwen2.5-vl-32b-instruct для изображения")
                    ocr_text = await self.openrouter_service.extract_text_from_image(
                        image_base64=file_b64,
                        languages=languages,
                        model="qwen/qwen2.5-vl-32b-instruct"  # Быстрая модель для изображений, без fallback
                    )
                    
                    openrouter_time = time.time() - openrouter_start
                    if ocr_text and len(ocr_text.strip()) > 10:
                        processing_info["method"] = "openrouter_fallback"
                        processing_info["openrouter_time"] = openrouter_time
                        ocr_logger.info(f"✅ OpenRouter успешно извлек текст из изображения: {len(ocr_text)} символов за {openrouter_time:.2f}s")
                except Exception as e:
                    ocr_logger.warning(f"⚠️ OpenRouter не сработал для изображения: {e}")
        
        # Для TESSERACT метода - обрабатываем напрямую без OpenRouter (быстро)
        if not ocr_text and selected_method == OCRMethod.TESSERACT:
            if self.tesseract_available:
                try:
                    ocr_logger.info("⚡ Используем Tesseract OCR напрямую (быстро)...")
                    ocr_text = await self._process_with_tesseract(file_content, file_type, languages)
                    if ocr_text and len(ocr_text.strip()) > 0:
                        processing_info["method"] = "tesseract"
                        ocr_logger.info(f"✅ Tesseract успешно извлек текст: {len(ocr_text)} символов")
                except Exception as e:
                    ocr_logger.warning(f"⚠️ Tesseract не сработал: {e}, пробуем другие методы...")
                    ocr_text = None
        
        # Для OpenRouter методов (только для PDF или если явно выбран OpenRouter)
        if not ocr_text and not is_image and selected_method in [OCRMethod.OPENROUTER_OLMOCR, OCRMethod.OPENROUTER_GOTOCR, OCRMethod.OPENROUTER_MISTRAL, OCRMethod.OPENROUTER_AUTO]:
            # ШАГ 1: Пробуем OpenRouter (если доступен)
            if self.openrouter_service and self.openrouter_service.is_available():
                try:
                    ocr_logger.info("🎯 Шаг 1: Пробуем извлечь текст через OpenRouter...")
                    openrouter_start = time.time()
                    
                    # Для PDF конвертируем первую страницу в изображение для OpenRouter
                    # (Изображения уже обработаны выше, здесь только PDF)
                    if PDF2IMAGE_AVAILABLE:
                        try:
                            from pdf2image import convert_from_bytes
                            # Конвертируем первую страницу в изображение с высоким DPI для лучшего OCR
                            images = convert_from_bytes(file_content, dpi=400, first_page=1, last_page=1)
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
                        # Выбираем конкретную модель в зависимости от метода
                        model_to_use = None
                        if selected_method == OCRMethod.OPENROUTER_OLMOCR:
                            model_to_use = "qwen/qwen2.5-vl-72b-instruct"  # Заменено на проверенную модель
                        elif selected_method == OCRMethod.OPENROUTER_GOTOCR:
                            model_to_use = "qwen/qwen2.5-vl-32b-instruct"  # Заменено на проверенную модель
                        elif selected_method == OCRMethod.OPENROUTER_MISTRAL:
                            model_to_use = "internvl/internvl2-26b"  # Заменено на проверенную модель
                        # Для OPENROUTER_AUTO используем None (автоматический выбор)
                        
                        # Пробуем извлечь текст через OpenRouter
                        ocr_text = await self.openrouter_service.extract_text_from_image(
                            image_base64=file_b64,
                            languages=languages,
                            model=model_to_use
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
        actual_time = time.time() - start_time
        processing_info["actual_time"] = actual_time
        
        if not ocr_text or len(ocr_text.strip()) == 0:
            ocr_logger.error("❌ Все методы не смогли извлечь текст!")
            ocr_logger.error(f"   Метод: {selected_method.value}, Тип PDF: {pdf_type.value if pdf_type else 'unknown'}")
            ocr_logger.error(f"   OpenRouter доступен: {self.openrouter_service and self.openrouter_service.is_available()}")
            ocr_logger.error(f"   Tesseract доступен: {self.tesseract_available}")
            ocr_logger.error(f"   PDF2Image доступен: {self.pdf2image_available}")
            raise Exception("OCR processing failed: все методы (OpenRouter, PyPDF2, Tesseract с адаптивными параметрами) не смогли извлечь текст")
            
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
                "method_used": processing_info["method"],
                "text_type": text_type.value if text_type else "unknown"
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

