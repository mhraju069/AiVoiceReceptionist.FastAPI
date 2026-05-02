import httpx
import datetime
from config import GHL_BASE_URL, GHL_API_KEY, GHL_LOCATION_ID
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
    this_week: Optional[bool] = None
):
    """
    Fetch all appointments from GoHighLevel and optionally filter by email, phone, 
    time range, specific day (or today), or this week.
    """
    url = f"{GHL_BASE_URL}/appointments/"
    params = {"locationId": GHL_LOCATION_ID}
    
    async with httpx.AsyncClient() as client:
        response = await client.get(url, params=params, headers=get_ghl_headers())
        
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=response.text)
            
        appointments_data = response.json()
        
        # GHL returns either a list of appointments directly, or under a key
        appointments = []
        if isinstance(appointments_data, list):
            appointments = appointments_data
        elif isinstance(appointments_data, dict):
            appointments = appointments_data.get("appointments", [])
            
        # Perform local filtering
        filtered_appointments = []
        for appt in appointments:
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
            
        return filtered_appointments
