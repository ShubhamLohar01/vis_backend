"""
Email Services
Handles sending emails for appointment QR codes
"""
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from typing import Optional
import io
import qrcode
from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailService:
    def __init__(self):
        self.enabled = settings.email_enabled
        # Support both SMTP_SERVER and SMTP_HOST style config
        self.smtp_host = getattr(settings, "smtp_server", None) or getattr(settings, "smtp_host", None)
        self.smtp_port = settings.smtp_port
        # Support both SMTP_USERNAME and SMTP_USER style config
        self.smtp_user = getattr(settings, "smtp_username", None) or getattr(settings, "smtp_user", None)
        self.smtp_password = settings.smtp_password
        self.from_email = settings.email_from
        
    def generate_qr_code_image(self, qr_code: str) -> bytes:
        """Generate QR code image as bytes"""
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_code)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        img_bytes = io.BytesIO()
        img.save(img_bytes, format='PNG')
        img_bytes.seek(0)
        return img_bytes.getvalue()
    
    def send_appointment_qr(
        self,
        to_email: str,
        visitor_name: str,
        qr_code: str,
        visitor_number: Optional[str] = None,
        appointment_date: Optional[str] = None,
        appointment_time: Optional[str] = None,
        approver_name: Optional[str] = None
    ) -> bool:
        """
        Send appointment QR code via email
        
        Args:
            to_email: Visitor's email address
            visitor_name: Visitor's name
            qr_code: Unique QR code identifier
            appointment_date: Date of appointment
            appointment_time: Time slot of appointment
            
        Returns:
            True if email sent successfully, False otherwise
        """
        if not self.enabled:
            logger.warning("Email service is disabled. Set EMAIL_ENABLED=true to enable.")
            return False
            
        if not self.smtp_host or not self.smtp_port:
            logger.warning(f"SMTP host/port not configured (host={self.smtp_host}, port={self.smtp_port}). Email not sent.")
            return False

        if not self.smtp_user or not self.smtp_password:
            # Don't log passwords; just indicate what's missing.
            logger.warning(
                f"SMTP credentials not configured (user_set={bool(self.smtp_user)}, password_set={bool(self.smtp_password)}). Email not sent."
            )
            return False
        
        try:
            # Generate QR code image
            qr_image = self.generate_qr_code_image(qr_code)
            
            # Create email message
            msg = MIMEMultipart('related')
            # IMPORTANT: From address must match SMTP username for authentication
            # Use SMTP username as From address, but display as "Candor Foods"
            # Format: "Display Name <email@domain.com>"
            from_address = self.smtp_user if self.smtp_user else (self.from_email if self.from_email else "erp@candorfoods.in")
            msg['From'] = f"Candor Foods <{from_address}>"
            msg['To'] = to_email
            msg['Subject'] = "Your Appointment QR Code - Candor Foods"
            
            logger.info(f"[Email] Sending from: {from_address}, to: {to_email}, SMTP user: {self.smtp_user}")
            
            # Email body with professional Candor Foods branding
            html_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; margin: 0; padding: 0; background-color: #f5f5f5;">
                <div style="max-width: 600px; margin: 0 auto; padding: 0; background-color: #ffffff;">
                    <!-- Header -->
                    <div style="background: linear-gradient(135deg, #f97316 0%, #ea580c 100%); padding: 30px 20px; text-align: center;">
                        <h1 style="color: #ffffff; margin: 0; font-size: 28px; font-weight: bold;">Candor Foods</h1>
                        <p style="color: #ffffff; margin: 5px 0 0 0; font-size: 14px;">Visitor Management System</p>
                    </div>
                    
                    <!-- Content -->
                    <div style="padding: 30px 20px;">
                        <h2 style="color: #f97316; margin-top: 0; font-size: 24px;">Appointment Approved!</h2>
                        
                        <p style="font-size: 16px; margin-bottom: 20px;">Dear <strong>{visitor_name}</strong>,</p>
                        
                        <p style="font-size: 16px; margin-bottom: 20px;">
                            We are pleased to inform you that your appointment request has been <strong style="color: #16a34a;">approved</strong>{" by " + approver_name if approver_name else ""}.
                        </p>
                        
                        <div style="background-color: #fef3c7; border-left: 4px solid #f59e0b; padding: 15px; margin: 20px 0; border-radius: 4px;">
                            <p style="margin: 0; font-size: 14px; color: #92400e;">
                                <strong>Important:</strong> Please bring the QR code and your Visitor Number when you visit our premises.
                            </p>
                        </div>
                        
                        <!-- QR Code Section -->
                        <div style="background-color: #f9fafb; padding: 25px; border-radius: 8px; margin: 25px 0; text-align: center; border: 2px solid #e5e7eb;">
                            <p style="font-weight: bold; font-size: 16px; margin-bottom: 15px; color: #111827;">Your QR Code:</p>
                            <img src="cid:qrcode" alt="QR Code" style="max-width: 250px; height: auto; border: 3px solid #f97316; border-radius: 8px; padding: 10px; background-color: #ffffff;" />
                            <p style="margin-top: 15px; font-size: 13px; color: #6b7280; font-family: monospace; word-break: break-all;">{qr_code}</p>
                        </div>
                        
                        <!-- Visitor Number Section -->
                        {f'''
                        <div style="background-color: #eff6ff; padding: 20px; border-radius: 8px; margin: 20px 0; border: 2px solid #3b82f6;">
                            <p style="font-weight: bold; font-size: 16px; margin-bottom: 10px; color: #1e40af;">Your Visitor Number:</p>
                            <p style="font-size: 24px; font-weight: bold; color: #1e40af; font-family: monospace; letter-spacing: 2px; margin: 0;">{visitor_number}</p>
                            <p style="font-size: 12px; color: #6b7280; margin-top: 8px;">Please keep this number for your records</p>
                        </div>
                        ''' if visitor_number else ''}
                        
                        <!-- Appointment Details -->
                        <div style="background-color: #f9fafb; padding: 20px; border-radius: 8px; margin: 20px 0;">
                            <p style="font-weight: bold; font-size: 16px; margin-bottom: 15px; color: #111827;">Appointment Details:</p>
                            {"<p style='margin: 8px 0;'><strong style='color: #6b7280;'>Date:</strong> <span style='color: #111827;'>" + appointment_date + "</span></p>" if appointment_date else ""}
                            {"<p style='margin: 8px 0;'><strong style='color: #6b7280;'>Time:</strong> <span style='color: #111827;'>" + appointment_time + "</span></p>" if appointment_time else ""}
                        </div>
                        
                        <!-- Instructions -->
                        <div style="margin: 25px 0;">
                            <p style="font-weight: bold; font-size: 16px; margin-bottom: 12px; color: #111827;">📋 Instructions for Your Visit:</p>
                            <ul style="margin: 0; padding-left: 20px; color: #374151;">
                                <li style="margin-bottom: 8px;">Please bring this QR code (either printed or on your mobile device)</li>
                                <li style="margin-bottom: 8px;">Show the QR code at the gate for scanning</li>
                                <li style="margin-bottom: 8px;">Keep your Visitor Number handy: <strong style="color: #f97316;">{visitor_number if visitor_number else 'N/A'}</strong></li>
                                <li style="margin-bottom: 8px;">Arrive on time for your scheduled appointment</li>
                                <li>Keep this email accessible on your mobile device</li>
                            </ul>
                        </div>
                        
                        <div style="border-top: 2px solid #e5e7eb; padding-top: 20px; margin-top: 30px;">
                            <p style="margin: 0; font-size: 14px; color: #6b7280;">
                                If you have any questions or need to reschedule, please contact us in advance.
                            </p>
                        </div>
                    </div>
                    
                    <!-- Footer -->
                    <div style="background-color: #f9fafb; padding: 20px; text-align: center; border-top: 1px solid #e5e7eb;">
                        <p style="margin: 0; font-size: 14px; color: #6b7280; font-weight: bold;">Candor Foods</p>
                        <p style="margin: 5px 0 0 0; font-size: 12px; color: #9ca3af;">
                            This is an automated message sent from erp@candorfoods.in
                        </p>
                        <p style="margin: 10px 0 0 0; font-size: 11px; color: #9ca3af;">
                            Please do not reply to this email. For inquiries, please contact us directly.
                        </p>
                    </div>
                </div>
            </body>
            </html>
            """
            
            # Attach HTML body
            msg.attach(MIMEText(html_body, 'html'))
            
            # Attach QR code image
            qr_attachment = MIMEImage(qr_image)
            qr_attachment.add_header('Content-ID', '<qrcode>')
            qr_attachment.add_header('Content-Disposition', 'inline', filename='qrcode.png')
            msg.attach(qr_attachment)
            
            # Send email
            logger.info(f"[Email] Connecting to SMTP server: {self.smtp_host}:{self.smtp_port}")
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                logger.info("[Email] Starting TLS...")
                server.starttls()
                
                # Login with SMTP credentials
                if not self.smtp_user or not self.smtp_password:
                    logger.error("[Email] SMTP username or password not configured")
                    return False
                
                try:
                    logger.info(f"[Email] Attempting to authenticate with user: {self.smtp_user}")
                    server.login(self.smtp_user, self.smtp_password)
                    logger.info("[Email] SMTP authentication successful")
                except smtplib.SMTPAuthenticationError as e:
                    logger.error(f"[Email] SMTP authentication failed: {e}")
                    logger.error("[Email] Troubleshooting steps:")
                    logger.error("  1. Verify SMTP_USERNAME and SMTP_PASSWORD in .env file")
                    logger.error("  2. For Gmail: Use App Password (not regular password)")
                    logger.error("     - Go to: https://myaccount.google.com/apppasswords")
                    logger.error("     - Generate App Password for 'Mail'")
                    logger.error("  3. Ensure SMTP_USERNAME matches the authenticated email")
                    logger.error(f"  4. Current SMTP_USERNAME: {self.smtp_user}")
                    logger.error(f"  5. Current SMTP_HOST: {self.smtp_host}:{self.smtp_port}")
                    return False
                except Exception as e:
                    logger.error(f"[Email] Error during SMTP login: {e}")
                    return False
                
                # Send the message
                try:
                    logger.info(f"[Email] Sending email message to {to_email}...")
                    server.send_message(msg)
                    logger.info(f"[Email] ✓ Email sent successfully to {to_email}")
                except Exception as e:
                    logger.error(f"[Email] Failed to send email message: {e}")
                    return False
            
            return True
            
        except smtplib.SMTPException as e:
            logger.error(f"[Email] SMTP error: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"[Email] Failed to send QR code email to {to_email}: {e}", exc_info=True)
            return False
    
    def send_appointment_rejection(
        self,
        to_email: str,
        visitor_name: str,
        appointment_date: Optional[str] = None,
        appointment_time: Optional[str] = None,
        rejection_reason: Optional[str] = None
    ) -> bool:
        """
        Send polite rejection email for appointment
        
        Args:
            to_email: Visitor's email address
            visitor_name: Visitor's name
            appointment_date: Date of appointment
            appointment_time: Time slot of appointment
            rejection_reason: Reason for rejection (optional)
            
        Returns:
            True if email sent successfully, False otherwise
        """
        if not self.enabled:
            logger.warning("Email service is disabled. Set EMAIL_ENABLED=true to enable.")
            return False
            
        if not self.smtp_host or not self.smtp_port:
            logger.warning(f"SMTP host/port not configured (host={self.smtp_host}, port={self.smtp_port}). Email not sent.")
            return False

        if not self.smtp_user or not self.smtp_password:
            logger.warning(
                f"SMTP credentials not configured (user_set={bool(self.smtp_user)}, password_set={bool(self.smtp_password)}). Email not sent."
            )
            return False
        
        try:
            # Create email message
            msg = MIMEMultipart('related')
            # IMPORTANT: From address must match SMTP username for authentication
            from_address = self.smtp_user if self.smtp_user else (self.from_email if self.from_email else "erp@candorfoods.in")
            msg['From'] = f"Candor Foods <{from_address}>"
            msg['To'] = to_email
            msg['Subject'] = "Appointment Request Update - Candor Foods"
            
            logger.info(f"[Email] Sending rejection email from: {from_address}, to: {to_email}")
            
            # Email body with polite rejection message
            html_body = f"""
            <html>
            <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
                <div style="max-width: 600px; margin: 0 auto; padding: 20px;">
                    <h2 style="color: #2563eb;">Appointment Request Update</h2>
                    <p>Dear {visitor_name},</p>
                    <p>Thank you for your interest in visiting Candor Foods. We appreciate you taking the time to submit an appointment request.</p>
                    
                    <div style="background-color: #fef3c7; padding: 20px; border-radius: 8px; margin: 20px 0; border-left: 4px solid #f59e0b;">
                        <p style="margin: 0; font-weight: bold; color: #92400e;">Unfortunately, we are unable to accommodate your appointment request at this time.</p>
                    </div>
                    
                    {"<p><strong>Requested Date:</strong> " + appointment_date + "</p>" if appointment_date else ""}
                    {"<p><strong>Requested Time:</strong> " + appointment_time + "</p>" if appointment_time else ""}
                    
                    {f"<p><strong>Reason:</strong> {rejection_reason}</p>" if rejection_reason else ""}
                    
                    <p style="margin-top: 20px;">We apologize for any inconvenience this may cause. If you have any questions or would like to discuss alternative arrangements, please feel free to contact us.</p>
                    
                    <p style="margin-top: 20px;">Thank you for your understanding.</p>
                    
                    <p style="margin-top: 30px;">Best regards,<br>Candor Foods Team</p>
                    
                    <p style="margin-top: 30px; color: #666; font-size: 12px;">
                        This is an automated message. Please do not reply to this email.
                    </p>
                </div>
            </body>
            </html>
            """
            
            # Attach HTML body
            msg.attach(MIMEText(html_body, 'html'))
            
            # Send email
            logger.info(f"[Email] Connecting to SMTP server: {self.smtp_host}:{self.smtp_port}")
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                logger.info("[Email] Starting TLS...")
                server.starttls()
                
                # Login with SMTP credentials
                if not self.smtp_user or not self.smtp_password:
                    logger.error("[Email] SMTP username or password not configured")
                    return False
                
                try:
                    logger.info(f"[Email] Attempting to authenticate with user: {self.smtp_user}")
                    server.login(self.smtp_user, self.smtp_password)
                    logger.info("[Email] SMTP authentication successful")
                except smtplib.SMTPAuthenticationError as e:
                    logger.error(f"[Email] SMTP authentication failed: {e}")
                    logger.error("[Email] Troubleshooting steps:")
                    logger.error("  1. Verify SMTP_USERNAME and SMTP_PASSWORD in .env file")
                    logger.error("  2. For Gmail: Use App Password (not regular password)")
                    logger.error("     - Go to: https://myaccount.google.com/apppasswords")
                    logger.error("     - Generate App Password for 'Mail'")
                    logger.error("  3. Ensure SMTP_USERNAME matches the authenticated email")
                    logger.error(f"  4. Current SMTP_USERNAME: {self.smtp_user}")
                    return False
                except Exception as e:
                    logger.error(f"[Email] Error during SMTP login: {e}")
                    return False
                
                # Send the message
                try:
                    logger.info(f"[Email] Sending rejection email to {to_email}...")
                    server.send_message(msg)
                    logger.info(f"[Email] ✓ Rejection email sent successfully to {to_email}")
                except Exception as e:
                    logger.error(f"[Email] Failed to send email message: {e}")
                    return False
            
            return True
            
        except smtplib.SMTPException as e:
            logger.error(f"[Email] SMTP error: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"[Email] Failed to send rejection email to {to_email}: {e}", exc_info=True)
            return False


email_service = EmailService()

