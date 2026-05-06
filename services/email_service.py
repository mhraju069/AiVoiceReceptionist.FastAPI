"""
Email notification service using SMTP.
Sends confirmation and Stripe payment emails to callers.
"""
import smtplib
import asyncio
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM


def _send_email_sync(to_email: str, subject: str, html_body: str):
    """Synchronous SMTP email send (run in thread executor)."""
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = SMTP_FROM
    msg["To"] = to_email
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM, to_email, msg.as_string())

    print(f"📧 [Email] Sent '{subject}' to {to_email}")


async def send_email(to_email: str, subject: str, html_body: str):
    """Async wrapper for sending emails."""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _send_email_sync, to_email, subject, html_body)


async def send_booking_confirmation(
    to_email: str,
    contact_name: str,
    booking_date: str,
    booking_time: str,
    call_summary: str,
):
    """Send appointment confirmation email to an existing contact."""
    subject = "✅ Your Appointment is Confirmed — Voca AI"
    html_body = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; background: #f4f4f4; padding: 20px;">
  <div style="max-width: 600px; margin: auto; background: white; border-radius: 12px; padding: 32px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
    <h2 style="color: #6B3FA0;">🎉 Appointment Confirmed!</h2>
    <p>Dear <strong>{contact_name}</strong>,</p>
    <p>Your appointment with Voca AI has been successfully booked.</p>

    <div style="background: #f0eaff; border-radius: 8px; padding: 16px; margin: 20px 0;">
      <p><strong>📅 Date:</strong> {booking_date}</p>
      <p><strong>🕐 Time:</strong> {booking_time}</p>
    </div>

    <h3 style="color: #6B3FA0;">📞 Call Summary:</h3>
    <div style="background: #f9f9f9; border-left: 4px solid #6B3FA0; padding: 12px; border-radius: 4px;">
      <p style="color: #444;">{call_summary}</p>
    </div>

    <p style="margin-top: 24px; color: #888; font-size: 12px;">
      Thank you for choosing Voca AI. If you have any questions, please feel free to contact us.
    </p>
  </div>
</body>
</html>
"""
    await send_email(to_email, subject, html_body)


async def send_stripe_payment_link(
    to_email: str,
    contact_name: str,
    payment_url: str,
    call_summary: str,
):
    """Send a Stripe payment link email to a new contact."""
    subject = "💳 Complete Your Booking Payment — Voca AI"
    html_body = f"""
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"></head>
<body style="font-family: Arial, sans-serif; background: #f4f4f4; padding: 20px;">
  <div style="max-width: 600px; margin: auto; background: white; border-radius: 12px; padding: 32px; box-shadow: 0 4px 12px rgba(0,0,0,0.1);">
    <h2 style="color: #6B3FA0;">👋 We Received Your Appointment Request!</h2>
    <p>Dear <strong>{contact_name}</strong>,</p>
    <p>You recently spoke with our AI receptionist. To confirm your appointment, please complete the payment using the link below.</p>

    <h3 style="color: #6B3FA0;">📞 Call Summary:</h3>
    <div style="background: #f9f9f9; border-left: 4px solid #6B3FA0; padding: 12px; border-radius: 4px; margin-bottom: 24px;">
      <p style="color: #444;">{call_summary}</p>
    </div>

    <div style="text-align: center; margin: 28px 0;">
      <a href="{payment_url}"
         style="background: linear-gradient(135deg, #6B3FA0, #9B59B6); color: white; padding: 14px 32px;
                border-radius: 50px; text-decoration: none; font-size: 16px; font-weight: bold;">
        💳 Pay Now
      </a>
    </div>

    <p style="color: #888; font-size: 12px;">
      Once payment is complete, your appointment will be automatically added to our system and a confirmation email will be sent to you.
    </p>
  </div>
</body>
</html>
"""
    await send_email(to_email, subject, html_body)
