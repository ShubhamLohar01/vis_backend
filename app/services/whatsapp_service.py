"""
WhatsApp Service for sending notifications via Meta WhatsApp Business Cloud API.
"""
from typing import Optional
from datetime import datetime
from zoneinfo import ZoneInfo
import logging
import httpx

from app.core.config import settings

# All timestamps shown to Indian visitors/approvers should be in IST regardless
# of where the server runs (Lambda is UTC by default).
IST = ZoneInfo("Asia/Kolkata")

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

    def upload_media(self, image_bytes: bytes, content_type: str = "image/jpeg") -> Optional[str]:
        """
        Upload image bytes directly to WhatsApp Media API.
        Returns media_id string, or None on failure.
        """
        if not self.enabled:
            return None
        try:
            media_url = f"{self.api_url}/{self.phone_number_id}/media"
            with httpx.Client(timeout=30) as client:
                response = client.post(
                    media_url,
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    files={"file": ("selfie.jpg", image_bytes, content_type)},
                    data={"messaging_product": "whatsapp", "type": content_type},
                )
            if response.status_code == 200:
                media_id = response.json().get("id")
                logger.info(f"WhatsApp media uploaded. media_id: {media_id}")
                return media_id
            else:
                logger.error(f"WhatsApp media upload failed: {response.status_code} - {response.text}")
                return None
        except Exception as e:
            logger.error(f"WhatsApp media upload error: {e}")
            return None

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
        image_bytes: Optional[bytes] = None,
        image_content_type: str = "image/jpeg",
        visitor_image_url: Optional[str] = None,
    ) -> bool:
        """
        Send visitor_approval_emp template with Approve/Reject buttons.

        Image priority:
          1. image_bytes → uploaded to WhatsApp Media API → media_id used in header
          2. visitor_image_url → used as link in header
          3. DEFAULT_VISITOR_IMAGE fallback
        """
        if not self.enabled:
            logger.warning("WhatsApp service is disabled")
            return False

        try:
            formatted_to = self._format_phone_for_whatsapp(to_phone)
            # Use IST so the time displayed matches the visitor's local time,
            # not the Lambda runtime's UTC clock.
            current_time = datetime.now(IST).strftime("%I:%M %p")

            # Build header image component
            if image_bytes:
                media_id = self.upload_media(image_bytes, image_content_type)
                if media_id:
                    header_param = {"type": "image", "image": {"id": media_id}}
                else:
                    # fallback to URL if media upload fails
                    fallback = visitor_image_url or self.DEFAULT_VISITOR_IMAGE
                    header_param = {"type": "image", "image": {"link": fallback}}
            elif visitor_image_url:
                header_param = {"type": "image", "image": {"link": visitor_image_url}}
            else:
                header_param = {"type": "image", "image": {"link": self.DEFAULT_VISITOR_IMAGE}}

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
                            "parameters": [header_param],
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
                            "type": "button", "sub_type": "quick_reply", "index": "0",
                            "parameters": [{"type": "payload", "payload": f"approve_{visitor_id}"}],
                        },
                        {
                            "type": "button", "sub_type": "quick_reply", "index": "1",
                            "parameters": [{"type": "payload", "payload": f"reject_{visitor_id}"}],
                        },
                    ],
                },
            }

            logger.info(f"Sending visitor_approval_emp to {formatted_to} for visitor {visitor_id}")

            with httpx.Client(timeout=10) as client:
                response = client.post(
                    self._get_messages_url(),
                    headers=self._get_headers(),
                    json=payload,
                )

            if response.status_code == 200:
                msg_id = response.json().get("messages", [{}])[0].get("id", "unknown")
                logger.info(f"WhatsApp template sent. Message ID: {msg_id}")
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

    def send_approval_notification(self, to_phone: str, visitor_id_str: str) -> bool:
        """Send visitor_approved template to visitor when their request is approved."""
        if not self.enabled:
            return False
        try:
            formatted_to = self._format_phone_for_whatsapp(to_phone)
            payload = {
                "messaging_product": "whatsapp",
                "to": formatted_to,
                "type": "template",
                "template": {
                    "name": "visitor_approved",
                    "language": {"code": "en"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": visitor_id_str}],
                        }
                    ],
                },
            }
            with httpx.Client(timeout=10) as client:
                response = client.post(self._get_messages_url(), headers=self._get_headers(), json=payload)
            if response.status_code == 200:
                logger.info(f"visitor_approved template sent to {formatted_to}")
                return True
            else:
                logger.error(f"visitor_approved error: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error sending approval notification: {e}")
            return False

    def send_rejection_notification(self, to_phone: str, visitor_id_str: str) -> bool:
        """Send visitor_rejected template to visitor when their request is rejected."""
        if not self.enabled:
            return False
        try:
            formatted_to = self._format_phone_for_whatsapp(to_phone)
            payload = {
                "messaging_product": "whatsapp",
                "to": formatted_to,
                "type": "template",
                "template": {
                    "name": "visitor_rejected",
                    "language": {"code": "en"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": visitor_id_str}],
                        }
                    ],
                },
            }
            with httpx.Client(timeout=10) as client:
                response = client.post(self._get_messages_url(), headers=self._get_headers(), json=payload)
            if response.status_code == 200:
                logger.info(f"visitor_rejected template sent to {formatted_to}")
                return True
            else:
                logger.error(f"visitor_rejected error: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error sending rejection notification: {e}")
            return False

    def send_otp_notification(self, to_phone: str, otp_code: str) -> bool:
        """Send visitor_revisit_otp template with COPY_CODE button for revisit verification."""
        if not self.enabled:
            return False
        try:
            formatted_to = self._format_phone_for_whatsapp(to_phone)
            payload = {
                "messaging_product": "whatsapp",
                "to": formatted_to,
                "type": "template",
                "template": {
                    "name": "visitor_revisit_otp",
                    "language": {"code": "en_US"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [{"type": "text", "text": otp_code}],
                        },
                        {
                            "type": "button",
                            "sub_type": "url",
                            "index": "0",
                            "parameters": [{"type": "text", "text": otp_code}],
                        },
                    ],
                },
            }
            with httpx.Client(timeout=10) as client:
                response = client.post(self._get_messages_url(), headers=self._get_headers(), json=payload)
            if response.status_code == 200:
                logger.info(f"visitor_revisit_otp sent to {formatted_to}")
                return True
            else:
                logger.error(f"visitor_revisit_otp error: {response.status_code} - {response.text}")
                return False
        except Exception as e:
            logger.error(f"Error sending OTP notification: {e}")
            return False

    def send_appointment_approval_request(
        self,
        to_phone: str,
        visitor_name: str,
        company: Optional[str],
        purpose: str,
        date_of_visit: Optional[str],
        time_slot: Optional[str],
        visitor_id: str,
    ) -> bool:
        """
        Send appointment_approval_emp template to an approver with Approve/Reject buttons.
        Body params: {{1}} visitor_name, {{2}} company, {{3}} purpose, {{4}} date, {{5}} time, {{6}} visitor_id
        Quick-reply payloads use the same approve_<id>/reject_<id> format as walk-ins,
        so the existing webhook handler works unchanged.
        """
        if not self.enabled:
            return False
        try:
            formatted_to = self._format_phone_for_whatsapp(to_phone)
            payload = {
                "messaging_product": "whatsapp",
                "to": formatted_to,
                "type": "template",
                "template": {
                    "name": "appointment_approval_emp",
                    "language": {"code": "en"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": visitor_name},
                                {"type": "text", "text": company or "Not specified"},
                                {"type": "text", "text": purpose},
                                {"type": "text", "text": date_of_visit or "Not specified"},
                                {"type": "text", "text": time_slot or "Not specified"},
                                {"type": "text", "text": visitor_id},
                            ],
                        },
                        {
                            "type": "button", "sub_type": "quick_reply", "index": "0",
                            "parameters": [{"type": "payload", "payload": f"approve_{visitor_id}"}],
                        },
                        {
                            "type": "button", "sub_type": "quick_reply", "index": "1",
                            "parameters": [{"type": "payload", "payload": f"reject_{visitor_id}"}],
                        },
                    ],
                },
            }
            with httpx.Client(timeout=10) as client:
                response = client.post(self._get_messages_url(), headers=self._get_headers(), json=payload)
            if response.status_code == 200:
                logger.info(f"appointment_approval_emp sent to {formatted_to} for visitor {visitor_id}")
                return True
            logger.error(f"appointment_approval_emp error: {response.status_code} - {response.text}")
            return False
        except Exception as e:
            logger.error(f"Error sending appointment_approval_emp: {e}")
            return False

    def send_appointment_approved_notification(
        self,
        to_phone: str,
        visitor_name: str,
        date_of_visit: Optional[str],
        time_slot: Optional[str],
        qr_code: str,
        visitor_id_str: str,
    ) -> bool:
        """
        Send appointment_approved template to visitor confirming their appointment.
        Body params: {{1}} visitor_name, {{2}} date, {{3}} time, {{4}} qr_code, {{5}} visitor_id_str
        """
        if not self.enabled:
            return False
        try:
            formatted_to = self._format_phone_for_whatsapp(to_phone)
            payload = {
                "messaging_product": "whatsapp",
                "to": formatted_to,
                "type": "template",
                "template": {
                    "name": "appointment_approved",
                    "language": {"code": "en"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": visitor_name},
                                {"type": "text", "text": date_of_visit or "TBD"},
                                {"type": "text", "text": time_slot or "TBD"},
                                {"type": "text", "text": qr_code},
                                {"type": "text", "text": visitor_id_str},
                            ],
                        }
                    ],
                },
            }
            with httpx.Client(timeout=10) as client:
                response = client.post(self._get_messages_url(), headers=self._get_headers(), json=payload)
            if response.status_code == 200:
                logger.info(f"appointment_approved sent to {formatted_to}")
                return True
            logger.error(f"appointment_approved error: {response.status_code} - {response.text}")
            return False
        except Exception as e:
            logger.error(f"Error sending appointment_approved: {e}")
            return False

    def send_appointment_rejected_notification(
        self,
        to_phone: str,
        visitor_name: str,
        date_of_visit: Optional[str],
        time_slot: Optional[str],
        rejection_reason: Optional[str],
        visitor_id_str: str,
    ) -> bool:
        """
        Send appointment_rejected template to visitor.
        Body params: {{1}} visitor_name, {{2}} date, {{3}} time, {{4}} rejection_reason, {{5}} visitor_id_str
        """
        if not self.enabled:
            return False
        try:
            formatted_to = self._format_phone_for_whatsapp(to_phone)
            payload = {
                "messaging_product": "whatsapp",
                "to": formatted_to,
                "type": "template",
                "template": {
                    "name": "appointment_rejected",
                    "language": {"code": "en"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": visitor_name},
                                {"type": "text", "text": date_of_visit or "Not specified"},
                                {"type": "text", "text": time_slot or "Not specified"},
                                {"type": "text", "text": rejection_reason or "No reason provided"},
                                {"type": "text", "text": visitor_id_str},
                            ],
                        }
                    ],
                },
            }
            with httpx.Client(timeout=10) as client:
                response = client.post(self._get_messages_url(), headers=self._get_headers(), json=payload)
            if response.status_code == 200:
                logger.info(f"appointment_rejected sent to {formatted_to}")
                return True
            logger.error(f"appointment_rejected error: {response.status_code} - {response.text}")
            return False
        except Exception as e:
            logger.error(f"Error sending appointment_rejected: {e}")
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
