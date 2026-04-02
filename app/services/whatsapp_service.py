"""
WhatsApp Service for sending notifications via Meta WhatsApp Business Cloud API.
"""
from typing import Optional
from datetime import datetime
import logging
import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class WhatsAppService:
    """
    Service for sending WhatsApp messages via Meta WhatsApp Business Cloud API.
    Sends interactive button messages for visitor approval/rejection.
    """

    def __init__(self):
        self.enabled = settings.whatsapp_enabled
        self.api_url = settings.whatsapp_api_url
        self.access_token = settings.whatsapp_access_token
        self.phone_number_id = settings.whatsapp_phone_number_id

        if self.enabled and not (self.access_token and self.phone_number_id):
            logger.warning("WhatsApp is enabled but credentials are missing")
            self.enabled = False
        elif self.enabled:
            logger.info("WhatsApp service initialized successfully")
        else:
            logger.warning("WhatsApp service is disabled")

    def _format_phone_for_whatsapp(self, phone_number: str) -> str:
        """
        Format phone number for WhatsApp API (digits only, no + prefix).
        E.g., "9876543210" -> "919876543210", "+919876543210" -> "919876543210"
        """
        if not phone_number:
            return ""

        digits = ''.join(filter(str.isdigit, phone_number))

        if digits.startswith('0'):
            digits = digits[1:]

        if len(digits) == 10:
            return f"91{digits}"
        elif len(digits) == 12 and digits.startswith('91'):
            return digits
        else:
            return digits

    def _get_headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _get_messages_url(self) -> str:
        return f"{self.api_url}/{self.phone_number_id}/messages"

    DEFAULT_VISITOR_IMAGE = "https://visitor-selfie-image.s3.ap-south-1.amazonaws.com/default-visitor.jpg"

    def send_visitor_approval_request(
        self,
        to_phone: str,
        visitor_name: str,
        visitor_mobile: str,
        visitor_email: Optional[str],
        visitor_company: Optional[str],
        reason_for_visit: str,
        visitor_id: str,
        warehouse: Optional[str] = None,
        person_to_meet_name: Optional[str] = None,
        visitor_image_url: Optional[str] = None,
    ) -> bool:
        """
        Send WhatsApp template message with Approve/Reject buttons to approver.

        Uses pre-approved 'visitor_approval_emp' template:
          header  = visitor selfie image
          {{1}} = Visitor Name, {{2}} = Company, {{3}} = Purpose
          {{4}} = Time,         {{5}} = Reference/ID
          Buttons: Approve (approve_{visitor_id}), Reject (reject_{visitor_id})
        """
        if not self.enabled:
            logger.warning("WhatsApp service is disabled")
            return False

        try:
            formatted_to = self._format_phone_for_whatsapp(to_phone)
            current_time = datetime.now().strftime("%I:%M %p")
            header_img = visitor_image_url or self.DEFAULT_VISITOR_IMAGE

            payload = {
                "messaging_product": "whatsapp",
                "to": formatted_to,
                "type": "template",
                "template": {
                    "name": "visitor_approval_emp",
                    "language": {"code": "en"},
                    "components": [
                        {
                            "type": "header",
                            "parameters": [
                                {"type": "image", "image": {"link": header_img}},
                            ],
                        },
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": visitor_name},
                                {"type": "text", "text": visitor_company or "Not specified"},
                                {"type": "text", "text": reason_for_visit},
                                {"type": "text", "text": current_time},
                                {"type": "text", "text": visitor_id},
                            ],
                        },
                        {
                            "type": "button",
                            "sub_type": "quick_reply",
                            "index": "0",
                            "parameters": [
                                {"type": "payload", "payload": f"approve_{visitor_id}"},
                            ],
                        },
                        {
                            "type": "button",
                            "sub_type": "quick_reply",
                            "index": "1",
                            "parameters": [
                                {"type": "payload", "payload": f"reject_{visitor_id}"},
                            ],
                        },
                    ],
                },
            }

            logger.info(f"Sending WhatsApp template to {formatted_to} for visitor {visitor_id}")

            with httpx.Client(timeout=10) as client:
                response = client.post(
                    self._get_messages_url(),
                    headers=self._get_headers(),
                    json=payload,
                )

            if response.status_code == 200:
                data = response.json()
                message_id = data.get("messages", [{}])[0].get("id", "unknown")
                logger.info(f"WhatsApp template sent successfully. Message ID: {message_id}")
                return True
            else:
                logger.error(f"WhatsApp API error: {response.status_code} - {response.text}")
                return False

        except httpx.TimeoutException:
            logger.error(f"WhatsApp API timeout sending to {to_phone}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending WhatsApp message: {e}")
            return False

    def send_text_message(self, to_phone: str, text: str) -> bool:
        """Send a plain text WhatsApp message (for confirmations)."""
        if not self.enabled:
            return False

        try:
            formatted_to = self._format_phone_for_whatsapp(to_phone)
            payload = {
                "messaging_product": "whatsapp",
                "to": formatted_to,
                "type": "text",
                "text": {"body": text},
            }

            with httpx.Client(timeout=10) as client:
                response = client.post(
                    self._get_messages_url(),
                    headers=self._get_headers(),
                    json=payload,
                )

            if response.status_code == 200:
                logger.info(f"WhatsApp text sent to {formatted_to}")
                return True
            else:
                logger.error(f"WhatsApp text error: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"Error sending WhatsApp text: {e}")
            return False


# Singleton instance
whatsapp_service = WhatsAppService()
