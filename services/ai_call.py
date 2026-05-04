import os
import httpx
from typing import Optional

# AI Model Configuration
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_URL = os.getenv("OPENAI_API_URL", "https://api.openai.com/v1/chat/completions")


async def generate_ai_response(user_message: str, system_context: Optional[str] = None) -> str:
    """
    Generates a conversational AI response using standard OpenAI API or simulated responses.
    """
    from services.prompts import system_prompt
    if system_context is None:
        system_context = system_prompt()

    # If an API key exists, call real OpenAI API
    if OPENAI_API_KEY:
        payload = {
            "model": "gpt-4o-mini",
            "messages": [
                {"role": "system", "content": system_context},
                {"role": "user", "content": user_message}
            ],
            "max_tokens": 150
        }
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(OPENAI_API_URL, json=payload, headers=headers, timeout=10.0)
                if response.status_code == 200:
                    return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"Error calling AI API: {e}")

    # Fallback/Mock conversational AI response logic
    msg_lower = user_message.lower()
    if "hello" in msg_lower or "hi" in msg_lower:
        return "Hello! Thank you for calling Voca AI Receptionist. How can I assist you today?"
    elif "appointment" in msg_lower or "book" in msg_lower:
        return "I'd be happy to help you book an appointment. May I know your name and preferred date?"
    elif "pricing" in msg_lower or "cost" in msg_lower:
        return "Our pricing starts at affordable tiers tailored to your needs. Shall I send you the details?"
    else:
        return "I understand. I will make sure our team follows up with you on this. Is there anything else I can assist with?"


async def process_incoming_call_event(stream_sid: str, audio_base64: str) -> Optional[str]:
    """
    Simulates processing a Twilio live audio stream chunk.
    It takes the base64 audio string, mimics STT processing, and returns a dynamic AI response.
    """
    if not audio_base64:
        return None

    print(f"[{stream_sid}] Received live audio chunk from caller.")
    
    # Simulate converting audio to text
    user_text = "Hi, I would like to schedule an appointment for next Monday."
    
    # Generate conversational AI response
    ai_text = await generate_ai_response(user_text)
    print(f"[{stream_sid}] AI generated conversational response: {ai_text}")
    return ai_text
