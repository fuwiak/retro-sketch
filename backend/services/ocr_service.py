"""
OCR Service using OpenRouter API
Handles PDF and image OCR with OpenRouter vision models
"""

import os
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

from services.openrouter_service import OpenRouterService
from services.logger import ocr_logger, log_ocr_request, log_ocr_result


class OCRService:
    """Service for OCR processing using OpenRouter vision models"""
    
    def __init__(self):
        self.openrouter_service = OpenRouterService()
        self.tesseract_available = TESSERACT_AVAILABLE
        self.pdf2image_available = PDF2IMAGE_AVAILABLE
    
    def is_available(self) -> bool:
        """Check if OCR service is available"""
        return self.openrouter_service.is_available() or self.tesseract_available
    
    def _file_to_base64(self, file_content: bytes) -> str:
        """Convert file content to base64 string"""
        return base64.b64encode(file_content).decode("utf-8")
    
    async def _process_with_tesseract(
        self,
        file_content: bytes,
        file_type: str,
        languages: List[str]
    ) -> str:
        """Process file with Tesseract OCR (fallback only)"""
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
            text = pytesseract.image_to_string(image, lang=tesseract_langs)
            return text
        else:
            # Process PDF - convert to images first
            if not self.pdf2image_available:
                raise ValueError("pdf2image not available for PDF processing")
            
            images = convert_from_bytes(file_content)
            all_text = []
            
            for img in images:
                text = pytesseract.image_to_string(img, lang=tesseract_langs)
                all_text.append(text)
            
            return "\n\n--- Page Break ---\n\n".join(all_text)
    
    async def process_file(
        self,
        file_content: bytes,
        file_type: str,
        languages: List[str] = ["rus", "eng"],
        model: Optional[str] = None,
        temperature: float = 0.0,
        custom_prompt: Optional[str] = None
    ) -> Dict:
        """
        Process file with OCR using OpenRouter vision models
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
        
        ocr_logger.info(f"Starting OCR processing with OpenRouter - File size: {len(file_content) / 1024:.1f}KB")
        
        is_image = file_type.startswith("image/")
        
        # Convert file to base64
        file_b64 = self._file_to_base64(file_content)
        
        try:
            # Use OpenRouter for text extraction
            ocr_logger.info("Using OpenRouter vision model for OCR...")
            
            text = await self.openrouter_service.extract_text_from_image(
                image_base64=file_b64,
                languages=languages,
                model=model
            )
            
            if not text:
                # Fallback to Tesseract if OpenRouter fails
                ocr_logger.warning("OpenRouter failed, falling back to Tesseract...")
                text = await self._process_with_tesseract(file_content, file_type, languages)
                method_used = "tesseract_fallback"
            else:
                method_used = "openrouter"
            
            actual_time = time.time() - start_time
            
            # Determine pages
            if is_image:
                pages = 1
            else:
                # Try to count PDF pages
                try:
                    if self.pdf2image_available:
                        images = convert_from_bytes(file_content)
                        pages = len(images)
                    else:
                        pages = 1
                except:
                    pages = 1
            
            ocr_logger.info(
                f"OCR completed - Method: {method_used}, "
                f"Time: {actual_time:.2f}s, "
                f"Text length: {len(text)} chars, "
                f"Pages: {pages}"
            )
            
            # Log success
            log_ocr_result(
                method=method_used,
                success=True,
                time_taken=actual_time,
                pages=pages
            )
            
            return {
                "text": text,
                "file_type": "image" if is_image else "pdf",
                "pages": pages,
                "metadata": {
                    "languages": languages,
                    "file_type": file_type,
                    "method_used": method_used,
                    "model": model
                },
                "processing_info": {
                    "method": method_used,
                    "actual_time": actual_time,
                    "model": model
                }
            }
        
        except Exception as e:
            ocr_logger.error(f"OCR processing failed: {str(e)}")
            
            # Try Tesseract as last resort
            try:
                ocr_logger.info("Trying Tesseract as last resort...")
                text = await self._process_with_tesseract(file_content, file_type, languages)
                actual_time = time.time() - start_time
                
                pages = 1 if is_image else 1
                
                log_ocr_result(
                    method="tesseract_last_resort",
                    success=True,
                    time_taken=actual_time,
                    pages=pages
                )
                
                return {
                    "text": text,
                    "file_type": "image" if is_image else "pdf",
                    "pages": pages,
                    "metadata": {
                        "languages": languages,
                        "file_type": file_type,
                        "method_used": "tesseract_last_resort"
                    },
                    "processing_info": {
                        "method": "tesseract_last_resort",
                        "actual_time": actual_time
                    }
                }
            except Exception as fallback_error:
                ocr_logger.error(f"All OCR methods failed: {str(fallback_error)}")
                log_ocr_result(
                    method="all_failed",
                    success=False,
                    time_taken=time.time() - start_time,
                    error=f"{str(e)} | {str(fallback_error)}"
                )
                raise Exception(f"OCR processing failed: {str(e)} | {str(fallback_error)}")
    
    async def process_image(
        self,
        image_content: bytes,
        languages: List[str] = ["rus", "eng"],
        model: Optional[str] = None,
        temperature: float = 0.0
    ) -> str:
        """Process image with OCR"""
        result = await self.process_file(
            file_content=image_content,
            file_type="image/png",
            languages=languages,
            model=model,
            temperature=temperature
        )
        return result["text"]
    
    async def process_pdf(
        self,
        pdf_content: bytes,
        languages: List[str] = ["rus", "eng"],
        model: Optional[str] = None,
        temperature: float = 0.0
    ) -> str:
        """Process PDF with OCR"""
        result = await self.process_file(
            file_content=pdf_content,
            file_type="application/pdf",
            languages=languages,
            model=model,
            temperature=temperature
        )
        return result["text"]
