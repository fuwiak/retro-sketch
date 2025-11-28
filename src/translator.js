// Translation Service with Technical Glossary
// Translates Russian technical terms to English using OpenRouter

/**
 * Technical glossary for engineering/drawing terms
 */
const TECHNICAL_GLOSSARY = {
  // Materials
  'сталь': 'steel',
  'Сталь': 'Steel',
  'Ст.': 'Steel',
  'марка': 'grade',
  'Марка': 'Grade',
  
  // Standards
  'ГОСТ': 'GOST',
  'ОСТ': 'OST',
  'ТУ': 'TU',
  
  // Surface roughness
  'шероховатость': 'surface roughness',
  'Шероховатость': 'Surface Roughness',
  'Ra': 'Ra',
  'Rz': 'Rz',
  
  // Fits
  'посадка': 'fit',
  'Посадка': 'Fit',
  
  // Heat treatment
  'термообработка': 'heat treatment',
  'Термообработка': 'Heat Treatment',
  'закалка': 'hardening',
  'Закалка': 'Hardening',
  'отпуск': 'tempering',
  'Отпуск': 'Tempering',
  'нормализация': 'normalization',
  'Нормализация': 'Normalization',
  'отжиг': 'annealing',
  'Отжиг': 'Annealing',
  
  // Common phrases
  'Материал': 'Material',
  'материал': 'material',
  'Размер': 'Size',
  'размер': 'size',
  'Допуск': 'Tolerance',
  'допуск': 'tolerance',
  'Размеры': 'Dimensions',
  'размеры': 'dimensions',
  'Чертеж': 'Drawing',
  'чертеж': 'drawing',
  'Деталь': 'Part',
  'деталь': 'part'
};

/**
 * Translate text using OpenRouter API with technical glossary
 */
export async function translateToEnglish(text, useGlossary = true, model = null, temperature = 0.3) {
  try {
    // First apply technical glossary
    let translated = text;
    if (useGlossary) {
      for (const [ru, en] of Object.entries(TECHNICAL_GLOSSARY)) {
        const regex = new RegExp(`\\b${ru.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\b`, 'gi');
        translated = translated.replace(regex, en);
      }
    }
    
    // Use OpenRouter API for translation
    const { API_BASE_URL } = await import('./config.js');
    try {
      const response = await fetch(`${API_BASE_URL}/translate`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          text: translated,
          from_lang: 'ru',
          to_lang: 'en',
          model: model,
          temperature: temperature
        })
      });
      
      if (response.ok) {
        const result = await response.json();
        return result.translatedText || translated;
      } else {
        throw new Error(`Translation API error: ${response.status}`);
      }
    } catch (apiError) {
      console.warn('Translation API failed, using glossary only:', apiError);
      return translated; // Return glossary-translated text if API fails
    }
  } catch (error) {
    console.error('Translation error:', error);
    return text; // Return original if translation fails
  }
}

/**
 * Translate extracted data structure
 */
export async function translateExtractedData(data, model = null, temperature = 0.3) {
  const translated = {
    materials: await Promise.all(data.materials.map(m => translateToEnglish(m, true, model, temperature))),
    standards: await Promise.all(data.standards.map(s => translateToEnglish(s, true, model, temperature))),
    raValues: data.raValues, // Numbers don't need translation
    fits: data.fits, // Already in standard format
    heatTreatment: await Promise.all(data.heatTreatment.map(h => translateToEnglish(h, true, model, temperature))),
    rawText: await translateToEnglish(data.rawText, true, model, temperature)
  };
  
  return translated;
}
