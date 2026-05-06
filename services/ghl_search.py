"""
GHL contact search across all groups/pipelines.
"""
import httpx
from typing import Optional
from config import GHL_BASE_URL, GHL_API_KEY, GHL_LOCATION_ID


def get_ghl_headers():
    return {
        "Authorization": f"Bearer {GHL_API_KEY}",
        "Content-Type": "application/json"
    }


async def search_contact_by_phone_or_email(phone: Optional[str] = None, email: Optional[str] = None) -> Optional[dict]:
    """
    Search for a contact in GHL by phone or email directly using specific filters.
    """
    url = f"{GHL_BASE_URL}/contacts/"
    params = {"locationId": GHL_LOCATION_ID}
    
    if email:
        params["email"] = email
    elif phone:
        params["phone"] = phone
    else:
        return None

    async with httpx.AsyncClient(timeout=20.0) as client:
        print(f"🔍 [GHL] Searching for contact with params: {params}")
        response = await client.get(url, params=params, headers=get_ghl_headers())
        
        if response.status_code != 200:
            print(f"🔴 [GHL] API Error: {response.status_code} - {response.text}")
            return None

        data = response.json()
        # GHL returns contacts in a list
        contacts = data.get("contacts", [])

        if contacts:
            contact = contacts[0]
            print(f"✅ [GHL] Found existing contact: {contact.get('id')}")
            return contact

        print(f"🆕 [GHL] No existing contact found for params: {params}")
        return None
