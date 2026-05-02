from fastapi import FastAPI, Depends
from database import engine, Base
from routers import ghl, twilio, auth
from routers.auth import get_current_user

# Create all database tables
Base.metadata.create_all(bind=engine)

app = FastAPI(title="AI Receptionist with Authentication")

# Register routers
app.include_router(auth.router)
app.include_router(ghl.router, dependencies=[Depends(get_current_user)])
app.include_router(twilio.router)

@app.get("/")
def read_root():
    return {"message": "Welcome to AI Receptionist with Authentication"}

@app.get("/health")
def health_check():
    return {"status": "ok"}
