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
    Search for a contact in GHL by phone or email across all contacts in the location.
    Returns the first match or None if not found.
    """
    url = f"{GHL_BASE_URL}/contacts/"
    params = {"locationId": GHL_LOCATION_ID, "limit": 100}

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get(url, params=params, headers=get_ghl_headers())
        if response.status_code != 200:
            print(f"⚠️ [GHL] Could not fetch contacts: {response.text}")
            return None

        data = response.json()
        contacts = data if isinstance(data, list) else data.get("contacts", [])

        for contact in contacts:
            # Match by phone
            if phone and phone in (contact.get("phone") or ""):
                print(f"✅ [GHL] Found existing contact by phone: {contact.get('id')}")
                return contact
            # Match by email
            if email and email.lower() == (contact.get("email") or "").lower():
                print(f"✅ [GHL] Found existing contact by email: {contact.get('id')}")
                return contact

        print(f"🆕 [GHL] No existing contact found for phone={phone}, email={email}")
        return None
