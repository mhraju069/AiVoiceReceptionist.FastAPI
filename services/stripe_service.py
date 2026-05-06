"""
Stripe service for creating payment links for new contacts.
"""
import httpx
from config import STRIPE_SECRET_KEY, STRIPE_PRICE_ID


async def create_stripe_payment_link(
    customer_email: str,
    customer_name: str,
    booking_slot: str,
    call_summary: str,
    calendar_id: str = "",
    customer_phone: str = "",
) -> str:
    """
    Creates a Stripe Checkout payment link for a new contact.
    Embeds all booking metadata so it's fully accessible in the webhook after payment.
    Returns the Stripe Checkout URL.
    """
    if not STRIPE_SECRET_KEY:
        raise ValueError("STRIPE_SECRET_KEY not configured")
    if not STRIPE_PRICE_ID:
        raise ValueError("STRIPE_PRICE_ID not configured")

    url = "https://api.stripe.com/v1/checkout/sessions"
    headers = {
        "Authorization": f"Bearer {STRIPE_SECRET_KEY}",
        "Content-Type": "application/x-www-form-urlencoded",
    }

    # Truncate summary to stay within Stripe metadata 500-char limit per value
    summary_short = call_summary[:490] if call_summary else ""

    data = {
        "mode": "payment",
        "customer_email": customer_email,
        "line_items[0][price]": STRIPE_PRICE_ID,
        "line_items[0][quantity]": "1",
        "success_url": "https://vocaai.com/booking-success?session_id={CHECKOUT_SESSION_ID}",
        "cancel_url": "https://vocaai.com/booking-cancelled",
        # All metadata fields for the webhook to consume
        "metadata[customer_name]": customer_name,
        "metadata[customer_email]": customer_email,
        "metadata[customer_phone]": customer_phone,
        "metadata[booking_slot]": booking_slot,
        "metadata[calendar_id]": calendar_id,
        "metadata[call_summary]": summary_short,
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(url, headers=headers, data=data)
        if response.status_code != 200:
            raise Exception(f"Stripe error {response.status_code}: {response.text}")
        session = response.json()
        print(f"💳 [Stripe] Created payment link: {session['url']}")
        return session["url"]
