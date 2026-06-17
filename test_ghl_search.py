import asyncio
import re
from services.ghl_search import get_ghl_headers, search_contact_by_phone_or_email
from config import GHL_LOCATION_ID

async def main():
    print("Testing with full Twilio number: +19089067284")
    res1 = await search_contact_by_phone_or_email(phone="+19089067284")
    print("Result 1:", res1.get('phone') if res1 else "None")

    print("Testing with full Twilio number: +17812220591")
    res2 = await search_contact_by_phone_or_email(phone="+17812220591")
    print("Result 2:", res2.get('phone') if res2 else "None")

asyncio.run(main())
