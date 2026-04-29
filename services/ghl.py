import httpx
from config import GHL_BASE_URL, GHL_API_KEY, GHL_LOCATION_ID
from schemas import *
from fastapi import HTTPException

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
