// Data Extraction Module
// Extracts key elements from OCR text: materials, GOST/OST/TU, Ra, fits, heat treatment

/**
 * Extract materials from text
 */
export function extractMaterials(text) {
  const materials = [];
  
  // Russian steel patterns
  const steelPatterns = [
    /сталь\s*(\d+[А-Яа-я]*)/gi,
    /Ст\.?\s*(\d+[А-Яа-я]*)/gi,
    /(\d+[А-Яа-я]+)\s*сталь/gi,
    /марка\s*(\d+[А-Яа-я]+)/gi,
    /Material:\s*([A-Z0-9]+)/gi,
    /Steel\s+([A-Z0-9]+)/gi
  ];
  
  steelPatterns.forEach(pattern => {
    const matches = text.matchAll(pattern);
    for (const match of matches) {
      const material = match[1]?.trim();
      if (material && !materials.includes(material)) {
        materials.push(material);
      }
    }
  });
  
  // Extract any GOST references that might indicate material
  const gostMaterials = extractGOSTStandards(text).map(g => {
    const materialMatch = text.match(new RegExp(`(${g.replace(/[^\w\s]/g, '\\$&')}[^\\n]{0,100})`, 'i'));
    return materialMatch ? materialMatch[1] : null;
  }).filter(Boolean);
  
  return [...new Set([...materials, ...gostMaterials])];
}

/**
 * Extract GOST/OST/TU standards
 */
export function extractGOSTStandards(text) {
  const standards = [];
  
  // GOST patterns
  const gostPatterns = [
    /ГОСТ\s*(\d+[-.]?\d*)/gi,
    /GOST\s*(\d+[-.]?\d*)/gi,
    /ГОСТ\s*(\d+\.\d+)/gi,
    /ГОСТ\s*(\d+-\d+)/gi
  ];
  
  // OST patterns
  const ostPatterns = [
    /ОСТ\s*(\d+[-.]?\d*)/gi,
    /OST\s*(\d+[-.]?\d*)/gi
  ];
  
  // TU patterns
  const tuPatterns = [
    /ТУ\s*(\d+[-.]?\d*)/gi,
    /TU\s*(\d+[-.]?\d*)/gi,
    /ТУ\s*(\d+\.\d+)/gi
  ];
  
  const allPatterns = [
    ...gostPatterns.map(p => ({ pattern: p, prefix: 'ГОСТ' })),
    ...ostPatterns.map(p => ({ pattern: p, prefix: 'ОСТ' })),
    ...tuPatterns.map(p => ({ pattern: p, prefix: 'ТУ' }))
  ];
  
  allPatterns.forEach(({ pattern, prefix }) => {
    const matches = text.matchAll(pattern);
    for (const match of matches) {
      const standard = `${prefix} ${match[1]}`;
      if (!standards.includes(standard)) {
        standards.push(standard);
      }
    }
  });
  
  return standards;
}

/**
 * Extract surface roughness (Ra values)
 */
export function extractRaValues(text) {
  const raValues = [];
  
  const raPatterns = [
    /Ra\s*[=:]\s*([0-9.]+)/gi,
    /Ra\s*([0-9.]+)/gi,
    /шероховатость\s*Ra\s*[=:]\s*([0-9.]+)/gi,
    /Rz\s*[=:]\s*([0-9.]+)/gi,
    /Rz\s*([0-9.]+)/gi,
    /Rа\s*[=:]\s*([0-9.]+)/gi, // Cyrillic 'а'
    /roughness\s*Ra\s*[=:]\s*([0-9.]+)/gi
  ];
  
  raPatterns.forEach(pattern => {
    const matches = text.matchAll(pattern);
    for (const match of matches) {
      const ra = parseFloat(match[1]);
      if (!isNaN(ra) && ra > 0 && !raValues.includes(ra)) {
        raValues.push(ra);
      }
    }
  });
  
  return raValues.sort((a, b) => a - b);
}

/**
 * Extract fits (посадки)
 */
export function extractFits(text) {
  const fits = [];
  
  const fitPatterns = [
    /посадка\s*[=:]\s*([A-Z]\d+\/[a-z]\d+)/gi,
    /посадка\s*([A-Z]\d+\/[a-z]\d+)/gi,
    /fit\s*[=:]\s*([A-Z]\d+\/[a-z]\d+)/gi,
    /([A-Z]\d+\/[a-z]\d+)\s*посадка/gi,
    /([Hh]\d+\/[fg]\d+)/gi, // Common H7/f7 pattern
    /([Hh]\d+\/[eg]\d+)/gi
  ];
  
  fitPatterns.forEach(pattern => {
    const matches = text.matchAll(pattern);
    for (const match of matches) {
      const fit = match[1].toUpperCase();
      if (!fits.includes(fit)) {
        fits.push(fit);
      }
    }
  });
  
  return fits;
}

/**
 * Extract heat treatment information
 */
export function extractHeatTreatment(text) {
  const treatments = [];
  
  const treatmentPatterns = [
    /термообработка[:\s]+(.+?)(?:\n|$)/gi,
    /heat\s+treatment[:\s]+(.+?)(?:\n|$)/gi,
    /закалка[:\s]+(.+?)(?:\n|$)/gi,
    /hardening[:\s]+(.+?)(?:\n|$)/gi,
    /HRC\s*[=:]\s*(\d+[-–]?\d*)/gi,
    /HRC\s*(\d+[-–]?\d*)/gi,
    /отпуск[:\s]+(.+?)(?:\n|$)/gi,
    /tempering[:\s]+(.+?)(?:\n|$)/gi,
    /нормализация[:\s]+(.+?)(?:\n|$)/gi,
    /normalization[:\s]+(.+?)(?:\n|$)/gi,
    /отжиг[:\s]+(.+?)(?:\n|$)/gi,
    /annealing[:\s]+(.+?)(?:\n|$)/gi
  ];
  
  treatmentPatterns.forEach(pattern => {
    const matches = text.matchAll(pattern);
    for (const match of matches) {
      const treatment = match[1]?.trim();
      if (treatment && treatment.length > 2 && !treatments.includes(treatment)) {
        treatments.push(treatment);
      }
    }
  });
  
  return treatments;
}

/**
 * Extract structured data using OpenRouter (same method as chat)
 */
async function extractStructuredDataWithOpenRouter(ocrText) {
  try {
    const { getApiBaseUrl } = await import('./config.js');
    const apiBaseUrl = getApiBaseUrl();
    
    const response = await fetch(`${apiBaseUrl}/openrouter/extract-structured-data`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        ocr_text: ocrText
      })
    });
    
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}: ${response.statusText}`);
    }
    
    const result = await response.json();
    
    if (result.success && result.data) {
      return result.data;
    }
    
    throw new Error('Invalid response from OpenRouter');
  } catch (error) {
    console.warn('OpenRouter extraction failed:', error);
    throw error;
  }
}

/**
 * Extract all key data from OCR text
 * Uses OpenRouter first (same as chat), then Groq, then regex fallback
 */
export async function extractAllData(ocrText) {
  let aiData = null;
  
  // Step 1: Try OpenRouter first (same method as chat - works great!)
  try {
    console.log('📊 Extracting data via OpenRouter (like in chat)...');
    aiData = await extractStructuredDataWithOpenRouter(ocrText);
    console.log('✅ OpenRouter successfully extracted data');
  } catch (openRouterError) {
    console.warn('OpenRouter extraction failed, trying Groq fallback:', openRouterError);
    
    // Step 2: Fallback to Groq if OpenRouter fails
    try {
      const { extractStructuredData } = await import('./groqAgent.js');
      aiData = await extractStructuredData(ocrText);
      console.log('✅ Groq successfully extracted data');
    } catch (groqError) {
      console.warn('Groq extraction also failed:', groqError);
      aiData = null;
    }
  }
  
  // Always use regex as additional fallback/complement
  const regexData = {
    materials: extractMaterials(ocrText),
    standards: extractGOSTStandards(ocrText),
    raValues: extractRaValues(ocrText),
    fits: extractFits(ocrText),
    heatTreatment: extractHeatTreatment(ocrText),
  };
  
  // Combine AI and regex results
  if (aiData) {
    // Merge AI and regex, prefer AI but add any additional from regex
    return {
      materials: [...new Set([...aiData.materials || [], ...regexData.materials])],
      standards: [...new Set([...aiData.standards || [], ...regexData.standards])],
      raValues: [...new Set([...aiData.raValues || [], ...regexData.raValues])].sort((a, b) => a - b),
      fits: [...new Set([...aiData.fits || [], ...regexData.fits])],
      heatTreatment: [...new Set([...aiData.heatTreatment || [], ...regexData.heatTreatment])],
      rawText: ocrText
    };
  } else {
    // Fallback to regex only if all AI methods failed
    console.warn('All AI extraction methods failed, using regex fallback');
    return {
      materials: regexData.materials,
      standards: regexData.standards,
      raValues: regexData.raValues,
      fits: regexData.fits,
      heatTreatment: regexData.heatTreatment,
      rawText: ocrText
    };
  }
}

