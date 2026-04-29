from fastapi import FastAPI
from routers import ghl, twilio

app = FastAPI(title="AI Receptionist")

app.include_router(ghl.router)
app.include_router(twilio.router)

@app.get("/")
def read_root():
    return {"message": "Welcome to AI Receptionist"}

@app.get("/health")
def health_check():
    return {"status": "ok"}

