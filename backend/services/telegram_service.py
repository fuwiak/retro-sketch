"""
Telegram Service for sending notifications and handling webhook callbacks
"""
import os
import time
import httpx
from typing import Dict, Optional, List
from services.logger import api_logger

TELEGRAM_API_BASE = "https://api.telegram.org/bot"


class TelegramService:
    """Service for Telegram bot operations"""
    
    def __init__(self):
        self.api_base = TELEGRAM_API_BASE
    
    def send_message(
        self,
        bot_token: str,
        chat_id: str,
        message: str,
        show_approval: bool = False,
        message_id: Optional[str] = None
    ) -> Dict:
        """Send message to Telegram chat"""
        try:
            url = f"{self.api_base}{bot_token}/sendMessage"
            
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            
            if show_approval:
                callback_data = message_id or str(int(time.time()))
                payload["reply_markup"] = {
                    "inline_keyboard": [
                        [
                            {"text": "✅ Approve", "callback_data": f"approve_{callback_data}"},
                            {"text": "❌ Reject", "callback_data": f"reject_{callback_data}"}
                        ]
                    ]
                }
            
            response = httpx.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            api_logger.error(f"Error sending Telegram message: {str(e)}")
            raise
    
    def send_document(
        self,
        bot_token: str,
        chat_id: str,
        file_path: str,
        caption: str = "",
        show_approval: bool = False,
        message_id: Optional[str] = None
    ) -> Dict:
        """Send document to Telegram chat"""
        try:
            url = f"{self.api_base}{bot_token}/sendDocument"
            
            with open(file_path, "rb") as f:
                files = {"document": f}
                data = {
                    "chat_id": chat_id,
                    "caption": caption
                }
                
                if show_approval:
                    callback_data = message_id or str(int(time.time()))
                    data["reply_markup"] = {
                        "inline_keyboard": [
                            [
                                {"text": "✅ Approve", "callback_data": f"approve_{callback_data}"},
                                {"text": "❌ Reject", "callback_data": f"reject_{callback_data}"}
                            ]
                        ]
                    }
                
                response = httpx.post(url, data=data, files=files, timeout=30)
                response.raise_for_status()
                return response.json()
        except Exception as e:
            api_logger.error(f"Error sending Telegram document: {str(e)}")
            raise
    
    def answer_callback_query(
        self,
        bot_token: str,
        callback_query_id: str,
        text: str = "",
        show_alert: bool = False
    ) -> Dict:
        """Answer callback query (button click)"""
        try:
            url = f"{self.api_base}{bot_token}/answerCallbackQuery"
            
            payload = {
                "callback_query_id": callback_query_id,
                "text": text,
                "show_alert": show_alert
            }
            
            response = httpx.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            api_logger.error(f"Error answering callback query: {str(e)}")
            raise
    
    def edit_message_reply_markup(
        self,
        bot_token: str,
        chat_id: str,
        message_id: int,
        new_text: Optional[str] = None
    ) -> Dict:
        """Edit message to remove buttons after approval/rejection"""
        try:
            url = f"{self.api_base}{bot_token}/editMessageText"
            
            payload = {
                "chat_id": chat_id,
                "message_id": message_id
            }
            
            if new_text:
                payload["text"] = new_text
                payload["parse_mode"] = "HTML"
            else:
                # Just remove buttons
                payload["reply_markup"] = None
            
            response = httpx.post(url, json=payload, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            api_logger.error(f"Error editing message: {str(e)}")
            raise
    
    def format_review_message(
        self,
        extracted_data: Dict,
        translations: Dict,
        steel_equivalents: Optional[Dict] = None
    ) -> str:
        """Format extracted data as review message"""
        message = "<b>📐 Drawing Analysis Draft</b>\n\n"
        
        message += "<b>Extracted Data:</b>\n"
        message += f"Materials: {', '.join(translations.get('materials', [])) or 'N/A'}\n"
        message += f"Standards: {', '.join(translations.get('standards', [])) or 'N/A'}\n"
        message += f"Surface Roughness: Ra {', '.join(map(str, extracted_data.get('raValues', []))) or 'N/A'}\n"
        message += f"Fits: {', '.join(extracted_data.get('fits', [])) or 'N/A'}\n"
        message += f"Heat Treatment: {', '.join(translations.get('heatTreatment', [])) or 'N/A'}\n"
        
        if steel_equivalents:
            message += "\n<b>Steel Equivalents:</b>\n"
            for material, equiv in steel_equivalents.items():
                if isinstance(equiv, dict):
                    message += f"{material}:\n"
                    if equiv.get('astm'):
                        message += f"  ASTM: {equiv['astm']}\n"
                    if equiv.get('iso'):
                        message += f"  ISO: {equiv['iso']}\n"
                    if equiv.get('gbt'):
                        message += f"  GB/T: {equiv['gbt']}\n"
        
        message += "\n<i>Please review and approve or reject this draft.</i>"
        
        return message

