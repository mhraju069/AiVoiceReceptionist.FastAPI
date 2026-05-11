"""
Booking service - callable directly from WebSocket handlers.
Avoids self-HTTP round-trips that can fail in Docker networking.
"""
from config import (
    GHL_BASE_URL, GHL_LOCATION_ID, 
    CALENDAR_FOLLOW_UP_C, CALENDAR_FOLLOW_UP_B, 
    CALENDAR_VIRTUAL_CONSULT_15, CALENDAR_VIRTUAL_CPA_45, 
    CALENDAR_OFFICE_CPA_45, CALENDAR_BEAUTY_SALON_45, CALENDAR_TEST
)
import time
import httpx
from services.ghl import get_ghl_headers, add_contact, update_contact, create_appointment
from services.ghl_search import search_contact_by_phone_or_email
from services.email_service import send_booking_confirmation, send_stripe_payment_link
from services.stripe_service import create_stripe_payment_link
from schemas import ContactCreate, ContactUpdate, AppointmentCreate

# Calendar Definitions
CALENDARS = {
    "follow_up_c": {
        "id": CALENDAR_FOLLOW_UP_C or "NyTgTkNMmjyra19H68kT",
        "name": "10 Min follow up call - C",
        "price": 0, # Free for priority groups
        "public": False
    },
    "follow_up_b": {
        "id": CALENDAR_FOLLOW_UP_B or "XGl4AFDSVEujEeFvAa1W",
        "name": "10 Min follow up call B",
        "price": 0,
        "public": False
    },
    "virtual_consult_15": {
        "id": CALENDAR_VIRTUAL_CONSULT_15 or "bLqGtiE32LFGiQZZ13b9",
        "name": "Friday 15 Min Virtual Consult $",
        "price": 50, # Example price
        "public": True
    },
    "virtual_cpa_45": {
        "id": CALENDAR_VIRTUAL_CPA_45 or "v19Df4NXDWvYk039RlnW",
        "name": "45 Min SB Virtual CPA Consult - $",
        "price": 150,
        "public": True
    },
    "office_cpa_45": {
        "id": CALENDAR_OFFICE_CPA_45 or "pbIEH8PjVBhZBtuvC2Or",
        "name": "45 Min SB In-Office CPA Consult - $",
        "price": 200,
        "public": True
    },
    "beauty_salon_45": {
        "id": CALENDAR_BEAUTY_SALON_45 or "5MlN78oRANJfHqsqTPCP",
        "name": "Free Tax Consultation for Beauty Salons (45 Min)",
        "price": 0,
        "public": True
    },
    "test_calendar": {
        "id": CALENDAR_TEST or "4OIPAoMvrUMkcbRSyYiv",
        "name": "Original Test Calendar",
        "price": 0,
        "public": True
    }
}

DEFAULT_CALENDAR_ID = "XGl4AFDSVEujEeFvAa1W" # Follow up B as default
PRIORITY_GROUPS = ["Group A", "Group B", "Group C", "Group D", "A", "B", "C", "D"]


def _timezone_from_offset(slot: str, fallback: str = "Asia/Dhaka") -> str:
    """Infer the GHL timezone string from the ISO offset in the slot."""
    if slot.endswith("Z"):
        return "UTC"
    if "-04:00" in slot:
        return "America/New_York"
    if "-05:00" in slot:
        return "America/Chicago"
    if "-06:00" in slot:
        return "America/Chicago"
    if "-07:00" in slot:
        return "America/Denver"
    if "-08:00" in slot:
        return "America/Los_Angeles"
    if "+06:00" in slot:
        return "Asia/Dhaka"
    if "+05:30" in slot:
        return "Asia/Kolkata"
    return fallback


async def get_slots(calendar_type: str = "follow_up_b", timezone: str = "Asia/Dhaka") -> dict:
    """Fetch available slots for the next 7 days from GHL for a specific calendar."""
    cal_config = CALENDARS.get(calendar_type, CALENDARS["follow_up_b"])
    calendar_id = cal_config["id"]
    
    url = f"{GHL_BASE_URL}/appointments/slots"
    now_ms = int(time.time() * 1000)
    end_ms = now_ms + (7 * 24 * 60 * 60 * 1000)

    params = {
        "calendarId": calendar_id,
        "startDate": now_ms,
        "endDate": end_ms,
        "timezone": timezone,
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params=params, headers=get_ghl_headers())
        if resp.status_code == 200:
            return {"status": "success", "available_slots": resp.json()}
        return {"status": "error", "message": f"GHL slots API error: {resp.text}"}


async def book_appointment(
    name: str,
    email: str,
    phone: str,
    booking_slot: str,
    call_summary: str,
    calendar_type: str = "follow_up_b", # Default type
    timezone: str = "Asia/Dhaka",
) -> dict:
    """Full booking flow: find/create contact, check priority, book slot or request payment."""
    print(f"\n📋 [BookingService] phone={phone}, email={email}, slot={booking_slot}, type={calendar_type}")

    # Get calendar config
    cal_config = CALENDARS.get(calendar_type, CALENDARS["follow_up_b"])
    calendar_id = cal_config["id"]
    
    ghl_timezone = _timezone_from_offset(booking_slot, fallback=timezone)

    # Step 1: Search existing contact
    existing_contact = await search_contact_by_phone_or_email(phone=phone, email=email)

    if existing_contact:
        contact_id = existing_contact.get("id")
        # Ensure phone number is saved if it's missing or different in GHL
        existing_phone = existing_contact.get("phone")
        if phone and existing_phone != phone:
            print(f"🔄 [BookingService] Updating contact {contact_id} with phone: {phone}")
            try:
                await update_contact(contact_id, ContactUpdate(phone=phone))
            except Exception as e:
                print(f"⚠️ [BookingService] Failed to update contact phone: {e}")

        # Use AI-provided name as primary since GHL contact may lack name
        contact_name = (
            name
            or existing_contact.get("name")
            or f"{existing_contact.get('firstName', '')} {existing_contact.get('lastName', '')}".strip()
            or "Caller"
        )
        print(f"✅ [BookingService] Existing contact: {contact_id} ({contact_name})")
    else:
        # Create new contact
        new_contact_data = ContactCreate(
            firstName=name.split()[0] if name else "Caller",
            lastName=" ".join(name.split()[1:]) if len(name.split()) > 1 else "",
            email=email,
            phone=phone,
        )
        created = await add_contact(new_contact_data)
        contact_id = created.get("contact", {}).get("id") or created.get("id")
        contact_name = name
        print(f"🆕 [BookingService] New contact created: {contact_id}")

    if not contact_id:
        return {"status": "error", "message": "Could not find or create a contact in GHL."}

    # Step 2: Check if contact is in Priority Groups (A, B, C, D)
    tags = existing_contact.get("tags", []) if existing_contact else []
    # Normalize tags for comparison
    normalized_tags = [t.strip().upper() for t in tags]
    priority_check_tags = [g.strip().upper() for g in PRIORITY_GROUPS]
    
    is_priority = any(tag in priority_check_tags for tag in normalized_tags)
    
    # Pricing Logic
    price = cal_config["price"]
    
    # NEW RULE: If client is in Group A, B, C, or D, EVERYTHING is free and direct.
    if is_priority:
        requires_payment = False
        print(f"⭐ [BookingService] Priority client detected ({tags}). Direct booking enabled.")
    else:
        # For non-priority users, follow-ups are free (if configured), others need payment.
        requires_payment = price > 0
        if requires_payment:
            print(f"💰 [BookingService] Non-priority client. Payment required: ${price}")
    
    if requires_payment:
        print(f"🔗 [BookingService] Generating payment link for {email}...")
        # Create Stripe link
        payment_url = await create_stripe_payment_link(
            customer_email=email,
            customer_name=name,
            booking_slot=booking_slot,
            call_summary=call_summary,
            calendar_id=calendar_id,
            customer_phone=phone,
        )
        return {
            "status": "payment_required",
            "payment_url": payment_url,
            "message": f"To confirm your booking, a payment of ${price} is required. I've sent a secure payment link to your email ({email})."
        }

    # Step 3: Create appointment (if free/direct)
    appointment_data = AppointmentCreate(
        contactId=contact_id,
        calendarId=calendar_id,
        selectedTimezone=ghl_timezone,
        selectedSlot=booking_slot,
        title=f"AI Booking – {contact_name}",
        notes=(
            f"=== AI Receptionist Booking ===\n"
            f"Name    : {name}\n"
            f"Email   : {email}\n"
            f"Phone   : {phone}\n"
            f"Slot    : {booking_slot}\n"
            f"\n--- Call Summary ---\n{call_summary}"
        ),
        status="booked",
    )

    try:
        appointment = await create_appointment(appointment_data)
    except Exception as e:
        err_str = str(e)
        print(f"🔴 [BookingService] GHL appointment error: {err_str}")
        if "selectedSlot" in err_str or "no longer available" in err_str:
            return {
                "status": "slot_unavailable",
                "message": "The selected time slot is not available. Please ask the caller to choose a different time.",
            }
        if "calendar is disabled" in err_str.lower() or "calendarId" in err_str.lower():
            return {
                "status": "calendar_disabled",
                "message": "The booking calendar is currently disabled. Please inform the caller we cannot accept appointments right now.",
            }
        return {"status": "error", "message": f"Booking failed: {err_str}"}

    appointment_id = appointment.get("id", "N/A")
    print(f"📅 [BookingService] Appointment created: {appointment_id}")

    # Step 3: Add a note to the GHL contact with full call details
    try:
        note_body = (
            f"📞 AI Receptionist Booking\n"
            f"─────────────────────────\n"
            f"Name    : {name}\n"
            f"Email   : {email}\n"
            f"Phone   : {phone}\n"
            f"Slot    : {booking_slot}\n"
            f"Appt ID : {appointment_id}\n"
            f"\n💬 Call Summary:\n{call_summary}"
        )
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{GHL_BASE_URL}/contacts/{contact_id}/notes",
                json={"body": note_body, "userId": ""},
                headers=get_ghl_headers()
            )
        print(f"📝 [BookingService] Note added to contact {contact_id}")
    except Exception as e:
        print(f"⚠️ [BookingService] Note creation failed (non-fatal): {e}")

    # Step 3: Send confirmation email
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(booking_slot.replace("Z", "+00:00"))
        booking_date = dt.strftime("%d %B %Y")
        booking_time = dt.strftime("%I:%M %p")
    except Exception:
        booking_date = booking_slot[:10]
        booking_time = booking_slot[11:16]

    try:
        await send_booking_confirmation(
            to_email=email,
            contact_name=contact_name,
            booking_date=booking_date,
            booking_time=booking_time,
            call_summary=call_summary,
        )
        print(f"📧 [BookingService] Confirmation email sent to {email}")
    except Exception as e:
        print(f"⚠️ [BookingService] Email failed (non-fatal): {e}")

    return {
        "status": "confirmed",
        "appointment_id": appointment_id,
        "contact_id": contact_id,
        "message": f"Appointment confirmed for {contact_name} on {booking_date} at {booking_time}.",
    }
