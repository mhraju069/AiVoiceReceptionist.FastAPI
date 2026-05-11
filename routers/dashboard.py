from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
import datetime
import httpx
import base64
from typing import Optional

from database import get_db, SessionLocal
from models.activity_models import Activity, CallLog
from services.ghl import get_all_appointments, get_contacts
from routers.twilio import TWILIO_SID, TWILIO_AUTH_TOKEN
from schemas.dashboard_schemas import LeadsDashboardResponse, CalendarDashboardResponse, StatsDashboardResponse

router = APIRouter(
    prefix="/api/dashboard",
    tags=["Dashboard"]
)

async def get_all_calls(calendar_id: Optional[str] = None):
    db = SessionLocal()
    try:
        calls = db.query(CallLog).order_by(CallLog.start_time.desc()).all()
        return calls
    finally:
        db.close()

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

@router.get("/stats", response_model=StatsDashboardResponse)
async def get_dashboard_stats(calendar_id: Optional[str] = None, db: Session = Depends(get_db)):
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
        today_bookings = await get_all_appointments(specific_day="today", calendar_id=calendar_id)
        todays_booking_count = len(today_bookings)
    except Exception as e:
        print(f"Error fetching today bookings: {e}")
        todays_booking_count = 0
        today_bookings = []

    try:
        # Fetch all upcoming or recent bookings
        appointments = await get_all_appointments(calendar_id=calendar_id)
        
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


@router.get("/leads", response_model=LeadsDashboardResponse)
async def ViewLeads(query: Optional[str] = None):
    raw_leads = await get_contacts(query=query)
    
    enriched_leads = []
    urgent_count = 0
    new_count = 0
    qualified_count = 0

    for lead in raw_leads:
        tags = lead.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]
        
        # 1. Map Priority based on Group Tags
        priority = "Low"
        if any(t in ["Group A", "A", "Group B", "B"] for t in tags):
            priority = "High"
        elif any(t in ["Group C", "C"] for t in tags):
            priority = "Medium"
            
        # 2. Determine Status (Booked, Contacted, New, Closed)
        # For a real implementation, we'd check against our CallLog or GHL Appointments
        # Here we'll simulate based on common GHL keys
        status = "New"
        if lead.get("lastAppointment"):
            status = "Booked"
            qualified_count += 1
        elif lead.get("lastContact"):
            status = "Contacted"
        else:
            new_count += 1
            
        if priority == "High":
            urgent_count += 1

        # 3. Get Intent/Tag
        # Use the first tag that isn't a Group tag as the 'Intent'
        intent = "General"
        for t in tags:
            if not any(g in t for g in ["Group", "A", "B", "C", "D"]) or len(t) > 2:
                intent = t
                break

        enriched_leads.append({
            "id": lead.get("id"),
            "name": lead.get("name") or f"{lead.get('firstName', '')} {lead.get('lastName', '')}".strip() or "Unknown",
            "email": lead.get("email"),
            "phone": lead.get("phone"),
            "priority": priority,
            "intent": intent,
            "status": status,
            "last_contact": lead.get("dateUpdated") or lead.get("dateAdded"),
            "tags": tags
        })

    return {
        "summary": {
            "urgent": urgent_count,
            "new": new_count,
            "qualified": qualified_count,
            "total": len(enriched_leads)
        },
        "leads": enriched_leads
    }


@router.get("/calendar", response_model=CalendarDashboardResponse)
async def ViewCalendar(calendar_id: Optional[str] = None):
    calendar = await get_all_appointments(calendar_id=calendar_id)
    return {
        "calendar": calendar
    }


@router.get("/call-log")
async def ViewCallLog(calendar_id: Optional[str] = None):
    call_log = await get_all_calls(calendar_id=calendar_id)
    return {
        "call_log": call_log
    }

@router.get("/call-details/{call_id}")
async def ViewCallDetail(call_id: int):
    db = SessionLocal()
    try:
        call = db.query(CallLog).filter(CallLog.id == call_id).first()
        if not call:
            return {"error": "Call log not found"}
        return call
    finally:
        db.close()