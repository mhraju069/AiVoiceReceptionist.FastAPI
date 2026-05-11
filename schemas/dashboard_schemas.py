from pydantic import BaseModel
from typing import Optional, List, Dict, Any

class LeadSummary(BaseModel):
    urgent: int
    new: int
    qualified: int
    total: int

class LeadResponse(BaseModel):
    id: str
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    priority: str
    intent: str
    status: str
    last_contact: Optional[str] = None
    tags: List[str]

class LeadsDashboardResponse(BaseModel):
    summary: LeadSummary
    leads: List[LeadResponse]

class ContactBrief(BaseModel):
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

class CalendarAppointment(BaseModel):
    id: str
    title: Optional[str] = None
    startTime: Optional[str] = None
    endTime: Optional[str] = None
    selectedSlot: Optional[str] = None
    status: Optional[str] = None
    appointmentStatus: Optional[str] = None
    contact: Optional[ContactBrief] = None
    caller_summary: Optional[str] = None
    contact_notes: Optional[List[Dict[str, Any]]] = None

class CalendarDashboardResponse(BaseModel):
    calendar: List[CalendarAppointment]

class RecentActivity(BaseModel):
    type: str
    title: str
    status: str
    time: Optional[str] = None
    contact_name: str
    contact_email: Optional[str] = None
    contact_phone: Optional[str] = None

class StatsDashboardResponse(BaseModel):
    todays_call_count: int
    todays_booking_count: int
    recent_activity: List[RecentActivity]
    calendar_details: List[CalendarAppointment]
