"""
GHL contact search across all groups/pipelines.
"""
import httpx
import logging
from typing import Optional
from config import GHL_BASE_URL, GHL_API_KEY, GHL_LOCATION_ID

logger = logging.getLogger(__name__)


def get_ghl_headers():
    # For SMS contact search — use PIT token via shared get_sms_headers()
    try:
        from services.ghl import get_sms_headers
        return get_sms_headers()
    except Exception:
        return {
            "Authorization": f"Bearer {GHL_API_KEY}",
            "Content-Type": "application/json",
            "Version": "2021-07-28",
        }


async def search_contact_by_phone_or_email(phone: Optional[str] = None, email: Optional[str] = None) -> Optional[dict]:
    """
    Search for a contact in GHL by phone or email.
    GHL V2 contacts API requires the 'query' parameter (not 'phone' or 'email' directly).
    """
    url = "https://services.leadconnectorhq.com/contacts/"
    
    query_value = ""
    clean_target_phone = ""
    target_last_10 = ""
    
    if email:
        query_value = email
    elif phone:
        # Strip all formatting from the incoming Twilio phone
        clean_target_phone = "".join(filter(str.isdigit, phone))
        target_last_10 = clean_target_phone[-10:] if len(clean_target_phone) >= 10 else clean_target_phone
        
        # Search by the last 10 digits to maximize the chance of finding the exact match in GHL,
        # handling cases where GHL has the number saved slightly differently (e.g., (908) 906-7284)
        query_value = target_last_10
    
    if not query_value:
        return None

    params = {
        "locationId": GHL_LOCATION_ID,
        "query": query_value,   # ← correct param for GHL V2 contacts search
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        logger.info(f"🔍 [GHL Search] Searching contact: query={query_value}")
        response = await client.get(url, params=params, headers=get_ghl_headers())

        if response.status_code != 200:
            logger.error(f"🔴 [GHL Search] API Error: {response.status_code} — {response.text[:200]}")
            return None

        data = response.json()
        contacts = data.get("contacts", [])

        if contacts:
            if email:
                for contact in contacts:
                    c_email = contact.get("email", "")
                    if c_email and c_email.lower() == email.lower():
                        logger.info(f"✅ [GHL Search] Found contact by exact email match: {contact.get('id')}")
                        return contact
                
                # Fallback to the first result if no exact match but query succeeded
                contact = contacts[0]
                logger.info(f"✅ [GHL Search] Found contact by email query: {contact.get('id')}")
                return contact
                
            elif phone:
                # Strip formatting from GHL numbers and compare with Twilio number
                for contact in contacts:
                    c_phone = contact.get("phone", "")
                    if not c_phone:
                        continue
                        
                    c_clean = "".join(filter(str.isdigit, c_phone))
                    c_last_10 = c_clean[-10:] if len(c_clean) >= 10 else c_clean
                    
                    if target_last_10 and c_last_10 and target_last_10 == c_last_10:
                        logger.info(f"✅ [GHL Search] Found contact by phone: {contact.get('id')}")
                        return contact
                
                logger.info(f"🆕 [GHL Search] No contact matched phone exactly for: {phone}")
                return None

        logger.info(f"🆕 [GHL Search] No contact found for: {query_value}")
        return None
