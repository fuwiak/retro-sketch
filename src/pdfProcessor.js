// PDF Processing and OCR Module
// This module handles PDF file processing, OCR, and preview

import { API_BASE_URL } from './config.js';

let pdfjsLib = null;
let pdfjsImportPromise = null;

// Dynamically import PDF.js with retry mechanism
async function getPdfJs(maxRetries = 10, retryDelay = 100) {
  if (!pdfjsLib) {
    try {
      // First, try to import from npm package (pdfjs-dist)
      if (!pdfjsImportPromise) {
        pdfjsImportPromise = (async () => {
          try {
            const pdfjsModule = await import('pdfjs-dist');
            // PDF.js 4.x exports as default or named export
            const lib = pdfjsModule.default || pdfjsModule || pdfjsModule.pdfjsLib;
            if (lib && typeof lib.getDocument === 'function') {
              // Configure worker - use CDN worker for better compatibility
              if (typeof lib.GlobalWorkerOptions !== 'undefined') {
                // Use CDN worker instead of bundled one for better compatibility
                lib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.0.379/pdf.worker.min.js';
              }
              console.log('PDF.js loaded from npm package (pdfjs-dist):', {
                version: lib.version || 'unknown',
                hasGetDocument: typeof lib.getDocument === 'function'
              });
              return lib;
            }
          } catch (importError) {
            console.debug('PDF.js npm import failed, trying CDN:', importError.message);
            return null;
          }
        })();
      }
      
      const npmLib = await pdfjsImportPromise;
      if (npmLib) {
        pdfjsLib = npmLib;
        return pdfjsLib;
      }
      
      // Fallback: Try to use CDN version if available globally
      if (typeof window !== 'undefined') {
        // Wait for PDF.js to be available (it loads asynchronously from CDN)
        for (let attempt = 0; attempt < maxRetries; attempt++) {
          // PDF.js from CDN might be available in multiple ways:
          // Check all possible global variable names
          const possibleLib = window.pdfjsLib || 
                            window.pdfjs || 
                            window.pdfjsLib?.pdfjsLib ||
                            (typeof pdfjsLib !== 'undefined' ? pdfjsLib : null);
          
          // Also check if it's a UMD module that exports differently
          if (!possibleLib) {
            // Check for PDF.js in various formats
            const scripts = Array.from(document.querySelectorAll('script[src*="pdf"]'));
            for (const script of scripts) {
              if (script.src.includes('pdf.js') || script.src.includes('pdfjs')) {
                // Try to access after script loads
                try {
                  const checkLib = window.pdfjsLib || window.pdfjs || window.pdf || null;
                  if (checkLib && typeof checkLib.getDocument === 'function') {
                    pdfjsLib = checkLib;
                    break;
                  }
                } catch (e) {
                  // Continue searching
                }
              }
            }
          }
          
          if (possibleLib && typeof possibleLib.getDocument === 'function') {
            pdfjsLib = possibleLib;
            
            // Configure worker source if needed
            if (typeof pdfjsLib.GlobalWorkerOptions !== 'undefined') {
              if (!pdfjsLib.GlobalWorkerOptions.workerSrc) {
                pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.0.379/pdf.worker.min.js';
              }
            }
            
            console.log('PDF.js available from CDN:', {
              version: pdfjsLib.version || 'unknown',
              hasGetDocument: typeof pdfjsLib.getDocument === 'function',
              hasGlobalWorkerOptions: typeof pdfjsLib.GlobalWorkerOptions !== 'undefined',
              attempt: attempt + 1
            });
            break;
          }
          
          // If not found, wait a bit and retry (PDF.js might still be loading)
          if (attempt < maxRetries - 1) {
            await new Promise(resolve => setTimeout(resolve, retryDelay));
          }
        }
        
        // Verify that getDocument function exists
        if (!pdfjsLib || typeof pdfjsLib.getDocument !== 'function') {
          console.warn('PDF.js not found after retries. Available window properties:', 
            Object.keys(window).filter(k => k.toLowerCase().includes('pdf')));
          console.warn('window.pdfjsLib:', window.pdfjsLib);
          console.warn('window.pdfjs:', window.pdfjs);
          console.warn('window.pdf:', window.pdf);
          pdfjsLib = null;
        }
      }
      
      if (!pdfjsLib) {
        console.warn('PDF.js not available, using iframe fallback');
        return null;
      }
    } catch (e) {
      console.error('Error checking PDF.js availability:', e);
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
      name: error.name
    });
    // Fallback on error - use iframe
    try {
      const url = URL.createObjectURL(file);
      return url;
    } catch (fallbackError) {
      console.error('Fallback also failed:', fallbackError);
      throw new Error(`Failed to render PDF: ${error.message}`);
    }
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

