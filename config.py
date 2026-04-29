import os
from dotenv import load_dotenv

load_dotenv()

GHL_BASE_URL = os.getenv("GHL_BASE_URL", "https://rest.gohighlevel.com/v1")
GHL_API_KEY = os.getenv("GHL_API_KEY")
GHL_LOCATION_ID = os.getenv("GHL_LOCATION_ID")