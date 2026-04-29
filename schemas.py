from pydantic import BaseModel, EmailStr
from typing import Optional, List, Dict, Any



class ContactCreate(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    name: Optional[str] = None
    dateOfBirth: Optional[str] = None
    address1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postalCode: Optional[str] = None
    website: Optional[str] = None
    timezone: Optional[str] = None
    tags: Optional[List[str]] = None
    source: Optional[str] = None



class ContactUpdate(BaseModel):
    email: Optional[EmailStr] = None
    phone: Optional[str] = None
    firstName: Optional[str] = None
    lastName: Optional[str] = None
    name: Optional[str] = None
    dateOfBirth: Optional[str] = None
    address1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postalCode: Optional[str] = None
    website: Optional[str] = None
    timezone: Optional[str] = None
    tags: Optional[List[str]] = None
    source: Optional[str] = None
