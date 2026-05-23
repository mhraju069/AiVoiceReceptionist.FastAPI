import logging
logger = logging.getLogger(__name__)

import httpx
import datetime
from config import (
    GHL_BASE_URL, GHL_API_KEY, GHL_LOCATION_ID,
    CALENDAR_FOLLOW_UP_C, CALENDAR_FOLLOW_UP_B, 
    CALENDAR_VIRTUAL_CONSULT_15, CALENDAR_VIRTUAL_CPA_45, 
    CALENDAR_OFFICE_CPA_45, CALENDAR_BEAUTY_SALON_45, CALENDAR_TEST
)
from schemas import *
from fastapi import HTTPException
from typing import Optional
import time as _time

# In-memory cache stores
CACHE_TTL = 300  # 5 minutes in seconds
_appointments_cache = {}
_contacts_cache = {}

# Create headers for GHL API requests
def get_ghl_headers():
    return {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Content-Type": "application/json"
    }



async def add_contact(contact: ContactCreate):
    url = f"{GHL_BASE_URL}/contacts/"
    
    # We must ensure locationId is included if necessary in v1
    # Check GHL documentation: The typical payload includes locationId.
    payload = contact.model_dump(exclude_none=True)
    payload["locationId"] = GHL_LOCATION_ID
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=get_ghl_headers())
        
        if response.status_code in [200, 201]:
            return response.json()
        else:
            raise HTTPException(status_code=response.status_code, detail=response.text)



async def update_contact(contact_id: str, contact: ContactUpdate):
    url = f"{GHL_BASE_URL}/contacts/{contact_id}"
    
    payload = contact.model_dump(exclude_none=True)
    
    async with httpx.AsyncClient() as client:
        response = await client.put(url, json=payload, headers=get_ghl_headers())
        
        if response.status_code in [200, 201]:
            return response.json()
        else:
            raise HTTPException(status_code=response.status_code, detail=response.text)



async def get_contact(contact_id: str):
    url = f"{GHL_BASE_URL}/contacts/{contact_id}"
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=get_ghl_headers())
        
        if response.status_code == 200:
            return response.json()
        else:
            raise HTTPException(status_code=response.status_code, detail=response.text)

async def get_contact_by_phone(phone: str):
    """Search for a contact by phone number in GHL. Returns raw contact dict."""
    url = f"{GHL_BASE_URL}/contacts/lookup"
    params = {"phone": phone}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=get_ghl_headers(), params=params)
            if response.status_code == 200:
                data = response.json()
                if data.get("contact"):
                    return data.get("contact")
        except Exception as e:
            logger.error(f"Error looking up contact by phone: {e}")
    return None


async def get_contact_profile_by_phone(phone: str) -> dict:
    """
    Returns a structured profile for the AI: name, group (A/B/C/D), 
    client type, and invoice status from GHL tags.
    """
    from services.known_clients import normalize_phone
    normalized = normalize_phone(phone)
    search_phone = phone
    if normalized:
        if len(normalized) == 11 and normalized.startswith("1"):
            search_phone = f"+{normalized}"
        elif len(normalized) == 10:
            search_phone = f"+1{normalized}"

    contact = await get_contact_by_phone(search_phone)
    if not contact and normalized:
        # Fallback 1: Try searching with 11-digit format without +
        contact = await get_contact_by_phone(normalized)
        if not contact and len(normalized) == 11 and normalized.startswith("1"):
            # Fallback 2: Try searching with 10-digit format
            contact = await get_contact_by_phone(normalized[1:])

    if not contact:
        return {"found": False, "client_type": "Prospect", "group": None, "name": None}

    first = contact.get("firstName", "") or ""
    last = contact.get("lastName", "") or ""
    name = f"{first} {last}".strip() or "Client"

    # Parse tags to determine group and client type
    raw_tags = contact.get("tags", [])
    if isinstance(raw_tags, str):
        tags = [t.strip().upper() for t in raw_tags.split(",")]
    else:
        tags = [str(t).strip().upper() for t in raw_tags]

    group = None
    for g in ["A", "B", "C", "D"]:
        if any(g == tag or f"GROUP {g}" == tag for tag in tags):
            group = g
            break

    client_type = "Prospect"
    if any("ADHOC" in tag for tag in tags):
        client_type = "Adhoc"
    elif group:
        client_type = f"Class {group} Client"

    invoice_due = any("INVOICE" in tag or "DUE" in tag for tag in tags)

    return {
        "found": True,
        "contact_id": contact.get("id"),
        "name": name,
        "group": group,
        "client_type": client_type,
        "invoice_due": invoice_due,
        "tags": tags,
    }


async def add_crm_note(contact_id: str, note_body: str) -> bool:
    """Add a note to a GHL contact — used to record missed call messages."""
    if not contact_id:
        return False
    url = f"{GHL_BASE_URL}/contacts/{contact_id}/notes"
    payload = {"body": note_body}
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, json=payload, headers=get_ghl_headers())
            if response.status_code in [200, 201]:
                logger.info(f"✅ [GHL] Note saved for contact {contact_id}")
                return True
        except Exception as e:
            logger.error(f"Error saving CRM note: {e}")
    return False




async def create_appointment(appointment: AppointmentCreate):
    url = f"{GHL_BASE_URL}/appointments/"
    
    payload = appointment.model_dump(exclude_none=True)
    payload["locationId"] = GHL_LOCATION_ID
    
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json=payload, headers=get_ghl_headers())
        
        if response.status_code in [200, 201]:
            return response.json()
        else:
            raise HTTPException(status_code=response.status_code, detail=response.text)



async def update_appointment(appointment_id: str, appointment: AppointmentUpdate):
    url = f"{GHL_BASE_URL}/appointments/{appointment_id}"
    
    payload = appointment.model_dump(exclude_none=True)
    
    async with httpx.AsyncClient() as client:
        response = await client.put(url, json=payload, headers=get_ghl_headers())
        
        if response.status_code in [200, 201]:
            return response.json()
        else:
            raise HTTPException(status_code=response.status_code, detail=response.text)


async def get_all_appointments(
    email: Optional[str] = None,
    phone: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    specific_day: Optional[str] = None,
    this_week: Optional[bool] = None,
    calendar_id: Optional[str] = None
):
    """
    Fetch all appointments from GoHighLevel and optionally filter by email, phone, 
    time range, specific day (or today), or this week.
    """
    cache_key = f"{email}_{phone}_{start_time}_{end_time}_{specific_day}_{this_week}_{calendar_id}"
    now_sec = _time.time()
    
    if cache_key in _appointments_cache:
        cached_entry = _appointments_cache[cache_key]
        if now_sec - cached_entry["timestamp"] < CACHE_TTL:
            return cached_entry["data"]

    url = f"{GHL_BASE_URL}/appointments/"
    # GHL requires startDate and endDate (epoch ms). Default to 90-day window.
    now_ms = int(now_sec * 1000)
    
    # If no specific calendar_id is provided, fetch from all known calendars
    target_calendars = [calendar_id] if calendar_id else [
        CALENDAR_FOLLOW_UP_C, CALENDAR_FOLLOW_UP_B,
        CALENDAR_VIRTUAL_CONSULT_15, CALENDAR_VIRTUAL_CPA_45,
        CALENDAR_OFFICE_CPA_45, CALENDAR_BEAUTY_SALON_45, CALENDAR_TEST
    ]
    # Filter out None values in case some env vars are missing
    target_calendars = [cid for cid in target_calendars if cid]

    all_raw_appointments = []
    
    async with httpx.AsyncClient(timeout=20) as client:
        for cid in target_calendars:
            params = {
                "locationId": GHL_LOCATION_ID,
                "calendarId": cid,
                "startDate": now_ms - (30 * 24 * 60 * 60 * 1000),  # 30 days ago
                "endDate": now_ms + (60 * 24 * 60 * 60 * 1000),    # 60 days ahead
            }
            try:
                response = await client.get(url, params=params, headers=get_ghl_headers())
                if response.status_code == 200:
                    data = response.json()
                    appts = data if isinstance(data, list) else data.get("appointments", [])
                    all_raw_appointments.extend(appts)
            except Exception as e:
                logger.info(f"Error fetching from calendar {cid}: {e}")
            
        # Perform local filtering on the consolidated list
        filtered_appointments = []
        # Use a set to avoid duplicates if an appointment somehow appears in multiple calendars (rare but safe)
        seen_ids = set()
        
        for appt in all_raw_appointments:
            appt_id = appt.get("id")
            if appt_id in seen_ids:
                continue
            seen_ids.add(appt_id)
            
            contact = appt.get("contact", {})
            
            if email and email.lower() not in contact.get("email", "").lower():
                continue
                
            if phone and phone not in contact.get("phone", ""):
                continue
                
            appt_time = appt.get("selectedSlot") or appt.get("startTime")
            
            if appt_time:
                if start_time and appt_time < start_time:
                    continue
                if end_time and appt_time > end_time:
                    continue
                    
                appt_day = appt_time[:10]
                
                if specific_day:
                    target_day = specific_day
                    if specific_day.lower() == "today":
                        target_day = datetime.date.today().isoformat()
                    if appt_day != target_day:
                        continue
                        
                if this_week:
                    today = datetime.date.today()
                    week_start = today - datetime.timedelta(days=today.weekday())
                    week_end = week_start + datetime.timedelta(days=6)
                    if not (week_start.isoformat() <= appt_day <= week_end.isoformat()):
                        continue
                    
            filtered_appointments.append(appt)
        
        # Enrich each appointment with full details (notes, title, contact + contact notes)
        import asyncio
        async def enrich(appt_summary):
            appt_id = appt_summary.get("id")
            merged = dict(appt_summary)
            try:
                # 1) Fetch full appointment detail
                detail_resp = await client.get(
                    f"{GHL_BASE_URL}/appointments/{appt_id}",
                    headers=get_ghl_headers()
                )
                if detail_resp.status_code == 200:
                    merged.update(detail_resp.json())

                # 2) Fetch contact notes to surface AI call summary
                contact_id = merged.get("contactId")
                if contact_id:
                    notes_resp = await client.get(
                        f"{GHL_BASE_URL}/contacts/{contact_id}/notes",
                        headers=get_ghl_headers()
                    )
                    if notes_resp.status_code == 200:
                        all_notes = notes_resp.json().get("notes", [])
                        # Find the AI booking note that matches this appointment
                        ai_notes = [
                            n for n in all_notes
                            if appt_id in n.get("body", "")
                            or "AI Receptionist Booking" in n.get("body", "")
                        ]
                        if ai_notes:
                            # Use the most recent matching note
                            latest_note = sorted(ai_notes, key=lambda x: x.get("dateAdded", ""), reverse=True)[0]
                            merged["caller_summary"] = latest_note.get("body", "")
                        # Also include all notes for full visibility
                        merged["contact_notes"] = all_notes
            except Exception:
                pass
            return merged
        
        enriched = await asyncio.gather(*[enrich(a) for a in filtered_appointments])
        
        final_data = list(enriched)
        _appointments_cache[cache_key] = {
            "timestamp": now_sec,
            "data": final_data
        }
        return final_data


async def get_contacts(query: Optional[str] = None, limit: int = 20):
    """
    Fetch contacts from GoHighLevel.
    """
    cache_key = f"{query}_{limit}"
    now_sec = _time.time()
    
    if cache_key in _contacts_cache:
        cached_entry = _contacts_cache[cache_key]
        if now_sec - cached_entry["timestamp"] < CACHE_TTL:
            return cached_entry["data"]

    url = f"{GHL_BASE_URL}/contacts/"
    params = {
        "locationId": GHL_LOCATION_ID,
        "limit": limit
    }
    if query:
        params["query"] = query
        
    async with httpx.AsyncClient() as client:
        # Avoid trailing slash issues
        clean_url = url.rstrip('/')
        response = await client.get(clean_url, params=params, headers=get_ghl_headers())
        
        if response.status_code == 200:
            data = response.json().get("contacts", [])
            _contacts_cache[cache_key] = {
                "timestamp": now_sec,
                "data": data
            }
            return data
        else:
            raise HTTPException(status_code=response.status_code, detail=response.text)


async def send_sms_via_ghl(to_phone: str, message: str) -> bool:
    """
    Sends an SMS message using GoHighLevel API (supporting both V1 and V2 endpoints).
    If the contact doesn't exist, it first creates it.
    """
    import os
    from services.known_clients import normalize_phone
    from services.ghl_search import search_contact_by_phone_or_email
    
    normalized = normalize_phone(to_phone)
    # Ensure it is in E.164 format starting with +
    if not normalized.startswith("+"):
        if len(normalized) == 10:
            normalized = f"+1{normalized}"
        elif len(normalized) == 11 and normalized.startswith("1"):
            normalized = f"+{normalized}"
        else:
            normalized = f"+{normalized}"
            
    logger.info(f"📤 [GHL SMS] Starting SMS send to {normalized}...")
    
    contact_id = None
    try:
        # Search for existing contact in GHL
        contact = await search_contact_by_phone_or_email(phone=normalized)
        if contact:
            contact_id = contact.get("id") or contact.get("contactId")
            logger.info(f"✅ [GHL SMS] Found contact: {contact_id}")
        else:
            # Create a new contact
            logger.info(f"🆕 [GHL SMS] Contact not found. Creating test contact in GHL...")
            contact_resp = await add_contact(ContactCreate(
                phone=normalized,
                name="Prospect",
                source="AI Call Text Request",
                tags=["ai-text-request"]
            ))
            contact_obj = contact_resp.get("contact") or contact_resp
            contact_id = contact_obj.get("id") or contact_obj.get("contactId")
            logger.info(f"✅ [GHL SMS] Created contact: {contact_id}")
    except Exception as e:
        logger.error(f"❌ [GHL SMS] Error finding/creating contact: {e}")
        
    if not contact_id:
        logger.error("❌ [GHL SMS] Could not get or create contact_id.")
        return False

    # Send message using GHL V1 or V2
    headers = get_ghl_headers()
    from_num = os.getenv("GHL_FROM_NUMBER", "+17814887674")
    
    if "rest.gohighlevel.com" in GHL_BASE_URL or "api.gohighlevel.com" in GHL_BASE_URL:
        # GHL V1 endpoint
        url = f"{GHL_BASE_URL}/contacts/{contact_id}/messages"
        payload = {
            "type": "SMS",
            "message": message
        }
        if from_num:
            payload["fromNumber"] = from_num
            
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                # Remove trailing slash for stability
                clean_url = url.rstrip('/')
                resp = await client.post(clean_url, json=payload, headers=headers)
                if resp.status_code in [200, 201]:
                    logger.info(f"✅ [GHL SMS] V1 message sent to {normalized}")
                    return True
                else:
                    logger.error(f"❌ [GHL SMS] V1 Send failed (status={resp.status_code}): {resp.text}")
            except Exception as e:
                logger.error(f"❌ [GHL SMS] V1 send exception: {e}")
    else:
        # GHL V2 endpoint
        url = f"{GHL_BASE_URL}/conversations/messages"
        headers["Version"] = "2021-07-28"
        payload = {
            "type": "SMS",
            "contactId": contact_id,
            "message": message
        }
        if from_num:
            payload["fromNumber"] = from_num
            
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                clean_url = url.rstrip('/')
                resp = await client.post(clean_url, json=payload, headers=headers)
                if resp.status_code in [200, 201]:
                    logger.info(f"✅ [GHL SMS] V2 message sent to {normalized}")
                    return True
                else:
                    logger.error(f"❌ [GHL SMS] V2 Send failed (status={resp.status_code}): {resp.text}")
            except Exception as e:
                logger.error(f"❌ [GHL SMS] V2 send exception: {e}")
                
    return False
