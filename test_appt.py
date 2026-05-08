import asyncio, httpx, json, sys
sys.path.insert(0, '/home/mhraju/Projects/airec')
from services.ghl import get_ghl_headers
from config import GHL_BASE_URL

async def main():
    appt_id = "JmK69Wa3AAdFwZ2cilSi"
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"{GHL_BASE_URL}/appointments/{appt_id}", headers=get_ghl_headers())
        data = resp.json()
        # Print all keys and their values
        for k, v in data.items():
            print(f"{k}: {v}")

asyncio.run(main())
