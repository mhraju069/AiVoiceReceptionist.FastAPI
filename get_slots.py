import asyncio
from services.ghl import get_ghl_headers
from config import GHL_BASE_URL, GHL_LOCATION_ID
import httpx
from datetime import datetime

async def main():
    import time
    
    calendar_id = "4OIPAoMvrUMkcbRSyYiv"
    url = f"{GHL_BASE_URL}/appointments/slots"
    
    now_ms = int(time.time() * 1000)
    end_ms = now_ms + (7 * 24 * 60 * 60 * 1000) # 7 days later
    
    params = {
        "calendarId": calendar_id,
        "startDate": now_ms,
        "endDate": end_ms
    }
    
    async with httpx.AsyncClient() as client:
        print(f"Fetching slots for calendar {calendar_id} from {now_ms} to {end_ms}...")
        resp = await client.get(url, params=params, headers=get_ghl_headers())
        print(f"Response: {resp.status_code}")
        
        if resp.status_code == 200:
            data = resp.json()
            keys = list(data.keys())
            print(f"Available dates: {keys}")
            for key in keys[:3]: # print first 3 days
                print(f"Slots on {key}: {data[key]}")
        else:
            print(resp.text)

asyncio.run(main())
