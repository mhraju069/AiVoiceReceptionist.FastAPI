from fastapi import APIRouter, HTTPException, Body
from schemas import *
from services.ghl import *

router = APIRouter(
    prefix="/api/ghl",
    tags=["GoHighLevel"]
)

@router.post("/contacts/")
async def create_new_contact(contact: ContactCreate):
    """
    Create a new contact in GoHighLevel
    """
    try:
        response = await add_contact(contact)
        return response
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/contacts/{contact_id}")
async def update_existing_contact(contact_id: str, contact: ContactUpdate):
    """
    Update an existing contact in GoHighLevel
    """
    try:
        response = await update_contact(contact_id, contact)
        return response
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/contacts/{contact_id}")
async def fetch_contact(contact_id: str):

    """
    Get a contact from GoHighLevel by ID
    """
    try:
        response = await get_contact(contact_id)
        return response
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.post("/appointments/")
async def schedule_appointment(appointment: AppointmentCreate):
    """
    Schedule a new appointment in GoHighLevel
    """
    try:
        response = await create_appointment(appointment)
        return response
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@router.put("/appointments/{appointment_id}")
async def update_existing_appointment(appointment_id: str, appointment: AppointmentUpdate):
    """
    Update an existing appointment in GoHighLevel
    """
    try:
        response = await update_appointment(appointment_id, appointment)
        return response
    except HTTPException as e:
        raise e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
