// PDF Processing and OCR Module
// This module handles PDF file processing, OCR, and preview

import { API_BASE_URL } from './config.js';

// Static import of PDF.js for Vite build compatibility
// PDF.js 4.x exports everything directly from the module
let pdfjsLib = null;

// Initialize PDF.js - try static import first
try {
  // Static import works with Vite build
  const pdfjsModule = await import('pdfjs-dist');
  
  // PDF.js 4.x exports functions directly (getDocument, etc.)
  if (pdfjsModule.getDocument && typeof pdfjsModule.getDocument === 'function') {
    pdfjsLib = pdfjsModule;
    
    // Configure worker - use CDN worker matching the library version
    if (typeof pdfjsLib.GlobalWorkerOptions !== 'undefined') {
      const version = pdfjsLib.version || '4.10.38';
      pdfjsLib.GlobalWorkerOptions.workerSrc = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${version}/pdf.worker.min.js`;
      console.log('PDF.js initialized:', {
        version: version,
        workerSrc: pdfjsLib.GlobalWorkerOptions.workerSrc,
        hasGetDocument: typeof pdfjsLib.getDocument === 'function'
      });
    }
  } else if (pdfjsModule.default && typeof pdfjsModule.default.getDocument === 'function') {
    pdfjsLib = pdfjsModule.default;
    
    if (typeof pdfjsLib.GlobalWorkerOptions !== 'undefined') {
      const version = pdfjsLib.version || '4.10.38';
      pdfjsLib.GlobalWorkerOptions.workerSrc = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${version}/pdf.worker.min.js`;
      console.log('PDF.js initialized (default export):', {
        version: version,
        workerSrc: pdfjsLib.GlobalWorkerOptions.workerSrc
      });
    }
  }
} catch (importError) {
  console.warn('PDF.js static import failed, will try dynamic import:', importError.message);
}

// Dynamically import PDF.js with retry mechanism (fallback)
async function getPdfJs(maxRetries = 10, retryDelay = 100) {
  if (pdfjsLib) {
    return pdfjsLib;
  }
  
  try {
      
      // Fallback: Try dynamic import (if static import failed)
      if (!pdfjsLib) {
        try {
          const pdfjsModule = await import('pdfjs-dist');
          if (pdfjsModule.getDocument && typeof pdfjsModule.getDocument === 'function') {
            pdfjsLib = pdfjsModule;
          } else if (pdfjsModule.default && typeof pdfjsModule.default.getDocument === 'function') {
            pdfjsLib = pdfjsModule.default;
          }
          
          if (pdfjsLib && typeof pdfjsLib.GlobalWorkerOptions !== 'undefined') {
            const version = pdfjsLib.version || '4.10.38';
            pdfjsLib.GlobalWorkerOptions.workerSrc = `https://cdnjs.cloudflare.com/ajax/libs/pdf.js/${version}/pdf.worker.min.js`;
            console.log('PDF.js loaded via dynamic import:', {
              version: version,
              workerSrc: pdfjsLib.GlobalWorkerOptions.workerSrc
            });
          }
        } catch (dynamicError) {
          console.warn('PDF.js dynamic import also failed:', dynamicError.message);
        }
      }
      
      if (!pdfjsLib || typeof pdfjsLib.getDocument !== 'function') {
        console.error('PDF.js not available - all import methods failed');
        throw new Error('PDF.js library is not available. Please ensure PDF.js is properly loaded.');
      }
    } catch (e) {
      console.error('Error checking PDF.js availability:', e);
      throw e;
    }
  }
  return pdfjsLib;
}

/**
 * Convert PDF to images for preview
 */
export async function renderPdfPreview(file, canvas) {
  try {
    // Use PDF.js for client-side rendering if available
    const pdfjs = await getPdfJs();
    if (pdfjs && typeof pdfjs.getDocument === 'function') {
      console.log('Using PDF.js for rendering');
      const arrayBuffer = await file.arrayBuffer();
      
      // Load PDF document
      const loadingTask = pdfjs.getDocument({ 
        data: arrayBuffer,
        verbosity: 0 // Reduce console output
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
      // PDF.js not available - throw error instead of fallback
      throw new Error('PDF.js library is not available. Cannot render PDF preview.');
    }
  } catch (error) {
    console.error('Error rendering PDF preview:', error);
    console.error('Error details:', {
      message: error.message,
      stack: error.stack,
      name: error.name
    });
    // Don't use iframe fallback - throw error instead
    throw new Error(`Failed to render PDF: ${error.message}`);
  }
}

/**
 * Upload PDF or image and process with OCR via backend API
 */
export async function processPdfWithOCR(file, languages = ['rus', 'eng'], progressCallback = null) {
  try {
    // Always use backend API endpoint
    if (progressCallback) {
      progressCallback(`Sending file to backend API...`);
    }
    
    const formData = new FormData();
    formData.append('file', file);
    formData.append('languages', languages.join('+'));
    
    if (progressCallback) {
      const fileSizeKB = (file.size / 1024).toFixed(1);
      progressCallback(`File size: ${fileSizeKB} KB`);
      progressCallback(`Languages: ${languages.join(' + ')}`);
      progressCallback(`Sending to backend OCR service...`);
    }
    
    const response = await fetch(`${API_BASE_URL}/ocr/process`, {
      method: 'POST',
      body: formData
    });
    
    if (!response.ok) {
      const errorData = await response.json().catch(() => ({ detail: 'Unknown error' }));
      throw new Error(errorData.detail || `HTTP ${response.status}: ${response.statusText}`);
    }
    
    if (progressCallback) {
      progressCallback(`Processing response from backend...`);
    }
    
    const result = await response.json();
    
    if (progressCallback && result.processing_info) {
      const info = result.processing_info;
      progressCallback(`Method used: ${info.method_used}`);
      progressCallback(`Actual time: ${info.actual_time?.toFixed(2) || 'N/A'}s`);
      progressCallback(`Reasoning: ${info.reasoning}`);
    }
    
    // Convert backend response to frontend format
    return {
      text: result.text || '',
      confidence: 0.9,
      language: languages.join('+'),
      pages: result.pages || 1,
      model: result.processing_info?.method_used || 'backend',
      isCropped: file.type && file.type.startsWith('image/'),
      processing_info: result.processing_info
    };
  } catch (error) {
    console.error('OCR processing error:', error);
    
    // Don't fallback to direct Groq if backend error is due to missing API key
    // This prevents infinite recursion and stack overflow
    if (error.message && error.message.includes('Groq API key not configured')) {
      throw new Error(`OCR processing failed: ${error.message}. Please configure GROQ_API_KEY in backend environment.`);
    }
    
    // Fallback: try direct Groq if backend fails (only for non-config errors)
    if (progressCallback) {
      progressCallback(`Backend failed, trying direct Groq API...`);
    }
    
    try {
      const { processPdfOCR, processImageOCR } = await import('./groqAgent.js');
      
      if (file.type && file.type.startsWith('image/')) {
        return await processImageOCR(file, languages, progressCallback);
      } else {
        return await processPdfOCR(file, languages, progressCallback);
      }
    } catch (fallbackError) {
      throw new Error(`OCR processing failed: ${error.message}. Backend unavailable and Groq fallback also failed: ${fallbackError.message}`);
    }
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

