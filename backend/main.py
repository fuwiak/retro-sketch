"""
FastAPI Backend for Retro Drawing Analyzer
Handles OCR, translation, and document export
"""

from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional, List, Dict
import os
import time
from pathlib import Path
from dotenv import load_dotenv

from services.ocr_service import OCRService
from services.translation_service import TranslationService
from services.export_service import ExportService
from services.cloud_service import CloudService
from services.telegram_service import TelegramService
from services.openrouter_service import OpenRouterService
from services.logger import api_logger, log_api_request, log_api_response

# Load environment variables
load_dotenv()

app = FastAPI(
    title="Retro Drawing Analyzer",
    description="PDF drawing analysis with OCR, translation, and export",
    version="1.0.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify exact origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize services
openrouter_service = OpenRouterService()
ocr_service = OCRService(openrouter_service=openrouter_service)
translation_service = TranslationService()
export_service = ExportService()
cloud_service = CloudService()
telegram_service = TelegramService()

# Frontend static files configuration
# Check if frontend dist directory exists (for Railway deployment)
FRONTEND_DIR = Path(__file__).parent / "static"
if not FRONTEND_DIR.exists():
    # Try alternative path (if build is in root dist)
    FRONTEND_DIR = Path(__file__).parent.parent / "dist"

# Mount static assets if frontend exists
if FRONTEND_DIR.exists():
    assets_dir = FRONTEND_DIR / "assets"
    if assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")
    api_logger.info(f"Frontend found at {FRONTEND_DIR}")
else:
    api_logger.warning("Frontend not found - serving API only")

@app.get("/api/health")
async def health():
    """Health check with service status"""
    return {
        "status": "ok",
        "services": {
            "ocr": ocr_service.is_available(),
            "translation": translation_service.is_available(),
            "export": export_service.is_available(),
            "openrouter": openrouter_service.is_available()
        }
    }


# ========== OCR ENDPOINTS ==========

@app.post("/api/ocr/process")
async def process_ocr(
    request: Request,
    file: UploadFile = File(...),
    languages: str = Form("rus"),
    ocr_method: str = Form("auto"),
    ocr_quality: str = Form("balanced")
):
    """
    Process PDF or image file with OCR using OpenRouter first, then OCR fallbacks.
    Порядок: OpenRouter (специализированные OCR модели) -> PyPDF2 -> Tesseract OCR
    Groq полностью отключен - используется только OpenRouter + OCR fallback'и
    Supports multiple languages (rus+eng, eng, rus, etc.)
    """
    start_time = time.time()
    client_ip = request.client.host if request.client else None
    
    try:
        # Log API request
        log_api_request("POST", "/api/ocr/process", client_ip)
        api_logger.info(f"OCR request received - File: {file.filename}, Languages: {languages}")
        
        # Parse languages
        lang_list = languages.split("+") if "+" in languages else [languages]
        
        # Read file content
        file_content = await file.read()
        file_type = file.content_type
        
        api_logger.info(f"File read - Size: {len(file_content) / 1024:.1f}KB, Type: {file_type}")
        
        # Process with OCR (agent automatically selects best method)
        result = await ocr_service.process_file(
            file_content=file_content,
            file_type=file_type,
            languages=lang_list,
            ocr_method=ocr_method,
            ocr_quality=ocr_quality
        )
        
        # Проверяем, что результат валиден и содержит текст
        if not result:
            raise HTTPException(
                status_code=500,
                detail="OCR processing failed: service returned empty result. Используется только OpenRouter + OCR fallback'и."
            )
        
        # Проверяем, что текст не пустой
        result_text = result.get("text", "") if isinstance(result, dict) else ""
        if not result_text or len(result_text.strip()) == 0:
            raise HTTPException(
                status_code=500,
                detail="OCR processing failed: service returned empty result. Используется только OpenRouter + OCR fallback'и."
            )
        
        # Extract processing info (безопасный доступ)
        processing_info = {}
        if isinstance(result, dict):
            processing_info = result.get("processing_info", {}) or {}
        
        response_time = time.time() - start_time
        log_api_response("POST", "/api/ocr/process", 200, response_time)
        
        method_used = processing_info.get('method', 'unknown') if isinstance(processing_info, dict) else 'unknown'
        text_length = len(result.get('text', '')) if isinstance(result, dict) else 0
        api_logger.info(
            f"OCR request completed - Method: {method_used}, "
            f"Time: {response_time:.2f}s, Text length: {text_length} chars"
        )
        
        # Безопасное извлечение данных из результата
        result_text = result.get("text", "") if isinstance(result, dict) else ""
        result_file_type = result.get("file_type", "unknown") if isinstance(result, dict) else "unknown"
        result_pages = result.get("pages", 1) if isinstance(result, dict) else 1
        result_metadata = result.get("metadata", {}) if isinstance(result, dict) else {}
        
        return {
            "success": True,
            "text": result_text,
            "file_type": result_file_type,
            "pages": result_pages,
            "metadata": result_metadata,
            "processing_info": {
                "method_used": processing_info.get("method", "unknown") if isinstance(processing_info, dict) else "unknown",
                "estimated_time": processing_info.get("estimated_time", 0) if isinstance(processing_info, dict) else 0,
                "actual_time": processing_info.get("actual_time", 0) if isinstance(processing_info, dict) else 0,
                "reasoning": processing_info.get("reasoning", "") if isinstance(processing_info, dict) else "",
                "file_stats": processing_info.get("file_stats", {}) if isinstance(processing_info, dict) else {}
            }
        }
    
    except Exception as e:
        response_time = time.time() - start_time
        log_api_response("POST", "/api/ocr/process", 500, response_time)
        api_logger.error(f"OCR request failed - Error: {str(e)}", exc_info=True)
        
        raise HTTPException(
            status_code=500,
            detail=f"OCR processing failed: {str(e)}"
        )


# ========== OPENROUTER SKETCH ANALYSIS ENDPOINTS ==========

class SketchAnalysisRequest(BaseModel):
    image: str  # Base64 encoded image
    model: Optional[str] = None  # Optional: specific OpenRouter model
    temperature: float = 0.0
    max_tokens: int = 2000


@app.post("/api/openrouter/analyze-sketch")
async def analyze_sketch(request: SketchAnalysisRequest):
    """
    Analyze technical drawing/sketch using OpenRouter vision models
    Extracts: materials, GOST/OST/TU standards, Ra values, fits, heat treatment, and raw text
    """
    start_time = time.time()
    log_api_request("POST", "/api/openrouter/analyze-sketch", {})
    
    try:
        if not openrouter_service.is_available():
            raise HTTPException(
                status_code=503,
                detail="OpenRouter API key not configured. Please set OPENROUTER_API_KEY in environment variables."
            )
        
        api_logger.info("Starting sketch analysis with OpenRouter")
        
        # Analyze sketch with OpenRouter vision models
        result = await openrouter_service.analyze_sketch_with_vision(
            image_base64=request.image,
            model=request.model,
            temperature=request.temperature,
            max_tokens=request.max_tokens
        )
        
        if not result:
            raise HTTPException(
                status_code=503,
                detail="Failed to analyze sketch. All OpenRouter models failed. Check API key and internet connection."
            )
        
        response_time = time.time() - start_time
        log_api_response("POST", "/api/openrouter/analyze-sketch", 200, response_time)
        
        api_logger.info(
            f"Sketch analysis completed - Model: {result.get('model')}, "
            f"Time: {response_time:.2f}s"
        )
        
        return {
            "success": True,
            "data": result.get("data", {}),
            "model": result.get("model"),
            "provider": result.get("provider"),
            "processing_time": response_time
        }
    
    except HTTPException:
        raise
    except Exception as e:
        response_time = time.time() - start_time
        log_api_response("POST", "/api/openrouter/analyze-sketch", 500, response_time)
        api_logger.error(f"Sketch analysis failed - Error: {str(e)}", exc_info=True)
        
        raise HTTPException(
            status_code=500,
            detail=f"Sketch analysis failed: {str(e)}"
        )


class TextExtractionRequest(BaseModel):
    image: str  # Base64 encoded image
    languages: List[str] = ["rus", "eng"]
    model: Optional[str] = None


class QuestionRequest(BaseModel):
    question: str
    file_content: Optional[str] = None  # Base64 encoded file (PDF/image)
    extracted_text: Optional[str] = None  # Already extracted text from OCR
    file_type: Optional[str] = None


@app.post("/api/openrouter/ask-question")
async def ask_question_about_file(request: QuestionRequest):
    """
    Задать вопрос о файле через AI модель
    Использует OpenRouter для ответа на вопросы о техническом чертеже
    """
    start_time = time.time()
    log_api_request("POST", "/api/openrouter/ask-question", {})
    
    try:
        if not openrouter_service.is_available():
            raise HTTPException(
                status_code=503,
                detail="OpenRouter API key not configured. Please set OPENROUTER_API_KEY in environment variables."
            )
        
        api_logger.info(f"Question received: {request.question[:100]}...")
        
        # Формируем промпт с контекстом
        if request.extracted_text:
            context = f"""Текст, извлеченный из технического чертежа:
{request.extracted_text[:3000]}

"""
        else:
            context = ""
        
        prompt = f"""{context}Вопрос о техническом чертеже: {request.question}

Ответь подробно и точно, используя информацию из чертежа."""
        
        # Используем текстовую модель для ответа на вопрос
        result_text = await openrouter_service.ask_question(prompt)
        
        if not result_text:
            raise HTTPException(
                status_code=500,
                detail="Failed to get answer from AI model"
            )
        
        response_time = time.time() - start_time
        log_api_response("POST", "/api/openrouter/ask-question", 200, response_time)
        
        return {
            "success": True,
            "answer": result_text,
            "processing_time": response_time
        }
    
    except HTTPException:
        raise
    except Exception as e:
        response_time = time.time() - start_time
        log_api_response("POST", "/api/openrouter/ask-question", 500, response_time)
        api_logger.error(f"Question failed - Error: {str(e)}", exc_info=True)
        
        raise HTTPException(
            status_code=500,
            detail=f"Question failed: {str(e)}"
        )


@app.post("/api/openrouter/extract-text")
async def extract_text_from_sketch(request: TextExtractionRequest):
    """
    Extract text from sketch/drawing using OpenRouter vision models
    Supports Russian and English text extraction
    """
    start_time = time.time()
    log_api_request("POST", "/api/openrouter/extract-text", {"languages": request.languages})
    
    try:
        if not openrouter_service.is_available():
            raise HTTPException(
                status_code=503,
                detail="OpenRouter API key not configured. Please set OPENROUTER_API_KEY in environment variables."
            )
        
        api_logger.info(f"Extracting text from sketch - Languages: {request.languages}")
        
        # Extract text with OpenRouter vision models
        text = await openrouter_service.extract_text_from_image(
            image_base64=request.image,
            languages=request.languages,
            model=request.model
        )
        
        if not text:
            raise HTTPException(
                status_code=503,
                detail="Failed to extract text. All OpenRouter models failed. Check API key and internet connection."
            )
        
        response_time = time.time() - start_time
        log_api_response("POST", "/api/openrouter/extract-text", 200, response_time)
        
        api_logger.info(
            f"Text extraction completed - "
            f"Time: {response_time:.2f}s, Text length: {len(text)} chars"
        )
        
        return {
            "success": True,
            "text": text,
            "languages": request.languages,
            "processing_time": response_time
        }
    
    except HTTPException:
        raise
    except Exception as e:
        response_time = time.time() - start_time
        log_api_response("POST", "/api/openrouter/extract-text", 500, response_time)
        api_logger.error(f"Text extraction failed - Error: {str(e)}", exc_info=True)
        
        raise HTTPException(
            status_code=500,
            detail=f"Text extraction failed: {str(e)}"
        )


class StructuredDataExtractionRequest(BaseModel):
    ocr_text: str  # OCR текст для извлечения структурированных данных


@app.post("/api/openrouter/extract-structured-data")
async def extract_structured_data_from_text(request: StructuredDataExtractionRequest):
    """
    Извлечение структурированных данных из OCR текста через OpenRouter
    Использует те же методы, что и чат (Claude 3.5 Sonnet)
    """
    start_time = time.time()
    log_api_request("POST", "/api/openrouter/extract-structured-data", {})
    
    try:
        if not openrouter_service.is_available():
            raise HTTPException(
                status_code=503,
                detail="OpenRouter API key not configured. Please set OPENROUTER_API_KEY in environment variables."
            )
        
        api_logger.info(f"Extracting structured data from OCR text ({len(request.ocr_text)} chars)")
        
        # Извлекаем структурированные данные через OpenRouter
        structured_data = await openrouter_service.extract_structured_data(request.ocr_text)
        
        if not structured_data:
            raise HTTPException(
                status_code=500,
                detail="Failed to extract structured data from text"
            )
        
        response_time = time.time() - start_time
        log_api_response("POST", "/api/openrouter/extract-structured-data", 200, response_time)
        
        return {
            "success": True,
            "data": structured_data,
            "processing_time": response_time
        }
    
    except HTTPException:
        raise
    except Exception as e:
        response_time = time.time() - start_time
        log_api_response("POST", "/api/openrouter/extract-structured-data", 500, response_time)
        api_logger.error(f"Structured data extraction failed - Error: {str(e)}", exc_info=True)
        
        raise HTTPException(
            status_code=500,
            detail=f"Structured data extraction failed: {str(e)}"
        )


class SketchAnalysisCompleteRequest(BaseModel):
    """Комплексный запрос: анализ чертежа + извлечение текста + перевод"""
    image: str  # Base64 encoded image
    languages: List[str] = ["rus", "eng"]  # Языки для извлечения текста
    vision_model: Optional[str] = None  # Модель для анализа/извлечения текста
    text_model: Optional[str] = None  # Модель для перевода
    auto_translate: bool = True  # Автоматически переводить текст
    target_language: str = "en"  # Язык перевода
    use_glossary: bool = True  # Использовать технический глоссарий


class OpenRouterTranslationRequest(BaseModel):
    text: str
    target_language: str = "en"  # "en" for English, "ru" for Russian
    model: Optional[str] = None
    use_glossary: bool = True


@app.post("/api/openrouter/analyze-complete")
async def analyze_sketch_complete(request: SketchAnalysisCompleteRequest):
    """
    Комплексный анализ чертежа: извлечение текста + перевод
    Использует OpenRouter vision модели для извлечения текста (rus/eng)
    и text модели для перевода на целевой язык
    """
    start_time = time.time()
    log_api_request("POST", "/api/openrouter/analyze-complete", {
        "languages": request.languages,
        "auto_translate": request.auto_translate,
        "target_language": request.target_language
    })
    
    try:
        if not openrouter_service.is_available():
            raise HTTPException(
                status_code=503,
                detail="OpenRouter API key not configured. Please set OPENROUTER_API_KEY in environment variables."
            )
        
        api_logger.info("="*80)
        api_logger.info("🔬 Starting complete sketch analysis")
        api_logger.info(f"   Languages: {request.languages}")
        api_logger.info(f"   Auto-translate: {request.auto_translate}")
        api_logger.info(f"   Target language: {request.target_language}")
        api_logger.info("="*80)
        
        # Step 1: Extract text from sketch
        api_logger.info("📝 Step 1: Extracting text from sketch...")
        extraction_start = time.time()
        
        extracted_text = await openrouter_service.extract_text_from_image(
            image_base64=request.image,
            languages=request.languages,
            model=request.vision_model
        )
        
        extraction_time = time.time() - extraction_start
        
        if not extracted_text:
            api_logger.error("❌ Text extraction failed - all models failed")
            raise HTTPException(
                status_code=503,
                detail="Failed to extract text. All OpenRouter models failed. Check API key and internet connection."
            )
        
        api_logger.info(f"✅ Text extracted - Time: {extraction_time:.2f}s, Length: {len(extracted_text)} chars")
        api_logger.info(f"   Text preview: {extracted_text[:200]}...")
        
        # Step 2: Translate if requested
        translated_text = None
        translation_time = 0
        
        if request.auto_translate and extracted_text:
            api_logger.info(f"🌐 Step 2: Translating text to {request.target_language}...")
            translation_start = time.time()
            
            translated_text = await openrouter_service.translate_text(
                text=extracted_text,
                target_language=request.target_language,
                model=request.text_model,
                use_glossary=request.use_glossary
            )
            
            translation_time = time.time() - translation_start
            
            if translated_text:
                api_logger.info(f"✅ Translation completed - Time: {translation_time:.2f}s, Length: {len(translated_text)} chars")
                api_logger.info(f"   Translation preview: {translated_text[:200]}...")
            else:
                api_logger.warning("⚠️ Translation failed - all models failed, returning only extracted text")
        
        response_time = time.time() - start_time
        log_api_response("POST", "/api/openrouter/analyze-complete", 200, response_time)
        
        api_logger.info("="*80)
        api_logger.info(f"✅ Complete analysis finished - Total time: {response_time:.2f}s")
        api_logger.info(f"   Extraction: {extraction_time:.2f}s")
        if request.auto_translate:
            api_logger.info(f"   Translation: {translation_time:.2f}s")
        api_logger.info("="*80)
        
        return {
            "success": True,
            "extractedText": extracted_text,
            "translatedText": translated_text,
            "languages": request.languages,
            "targetLanguage": request.target_language,
            "autoTranslated": request.auto_translate and translated_text is not None,
            "processing_time": {
                "total": response_time,
                "extraction": extraction_time,
                "translation": translation_time if request.auto_translate else 0
            }
        }
    
    except HTTPException:
        raise
    except Exception as e:
        response_time = time.time() - start_time
        log_api_response("POST", "/api/openrouter/analyze-complete", 500, response_time)
        api_logger.error(f"❌ Complete analysis failed - Error: {str(e)}", exc_info=True)
        
        raise HTTPException(
            status_code=500,
            detail=f"Complete analysis failed: {str(e)}"
        )


@app.post("/api/openrouter/translate")
async def translate_with_openrouter(request: OpenRouterTranslationRequest):
    """
    Translate text using OpenRouter text models
    Supports technical glossary for Russian to English translation
    """
    start_time = time.time()
    log_api_request("POST", "/api/openrouter/translate", {"target_language": request.target_language})
    
    try:
        if not openrouter_service.is_available():
            raise HTTPException(
                status_code=503,
                detail="OpenRouter API key not configured. Please set OPENROUTER_API_KEY in environment variables."
            )
        
        api_logger.info(f"Translating text - Target language: {request.target_language}")
        
        # Translate with OpenRouter text models
        translated = await openrouter_service.translate_text(
            text=request.text,
            target_language=request.target_language,
            model=request.model,
            use_glossary=request.use_glossary
        )
        
        if not translated:
            raise HTTPException(
                status_code=503,
                detail="Failed to translate text. All OpenRouter models failed. Check API key and internet connection."
            )
        
        response_time = time.time() - start_time
        log_api_response("POST", "/api/openrouter/translate", 200, response_time)
        
        api_logger.info(
            f"Translation completed - "
            f"Time: {response_time:.2f}s, "
            f"Original length: {len(request.text)} chars, "
            f"Translated length: {len(translated)} chars"
        )
        
        return {
            "success": True,
            "originalText": request.text,
            "translatedText": translated,
            "targetLanguage": request.target_language,
            "processing_time": response_time
        }
    
    except HTTPException:
        raise
    except Exception as e:
        response_time = time.time() - start_time
        log_api_response("POST", "/api/openrouter/translate", 500, response_time)
        api_logger.error(f"Translation failed - Error: {str(e)}", exc_info=True)
        
        raise HTTPException(
            status_code=500,
            detail=f"Translation failed: {str(e)}"
        )


# ========== TRANSLATION ENDPOINTS ==========

class TranslationRequest(BaseModel):
    text: str
    from_lang: str = "ru"
    to_lang: str = "en"


@app.post("/api/translate")
async def translate_text(request: TranslationRequest):
    """
    Translate text from one language to another
    Uses technical glossary and Groq AI
    """
    try:
        translated = await translation_service.translate(
            text=request.text,
            from_lang=request.from_lang,
            to_lang=request.to_lang
        )
        
        return {
            "success": True,
            "originalText": request.text,
            "translatedText": translated,
            "from": request.from_lang,
            "to": request.to_lang
        }
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Translation failed: {str(e)}"
        )


# ========== EXPORT ENDPOINTS ==========

class ExportData(BaseModel):
    extractedData: dict
    translations: dict
    steelEquivalents: dict = {}


@app.post("/api/export/docx")
async def export_docx(data: ExportData):
    """
    Export data to DOCX format
    """
    try:
        file_path = await export_service.export_to_docx(
            extracted_data=data.extractedData,
            translations=data.translations,
            steel_equivalents=data.steelEquivalents
        )
        
        return FileResponse(
            file_path,
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="drawing_analysis.docx"
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"DOCX export failed: {str(e)}"
        )


@app.post("/api/export/xlsx")
async def export_xlsx(data: ExportData):
    """
    Export data to XLSX format
    """
    try:
        file_path = await export_service.export_to_xlsx(
            extracted_data=data.extractedData,
            translations=data.translations,
            steel_equivalents=data.steelEquivalents
        )
        
        return FileResponse(
            file_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="drawing_analysis.xlsx"
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"XLSX export failed: {str(e)}"
        )


# ========== CLOUD FOLDER API ==========
class CloudFolderRequest(BaseModel):
    url: str
    limit: int = 50  # Default: load 50 files at a time
    offset: int = 0  # Pagination offset

class CloudFileRequest(BaseModel):
    url: str
    fileName: str

class CloudFolderFilesRequest(BaseModel):
    folder_url: str
    folder_name: str = ""

@app.post("/api/cloud/folder")
async def get_cloud_folder(request: CloudFolderRequest):
    """Get folder structure from Mail.ru Cloud - LAZY: only structure, no recursive fetching"""
    log_api_request("POST", "/api/cloud/folder", {"url": request.url, "limit": request.limit, "offset": request.offset})
    
    try:
        import asyncio
        import concurrent.futures
        
        # LAZY approach: parse only structure (folders and file names), no recursive fetching
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            folder_data = await asyncio.wait_for(
                loop.run_in_executor(
                    executor, 
                    cloud_service.parse_mailru_folder_structure, 
                    request.url
                ),
                timeout=10.0  # 10 seconds should be enough for structure only
            )
        
        items = folder_data.get('items', [])
        total_items = len(items)
        
        # Apply pagination
        paginated_items = items[request.offset:request.offset + request.limit]
        has_more = (request.offset + request.limit) < total_items
        
        result = {
            'items': paginated_items,
            'folder_url': folder_data.get('folder_url', request.url),
            'pagination': {
                'total': total_items,
                'limit': request.limit,
                'offset': request.offset,
                'has_more': has_more,
                'returned': len(paginated_items)
            }
        }
        
        log_api_response("POST", "/api/cloud/folder", 200, 0.0)
        api_logger.info(f"Folder structure parsed: {len(paginated_items)}/{total_items} items returned (has_more={has_more})")
        return result
    except asyncio.TimeoutError:
        api_logger.error(f"Timeout parsing Mail.ru Cloud folder: {request.url}")
        log_api_response("POST", "/api/cloud/folder", 504, 0.0)
        api_logger.error("Request timeout")
        raise HTTPException(
            status_code=504,
            detail="Request timeout - folder is too large or server is slow. Try reducing the limit parameter."
        )
    except Exception as e:
        api_logger.error(f"Error getting cloud folder: {str(e)}")
        import traceback
        api_logger.error(f"Traceback: {traceback.format_exc()}")
        log_api_response("POST", "/api/cloud/folder", 500, 0.0)
        api_logger.error(f"Error details: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to load folder: {str(e)}"
        )

@app.post("/api/cloud/folder/files")
async def get_folder_files(request: CloudFolderFilesRequest):
    """Get files from a specific folder - LAZY: called on demand when user expands folder"""
    log_api_request("POST", "/api/cloud/folder/files", {"folder_url": request.folder_url, "folder_name": request.folder_name})
    
    try:
        import asyncio
        import concurrent.futures
        
        loop = asyncio.get_event_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            items = await asyncio.wait_for(
                loop.run_in_executor(
                    executor,
                    cloud_service.fetch_folder_files,
                    request.folder_url,
                    request.folder_name
                ),
                timeout=10.0
            )
        
        log_api_response("POST", "/api/cloud/folder/files", 200, 0.0)
        api_logger.info(f"Folder files fetched: {len(items)} items from {request.folder_name or request.folder_url}")
        return {'items': items, 'folder_url': request.folder_url}
        
    except asyncio.TimeoutError:
        api_logger.error(f"Timeout fetching folder files: {request.folder_url}")
        log_api_response("POST", "/api/cloud/folder/files", 504, 0.0)
        raise HTTPException(status_code=504, detail="Request timeout")
    except Exception as e:
        api_logger.error(f"Error fetching folder files: {str(e)}")
        log_api_response("POST", "/api/cloud/folder/files", 500, 0.0)
        raise HTTPException(status_code=500, detail=f"Failed to fetch folder files: {str(e)}")

@app.post("/api/cloud/file")
async def get_cloud_file(request: CloudFileRequest):
    """Download file from cloud URL"""
    log_api_request("POST", "/api/cloud/file", {"url": request.url, "fileName": request.fileName})
    
    try:
        file_content = cloud_service.download_file(request.url)
        log_api_response("POST", "/api/cloud/file", 200, 0.0)
        api_logger.info(f"File downloaded: {request.fileName} ({len(file_content)} bytes)")
        
        # Определяем MIME тип на основе расширения файла
        import mimetypes
        import urllib.parse
        mime_type, _ = mimetypes.guess_type(request.fileName)
        if not mime_type:
            # Fallback для известных типов
            if request.fileName.lower().endswith('.png'):
                mime_type = 'image/png'
            elif request.fileName.lower().endswith(('.jpg', '.jpeg')):
                mime_type = 'image/jpeg'
            elif request.fileName.lower().endswith('.pdf'):
                mime_type = 'application/pdf'
            else:
                mime_type = "application/octet-stream"
        
        # Handle Unicode filenames properly (RFC 5987)
        # Для изображений используем 'inline' вместо 'attachment', чтобы они отображались в браузере
        disposition_type = 'inline' if mime_type.startswith('image/') else 'attachment'
        
        # Encode filename for Content-Disposition header
        # Use ASCII fallback and RFC 5987 encoding for Unicode
        safe_filename = request.fileName.encode('ascii', 'ignore').decode('ascii')
        if safe_filename != request.fileName:
            # Contains non-ASCII characters, use RFC 5987 encoding
            encoded_filename = urllib.parse.quote(request.fileName, safe='')
            content_disposition = f'{disposition_type}; filename="{safe_filename}"; filename*=UTF-8\'\'{encoded_filename}'
        else:
            content_disposition = f'{disposition_type}; filename="{request.fileName}"'
        
        api_logger.info(f"Returning file with MIME type: {mime_type}, Content-Disposition: {content_disposition}")
        
        return Response(
            content=file_content,
            media_type=mime_type,
            headers={
                "Content-Disposition": content_disposition
            }
        )
    except Exception as e:
        error_msg = str(e)
        api_logger.error(f"Error downloading cloud file: {error_msg}", exc_info=True)
        log_api_response("POST", "/api/cloud/file", 500, 0.0)
        api_logger.error(f"Error details - URL: {request.url}, FileName: {request.fileName}, Error: {error_msg}")
        
        # Более информативное сообщение об ошибке
        if "HTML" in error_msg or "html" in error_msg.lower():
            detail_msg = "Файл недоступен для скачивания. Возможно, файл приватный или URL неверный."
        elif "timeout" in error_msg.lower():
            detail_msg = "Превышено время ожидания при загрузке файла. Попробуйте позже."
        elif "404" in error_msg or "Not Found" in error_msg:
            detail_msg = "Файл не найден по указанному URL."
        else:
            detail_msg = f"Ошибка загрузки файла: {error_msg}"
        
        raise HTTPException(status_code=500, detail=detail_msg)

@app.post("/api/export/pdf")
async def export_pdf(
    pdf: UploadFile = File(...),
    data: str = Form(...)
):
    """
    Export PDF with English overlay
    """
    try:
        import json
        data_dict = json.loads(data)
        
        pdf_content = await pdf.read()
        
        file_path = await export_service.export_to_pdf(
            pdf_content=pdf_content,
            extracted_data=data_dict.get("extractedData", {}),
            translations=data_dict.get("translations", {}),
            steel_equivalents=data_dict.get("steelEquivalents", {})
        )
        
        return FileResponse(
            file_path,
            media_type="application/pdf",
            filename="drawing_analysis_with_overlay.pdf"
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"PDF export failed: {str(e)}"
        )


# ========== TELEGRAM ENDPOINTS ==========

class TelegramSendRequest(BaseModel):
    bot_token: str
    chat_id: str
    extracted_data: Dict
    translations: Dict
    steel_equivalents: Optional[Dict] = {}
    send_files: Optional[bool] = False

class TelegramWebhookRequest(BaseModel):
    update_id: int
    callback_query: Optional[Dict] = None
    message: Optional[Dict] = None

@app.post("/api/telegram/send")
async def send_telegram_notification(request: TelegramSendRequest):
    """Send draft for review to Telegram"""
    log_api_request("POST", "/api/telegram/send", {"chat_id": request.chat_id})
    
    try:
        # Format message
        message = telegram_service.format_review_message(
            request.extracted_data,
            request.translations,
            request.steel_equivalents
        )
        
        # Generate unique message ID
        import time
        message_id = str(int(time.time() * 1000))
        
        # Send message with approval buttons
        result = telegram_service.send_message(
            bot_token=request.bot_token,
            chat_id=request.chat_id,
            message=message,
            show_approval=True,
            message_id=message_id
        )
        
        log_api_response("POST", "/api/telegram/send", 200, 0.0)
        api_logger.info(f"Telegram notification sent to chat {request.chat_id}")
        
        return {
            "success": True,
            "message_id": message_id,
            "telegram_message_id": result.get("result", {}).get("message_id"),
            "chat_id": request.chat_id
        }
    except Exception as e:
        api_logger.error(f"Error sending Telegram notification: {str(e)}")
        log_api_response("POST", "/api/telegram/send", 500, 0.0)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to send Telegram notification: {str(e)}"
        )

@app.post("/api/telegram/webhook")
async def handle_telegram_webhook(request: TelegramWebhookRequest):
    """Handle Telegram webhook callbacks (button clicks)"""
    log_api_request("POST", "/api/telegram/webhook", {"update_id": request.update_id})
    
    try:
        # Handle callback query (button click)
        if request.callback_query:
            callback_query = request.callback_query
            callback_id = callback_query.get("id")
            callback_data = callback_query.get("data", "")
            message = callback_query.get("message", {})
            chat_id = message.get("chat", {}).get("id")
            message_id = message.get("message_id")
            bot_token = callback_query.get("from", {}).get("id")  # This would need to be passed differently
            
            # Extract action and message ID from callback_data
            if callback_data.startswith("approve_"):
                action = "approve"
                msg_id = callback_data.replace("approve_", "")
                
                # Answer callback query
                # Note: In production, bot_token should be retrieved from database/storage
                # For now, we'll just log the action
                api_logger.info(f"Approval received for message {msg_id} in chat {chat_id}")
                
                # Update message to show approval
                # In production, you'd need to store bot_token with the message_id
                # For now, we'll return success
                
                log_api_response("POST", "/api/telegram/webhook", 200, 0.0)
                return {
                    "success": True,
                    "action": "approve",
                    "message_id": msg_id,
                    "chat_id": chat_id
                }
                
            elif callback_data.startswith("reject_"):
                action = "reject"
                msg_id = callback_data.replace("reject_", "")
                
                api_logger.info(f"Rejection received for message {msg_id} in chat {chat_id}")
                
                log_api_response("POST", "/api/telegram/webhook", 200, 0.0)
                return {
                    "success": True,
                    "action": "reject",
                    "message_id": msg_id,
                    "chat_id": chat_id
                }
        
        # Handle regular message
        if request.message:
            api_logger.info(f"Regular message received: {request.message.get('text', 'N/A')}")
            log_api_response("POST", "/api/telegram/webhook", 200, 0.0)
            return {"success": True, "type": "message"}
        
        log_api_response("POST", "/api/telegram/webhook", 200, 0.0)
        return {"success": True, "type": "unknown"}
        
    except Exception as e:
        api_logger.error(f"Error handling Telegram webhook: {str(e)}")
        log_api_response("POST", "/api/telegram/webhook", 500, 0.0)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to handle webhook: {str(e)}"
        )


# ========== FRONTEND SERVING (must be last) ==========
# Serve frontend for all non-API routes (SPA routing)
# This must be defined after all API routes
if FRONTEND_DIR.exists():
    @app.get("/{full_path:path}")
    async def serve_frontend(full_path: str, request: Request):
        # Don't interfere with API routes and docs
        if (full_path.startswith("api/") or 
            full_path.startswith("docs") or 
            full_path.startswith("openapi.json") or
            full_path.startswith("assets/")):
            raise HTTPException(status_code=404, detail="Not found")
        
        # Serve index.html for all frontend routes
        index_path = FRONTEND_DIR / "index.html"
        if index_path.exists():
            with open(index_path, "r", encoding="utf-8") as f:
                html_content = f.read()
            
            # Auto-detect API URL from request
            # Ensure HTTPS in production (fix Mixed Content error)
            base_url = str(request.base_url).rstrip("/")
            
            # Check if request came via HTTPS (Railway uses proxy with X-Forwarded-Proto)
            is_https = (
                request.url.scheme == 'https' or
                request.headers.get('X-Forwarded-Proto') == 'https' or
                request.headers.get('X-Forwarded-Ssl') == 'on' or
                'railway.app' in str(request.base_url)  # Railway domains should use HTTPS
            )
            
            # Force HTTPS if needed
            if is_https and base_url.startswith('http://'):
                base_url = base_url.replace('http://', 'https://', 1)
            
            api_url = f"{base_url}/api"
            
            # Inject API URL into HTML as window variable
            script_tag = f'<script>window.API_BASE_URL = "{api_url}";</script>'
            # Inject before closing head tag, or at the beginning if no head tag
            if '</head>' in html_content:
                html_content = html_content.replace('</head>', f'{script_tag}</head>')
            elif '<body>' in html_content:
                html_content = html_content.replace('<body>', f'{script_tag}<body>')
            else:
                html_content = script_tag + html_content
            
            return HTMLResponse(content=html_content)
        
        raise HTTPException(status_code=404, detail="Frontend not found")


if __name__ == "__main__":
    import uvicorn
    
    # Get port from environment variable (Railway provides PORT)
    port = int(os.getenv("PORT", 3000))
    host = os.getenv("HOST", "0.0.0.0")
    reload = os.getenv("ENVIRONMENT", "development") == "development"
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=reload
    )

