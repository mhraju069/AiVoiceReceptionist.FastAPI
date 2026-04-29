from fastapi import APIRouter, HTTPException, Body
from schemas import ContactCreate, ContactUpdate
from services.ghl import add_contact, update_contact, get_contact

router = APIRouter(
    prefix="/api/contacts",
    tags=["Contacts"]
)

@router.post("/")
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

@router.put("/{contact_id}")
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

@router.get("/{contact_id}")
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
