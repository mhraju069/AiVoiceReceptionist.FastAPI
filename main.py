from fastapi import FastAPI
from routers import ghl

app = FastAPI(title="AI Receptionist")

app.include_router(ghl.router)

@app.get("/")
def read_root():
    return {"message": "Welcome to AI Receptionist"}

@app.get("/health")
def health_check():
    return {"status": "ok"}

