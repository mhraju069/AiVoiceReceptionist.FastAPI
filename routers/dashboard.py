from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import datetime
import httpx
import base64

from database import get_db
from models.activity_models import Activity
from services.ghl import get_all_appointments
from routers.twilio import TWILIO_SID, TWILIO_AUTH_TOKEN

router = APIRouter(
    prefix="/api/dashboard",
    tags=["Dashboard"]
)

async def get_twilio_calls_today():
    if not TWILIO_SID or not TWILIO_AUTH_TOKEN:
        return 0
        
    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Calls.json"
    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    params = {"StartTime>": today}
    
    auth_header = base64.b64encode(f"{TWILIO_SID}:{TWILIO_AUTH_TOKEN}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(url, headers=headers, params=params)
            if response.status_code == 200:
                data = response.json()
                return len(data.get("calls", []))
        except Exception as e:
            print(f"Twilio API Error: {e}")
            pass
    return 0

@router.get("/stats")
async def get_dashboard_stats(db: Session = Depends(get_db)):
    """
    Returns dashboard statistics including today's call count, 
    booking count, recent GHL activity, and calendar details.
    """
    # 1. Today's Call Count (from Twilio API and Local DB)
    twilio_calls = await get_twilio_calls_today()
    
    today_date = datetime.datetime.utcnow().date()
    # Count local activity calls if they are logging there
    db_calls = db.query(Activity).filter(Activity.type == 'call').count()
    
    calls_today = twilio_calls if twilio_calls > 0 else db_calls

    # 2. Today's Booking Count & Calendar Details
    try:
        # Fetch today's bookings
        today_bookings = await get_all_appointments(specific_day="today")
        todays_booking_count = len(today_bookings)
    except Exception as e:
        print(f"Error fetching today bookings: {e}")
        todays_booking_count = 0
        today_bookings = []

    try:
        # Fetch all upcoming or recent bookings
        appointments = await get_all_appointments()
        
        # Format recent activity (top 5 most recent or upcoming)
        recent_activity = []
        # Sort appointments by time if possible (assuming ISO strings)
        sorted_appointments = sorted(
            appointments, 
            key=lambda x: x.get("selectedSlot") or x.get("startTime") or "", 
            reverse=True
        )
        
        for appt in sorted_appointments[:5]:
            contact = appt.get("contact", {})
            name = contact.get("name") or f"{contact.get('firstName', '')} {contact.get('lastName', '')}".strip() or "Unknown"
            status = appt.get("appointmentStatus", "booked")
            
            recent_activity.append({
                "type": "appointment",
                "title": appt.get("title", f"Appointment with {name}"),
                "status": status,
                "time": appt.get("selectedSlot") or appt.get("startTime"),
                "contact_name": name,
                "contact_email": contact.get("email", ""),
                "contact_phone": contact.get("phone", "")
            })
            
    except Exception as e:
        print(f"Error fetching all bookings: {e}")
        appointments = []
        recent_activity = []

    return {
        "todays_call_count": calls_today,
        "todays_booking_count": todays_booking_count,
        "recent_activity": recent_activity,
        "calendar_details": appointments
    }
