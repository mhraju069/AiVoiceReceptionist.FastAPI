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
    import time as _time
    url = f"{GHL_BASE_URL}/appointments/"
    # GHL requires startDate and endDate (epoch ms). Default to 90-day window.
    now_ms = int(_time.time() * 1000)
    
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
                print(f"Error fetching from calendar {cid}: {e}")
            
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
        return list(enriched)


async def get_contacts(query: Optional[str] = None, limit: int = 20):
    """
    Fetch contacts from GoHighLevel.
    """
    url = f"{GHL_BASE_URL}/contacts/"
    params = {
        "locationId": GHL_LOCATION_ID,
        "limit": limit
    }
    if query:
        params["query"] = query
        
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, headers=get_ghl_headers())
        
        if response.status_code == 200:
            return response.json().get("contacts", [])
        else:
            raise HTTPException(status_code=response.status_code, detail=response.text)
