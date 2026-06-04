"""
Quick SMS send test via GHL PIT token.
Usage:
    python tests/test_sms.py +19089067284 "Hello from Reba!"
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

import os
import httpx
from services.ghl import get_ghl_headers
from config import GHL_LOCATION_ID

async def main():
    to  = "+17759802006"
    msg = "Hello from Reba! GHL SMS test."

    headers = get_ghl_headers()
    from_num = os.getenv("GHL_FROM_NUMBER", "")

    print(f"\n🔑 Token prefix: {headers['Authorization'][:30]}...")
    print(f"📞 GHL_FROM_NUMBER: {from_num or '⚠️  NOT SET'}")
    print(f"📍 GHL_LOCATION_ID: {GHL_LOCATION_ID or '⚠️  NOT SET'}")

    # Step 1: Create/find contact
    async with httpx.AsyncClient(timeout=15) as client:
        print(f"\n📤 Creating contact for {to}...")
        r = await client.post(
            "https://services.leadconnectorhq.com/contacts/",
            json={"phone": to, "locationId": GHL_LOCATION_ID, "name": "Test"},
            headers=headers,
        )
        print(f"   Status: {r.status_code}")
        print(f"   Body:   {r.text[:300]}")

        contact_id = None
        if r.status_code in (200, 201):
            contact_id = (r.json().get("contact") or r.json()).get("id")
        elif r.status_code == 400:
            contact_id = r.json().get("meta", {}).get("contactId")
            print(f"   ♻️  Reusing existing contact: {contact_id}")

        if not contact_id:
            print("❌ No contact_id. Stopping.")
            return

        # Step 2: Send SMS
        payload = {"type": "SMS", "contactId": contact_id, "message": msg}
        if from_num:
            payload["fromNumber"] = from_num

        print(f"\n📨 Sending SMS to {to} (contact: {contact_id})...")
        resp = await client.post(
            "https://services.leadconnectorhq.com/conversations/messages",
            json=payload,
            headers=headers,
        )
        print(f"   Status: {resp.status_code}")
        print(f"   Body:   {resp.text[:500]}")
        print("\n✅ Done!" if resp.status_code in (200, 201) else "\n❌ Failed.")

asyncio.run(main())
