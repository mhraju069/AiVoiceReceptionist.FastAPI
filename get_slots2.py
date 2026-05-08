import asyncio
from services.ghl import get_ghl_headers
from config import GHL_BASE_URL
import httpx
import time

async def main():
    calendar_id = "4OIPAoMvrUMkcbRSyYiv"
    url = f"{GHL_BASE_URL}/appointments/slots"
    now_ms = int(time.time() * 1000)
    end_ms = now_ms + (7 * 24 * 60 * 60 * 1000)
    
    params = {
        "calendarId": calendar_id,
        "startDate": now_ms,
        "endDate": end_ms,
        "timezone": "Asia/Dhaka"
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params, headers=get_ghl_headers())
        if resp.status_code == 200:
            data = resp.json()
            keys = list(data.keys())
            if keys:
                print(f"Slots on {keys[0]} in Asia/Dhaka: {data[keys[0]]}")
        else:
            print(resp.text)

asyncio.run(main())
