from fastapi import FastAPI

app = FastAPI(title="AI Receptionist")

@app.get("/")
def read_root():
    return {"message": "Welcome to AI Receptionist"}

@app.get("/health")
def health_check():
    return {"status": "ok"}
