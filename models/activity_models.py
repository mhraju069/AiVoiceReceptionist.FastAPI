import datetime
from sqlalchemy import Column, Integer, String, DateTime
from database import Base

class Activity(Base):
    __tablename__ = "activities"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(String, nullable=False)
    time = Column(String, nullable=False)  # Storing as string e.g. "10:00" or datetime
    created_at = Column(DateTime, default=datetime.datetime.utcnow)
