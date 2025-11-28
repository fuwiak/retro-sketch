"""
Translation Service using OpenRouter API
Handles technical translation with glossary support
"""

import os
from typing import Dict, Optional

from services.openrouter_service import OpenRouterService
from services.logger import api_logger

# Technical glossary (Russian to English)
TECHNICAL_GLOSSARY = {
    "материал": "material",
    "сталь": "steel",
    "ГОСТ": "GOST",
    "ОСТ": "OST",
    "ТУ": "TU",
    "посадка": "fit",
    "термообработка": "heat treatment",
    "шероховатость": "roughness",
    "Ra": "Ra",
    "точность": "accuracy",
    "допуск": "tolerance",
    "размер": "size",
    "диаметр": "diameter",
    "длина": "length",
    "ширина": "width",
    "высота": "height",
    "толщина": "thickness",
}


class TranslationService:
    """Service for translation using OpenRouter with technical glossary"""
    
    def __init__(self):
        self.openrouter_service = OpenRouterService()
        self.glossary = TECHNICAL_GLOSSARY
    
    def is_available(self) -> bool:
        """Check if translation service is available"""
        return self.openrouter_service.is_available()
    
    async def translate(
        self,
        text: str,
        from_lang: str = "ru",
        to_lang: str = "en",
        use_glossary: bool = True,
        model: Optional[str] = None,
        temperature: float = 0.3
    ) -> str:
        """
        Translate text using OpenRouter with technical glossary
        """
        if not self.is_available():
            raise ValueError("OpenRouter API key not configured. Set OPENROUTER_API_KEY in environment variables.")
        
        # Translate using OpenRouter
        translated = await self.openrouter_service.translate_text(
            text=text,
            target_language=to_lang,
            model=model,
            use_glossary=use_glossary
        )
        
        if not translated:
            raise Exception("Translation failed: OpenRouter service returned empty result")
        
        return translated
