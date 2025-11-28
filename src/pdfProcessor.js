// PDF Processing and OCR Module
// This module handles PDF file processing, OCR, and preview

import { API_BASE_URL } from './config.js';
import * as pdfjsModule from 'pdfjs-dist';

let pdfjsLib = null;

// Initialize PDF.js from npm package
async function getPdfJs() {
  if (!pdfjsLib) {
    try {
      // Try to use npm package import first
      if (pdfjsModule && typeof pdfjsModule.getDocument === 'function') {
        pdfjsLib = pdfjsModule;
        console.log('PDF.js loaded: from npm package (pdfjs-dist)');
      } 
      // Try default export
      else if (pdfjsModule.default && typeof pdfjsModule.default.getDocument === 'function') {
        pdfjsLib = pdfjsModule.default;
        console.log('PDF.js loaded: from npm package default export');
      }
      // Fallback to CDN version if available globally
      else if (typeof window !== 'undefined') {
        if (window.pdfjsLib && typeof window.pdfjsLib.getDocument === 'function') {
          pdfjsLib = window.pdfjsLib;
          console.log('PDF.js loaded: from CDN (window.pdfjsLib)');
        } else if (window.pdfjs && typeof window.pdfjs.getDocument === 'function') {
          pdfjsLib = window.pdfjs;
          console.log('PDF.js loaded: from CDN (window.pdfjs)');
        }
      }
      
      // Configure worker if PDF.js was loaded
      if (pdfjsLib && pdfjsLib.GlobalWorkerOptions) {
        // Use worker from npm package via Vite
        // Import worker as a URL - Vite will bundle it and make it available
        try {
          // Use Vite's ?worker import for proper bundling
          const workerModule = await import('pdfjs-dist/build/pdf.worker.min.mjs?url');
          pdfjsLib.GlobalWorkerOptions.workerSrc = workerModule.default || workerModule;
          console.log('PDF.js worker configured from npm package (bundled by Vite)');
        } catch (workerError) {
          console.warn('Failed to import worker from npm, trying alternative method:', workerError);
          // Alternative: use direct path (works in production after build)
          try {
            // In production, Vite will have bundled the worker
            pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
              'pdfjs-dist/build/pdf.worker.min.mjs',
              import.meta.url
            ).href;
            console.log('PDF.js worker configured using URL constructor');
          } catch (urlError) {
            // Final fallback to CDN
            try {
              pdfjsLib.GlobalWorkerOptions.workerSrc = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${pdfjsLib.version || '4.0.379'}/pdf.worker.min.js`;
              console.log('PDF.js worker fallback to CDN');
            } catch (cdnError) {
              console.warn('Could not configure PDF.js worker:', cdnError);
            }
          }
        }
      }
      
      if (!pdfjsLib || typeof pdfjsLib.getDocument !== 'function') {
        console.error('PDF.js not available. Checked:', {
          hasPdfjsModule: !!pdfjsModule,
          hasGetDocument: pdfjsModule && typeof pdfjsModule.getDocument === 'function',
          hasDefault: pdfjsModule && !!pdfjsModule.default,
          hasWindowPdfjsLib: typeof window !== 'undefined' && typeof window.pdfjsLib !== 'undefined',
          moduleKeys: pdfjsModule ? Object.keys(pdfjsModule).slice(0, 10) : null
        });
        return null;
      }
    } catch (e) {
      console.error('Error loading PDF.js:', e);
      return null;
    }
  }
  return pdfjsLib;
}

/**
 * Convert PDF to images for preview
 */
export async function renderPdfPreview(file, canvas) {
  try {
    // Validate file before processing
    if (!file || file.size === 0) {
      throw new Error('PDF file is empty');
    }
    
    // Use PDF.js for client-side rendering if available
    const pdfjs = await getPdfJs();
    if (pdfjs && typeof pdfjs.getDocument === 'function') {
      console.log('Using PDF.js for rendering', { fileSize: file.size, fileName: file.name });
      const arrayBuffer = await file.arrayBuffer();
      
      // Validate PDF structure (check first bytes)
      const pdfHeader = new Uint8Array(arrayBuffer.slice(0, 4));
      const isPdf = pdfHeader[0] === 0x25 && pdfHeader[1] === 0x50 && pdfHeader[2] === 0x44 && pdfHeader[3] === 0x46; // %PDF
      
      if (!isPdf) {
        console.warn('File does not appear to be a valid PDF (missing %PDF header)');
        console.warn('First bytes:', Array.from(pdfHeader).map(b => `0x${b.toString(16)}`).join(' '));
        // Continue anyway - some PDFs might have different structure
      }
      
      // Load PDF document with better error handling
      const loadingTask = pdfjs.getDocument({ 
        data: arrayBuffer,
        verbosity: 0, // Reduce console output
        // Add error recovery options
        stopAtErrors: false,
        isEvalSupported: false
      });
      
      const pdf = await loadingTask.promise;
      const page = await pdf.getPage(1);
      const viewport = page.getViewport({ scale: 2.0 });
      
      // Set canvas size
      canvas.width = viewport.width;
      canvas.height = viewport.height;
      
      // Render page to canvas
      const context = canvas.getContext('2d');
      const renderContext = {
        canvasContext: context,
        viewport: viewport
      };
      
      await page.render(renderContext).promise;
      
      console.log('PDF rendered successfully on canvas');
      // Return data URL of rendered canvas
      return canvas.toDataURL('image/png');
    } else {
      console.log('PDF.js not available, using iframe fallback');
      // Fallback: create object URL for iframe preview
      const url = URL.createObjectURL(file);
      return url;
    }
  } catch (error) {
    console.error('Error rendering PDF preview:', error);
    console.error('Error details:', {
      message: error.message,
      stack: error.stack,
      name: error.name,
      fileName: file?.name,
      fileSize: file?.size,
      fileType: file?.type
    });
    
    // Check if it's a PDF structure error
    if (error.name === 'InvalidPDFException' || error.message.includes('Invalid PDF structure')) {
      console.warn('PDF structure error - file may be corrupted or not a valid PDF');
      console.warn('Attempting iframe fallback...');
      
      // Try to verify file is actually a PDF by checking first bytes
      try {
        const firstBytes = await file.slice(0, 4).arrayBuffer();
        const pdfHeader = new Uint8Array(firstBytes);
        const isPdf = pdfHeader[0] === 0x25 && pdfHeader[1] === 0x50 && pdfHeader[2] === 0x44 && pdfHeader[3] === 0x46;
        
        if (!isPdf) {
          throw new Error('File is not a valid PDF (missing %PDF header). First bytes: ' + 
            Array.from(pdfHeader).map(b => `0x${b.toString(16).padStart(2, '0')}`).join(' '));
        }
      } catch (validationError) {
        console.error('PDF validation failed:', validationError);
        throw new Error(`Invalid PDF file: ${validationError.message}. Original error: ${error.message}`);
      }
    }
    
    // Fallback on error - use iframe
    try {
      const url = URL.createObjectURL(file);
      console.log('Using iframe fallback for PDF preview');
      return url;
    } catch (fallbackError) {
      console.error('Fallback also failed:', fallbackError);
      throw new Error(`Failed to render PDF: ${error.message}. Fallback also failed: ${fallbackError.message}`);
    }
  }
}

/**
 * Upload PDF or image and process with OCR via backend API
 */
/**
 * Optimize image file - compress and resize if needed
 * Returns optimized File object ready for OCR
 */
async function optimizeImageForOCR(file) {
  if (!file.type || !file.type.startsWith('image/')) {
    return file; // Not an image, return as-is
  }
  
  return new Promise((resolve, reject) => {
    const img = new Image();
    const reader = new FileReader();
    
    reader.onload = (e) => {
      img.onload = () => {
        // Определяем максимальный размер для OCR (достаточно для качества, но быстро)
        const MAX_DIMENSION = 2048; // Максимальная сторона
        const MAX_FILE_SIZE = 2 * 1024 * 1024; // 2 MB максимальный размер файла
        
        let width = img.width;
        let height = img.height;
        let scale = 1;
        
        // Масштабируем если изображение слишком большое
        if (width > MAX_DIMENSION || height > MAX_DIMENSION) {
          scale = Math.min(MAX_DIMENSION / width, MAX_DIMENSION / height);
          width = Math.round(width * scale);
          height = Math.round(height * scale);
        }
        
        // Создаем canvas для оптимизации
        const canvas = document.createElement('canvas');
        canvas.width = width;
        canvas.height = height;
        const ctx = canvas.getContext('2d');
        
        // Рисуем изображение с масштабированием
        ctx.drawImage(img, 0, 0, width, height);
        
        // Конвертируем в blob с оптимальным качеством
        canvas.toBlob((blob) => {
          if (!blob) {
            resolve(file); // Если не удалось, возвращаем оригинал
            return;
          }
          
          // Если файл все еще большой, сжимаем сильнее
          if (blob.size > MAX_FILE_SIZE) {
            // Повторное сжатие с меньшим качеством
            canvas.toBlob((compressedBlob) => {
              if (compressedBlob && compressedBlob.size <= MAX_FILE_SIZE * 1.5) {
                const optimizedFile = new File([compressedBlob], file.name, { 
                  type: file.type || 'image/jpeg' 
                });
                resolve(optimizedFile);
              } else {
                resolve(file); // Возвращаем оригинал если сжатие не помогло
              }
            }, file.type || 'image/jpeg', 0.75); // Качество 75%
          } else {
            const optimizedFile = new File([blob], file.name, { 
              type: file.type || 'image/jpeg' 
            });
            resolve(optimizedFile);
          }
        }, file.type || 'image/jpeg', 0.85); // Качество 85% для OCR
      };
      
      img.onerror = () => {
        resolve(file); // Если ошибка загрузки, возвращаем оригинал
      };
      
      img.src = e.target.result;
    };
    
    reader.onerror = () => {
      resolve(file); // При ошибке чтения возвращаем оригинал
    };
    
    reader.readAsDataURL(file);
  });
}

export async function processPdfWithOCR(file, languages = ['rus'], progressCallback = null, ocrMethod = 'auto', ocrQuality = 'balanced', abortSignal = null) {
  let progressTimer = null; // Объявляем на уровне функции для доступа в catch
  
  try {
    // Optimize image files before sending
    let fileToProcess = file;
    const isImage = file.type && file.type.startsWith('image/');
    
    if (isImage) {
      if (progressCallback) {
        progressCallback(`Оптимизация изображения для OCR...`);
      }
      fileToProcess = await optimizeImageForOCR(file);
      
      if (progressCallback) {
        const originalSize = (file.size / 1024).toFixed(1);
        const optimizedSize = (fileToProcess.size / 1024).toFixed(1);
        if (fileToProcess.size < file.size) {
          progressCallback(`✓ Изображение оптимизировано: ${originalSize} KB → ${optimizedSize} KB`);
        }
      }
    }
    
    // Always use backend API endpoint
    if (progressCallback) {
      progressCallback(`Sending file to backend API...`);
    }
    
    const formData = new FormData();
    formData.append('file', fileToProcess);
    formData.append('languages', languages.join('+'));
    formData.append('ocr_method', ocrMethod);
    formData.append('ocr_quality', ocrQuality);
    
    if (progressCallback) {
      const fileSizeKB = (file.size / 1024).toFixed(1);
      progressCallback(`File size: ${fileSizeKB} KB`);
      progressCallback(`Languages: ${languages.join(' + ')}`);
      progressCallback(`Sending to backend OCR service...`);
    }
    
    // Увеличенный таймаут для OCR обработки (может занять до 5 минут для больших PDF)
    // Используем переданный signal или создаем новый
    const controller = abortSignal ? { signal: abortSignal, abort: () => {} } : new AbortController();
    const timeoutId = abortSignal ? null : setTimeout(() => controller.abort(), 300000); // 5 минут
    
    // Запускаем таймер для показа реального времени прогресса
    let elapsedSeconds = 0;
    if (progressCallback) {
      progressTimer = setInterval(() => {
        elapsedSeconds += 2;
        const minutes = Math.floor(elapsedSeconds / 60);
        const seconds = elapsedSeconds % 60;
        const timeStr = minutes > 0 ? `${minutes}м ${seconds}с` : `${seconds}с`;
        progressCallback(`⏳ Обработка... (${timeStr})`);
      }, 2000); // Обновляем каждые 2 секунды
    }
    
    const response = await fetch(`${API_BASE_URL}/ocr/process`, {
      method: 'POST',
      body: formData,
      signal: abortSignal || controller.signal
    });
    
    if (timeoutId) {
      clearTimeout(timeoutId);
    }
    if (progressTimer) {
      clearInterval(progressTimer);
      progressTimer = null;
    }
    
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
      const errorMessage = errorData.detail || errorData.message || `HTTP ${response.status}: ${response.statusText}`;
      throw new Error(errorMessage);
    }
    
    if (progressCallback) {
      progressCallback(`Обработка ответа от backend...`);
    }
    
    const result = await response.json();
    
    // Проверяем, что результат валиден
    if (!result) {
      throw new Error("Backend вернул пустой ответ");
    }
    
    if (progressCallback && result.processing_info) {
      const info = result.processing_info;
      progressCallback(`Метод: ${info.method_used || info.method || 'unknown'}`);
      progressCallback(`Время: ${info.actual_time?.toFixed(2) || 'N/A'}s`);
      if (info.reasoning) {
        progressCallback(`Причина: ${info.reasoning}`);
      }
    }
    
    // Convert backend response to frontend format
    const finalResult = {
      text: result.text || '',
      confidence: 0.9,
      language: languages.join('+'),
      pages: result.pages || 1,
      model: result.processing_info?.method_used || result.processing_info?.method || 'backend',
      isCropped: file.type && file.type.startsWith('image/'),
      processing_info: result.processing_info || {}
    };
    
    return finalResult;
  } catch (error) {
    console.error('OCR processing error:', error);
    
    // Останавливаем таймер при ошибке
    if (progressTimer) {
      clearInterval(progressTimer);
      progressTimer = null;
    }
    
    // Groq полностью отключен - используется только OpenRouter + OCR fallback'и
    // Если backend не сработал, возвращаем ошибку без fallback на Groq
    let errorMessage = error.message;
    
    // Обрабатываем таймаут
    if (error.name === 'AbortError' || errorMessage.includes('timeout') || errorMessage.includes('failed to respond')) {
      errorMessage = 'Таймаут при обработке OCR. Файл слишком большой или сервер перегружен. Попробуйте позже или используйте меньший файл.';
    }
    
    if (progressCallback) {
      progressCallback(`❌ Backend OCR failed: ${errorMessage}`);
    }
    
    throw new Error(`OCR processing failed: ${errorMessage}. Используется только OpenRouter + OCR fallback'и. Groq отключен.`);
  }
}

/**
 * Detect if PDF is vector or raster (scanned)
 */
export async function detectPdfType(file) {
  try {
    const pdfjs = await getPdfJs();
    if (pdfjs) {
      const arrayBuffer = await file.arrayBuffer();
      const pdf = await pdfjs.getDocument({ data: arrayBuffer }).promise;
      
      // Check if PDF has text layer
      const page = await pdf.getPage(1);
      const textContent = await page.getTextContent();
      
      if (textContent.items.length > 0 && textContent.items[0].str.trim().length > 0) {
        return 'vector';
      } else {
        return 'raster';
      }
    }
    
    // Default assumption
    return 'raster';
  } catch (error) {
    console.error('Error detecting PDF type:', error);
    return 'raster'; // Default to raster for OCR
  }
}

/**
 * Extract PDF metadata
 */
export async function extractPdfMetadata(file) {
  try {
    const pdfjs = await getPdfJs();
    if (pdfjs) {
      const arrayBuffer = await file.arrayBuffer();
      const pdf = await pdfjs.getDocument({ data: arrayBuffer }).promise;
      const metadata = await pdf.getMetadata();
      
      return {
        title: metadata.info?.Title || file.name,
        author: metadata.info?.Author || 'Unknown',
        pages: pdf.numPages,
        creationDate: metadata.info?.CreationDate || new Date().toISOString(),
        fileSize: file.size,
        fileName: file.name
      };
    }
  } catch (error) {
    console.error('Error extracting PDF metadata:', error);
  }
  
  // Fallback metadata
  return {
    title: file.name,
    author: 'Unknown',
    pages: 1,
    creationDate: new Date().toISOString(),
    fileSize: file.size,
    fileName: file.name
  };
}

