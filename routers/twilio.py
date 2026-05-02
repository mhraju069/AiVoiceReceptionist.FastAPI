import os
import json
import base64
import asyncio
from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect
import websockets

router = APIRouter(
    prefix="/api/twilio",
    tags=["Twilio Webhooks"]
)

# Configuration for real-time conversational AI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_WS_URL = os.getenv(
    "OPENAI_WS_URL", 
    "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-10-01"
)


@router.post("/incoming-call")
async def incoming_call(request: Request):
    """
    TwiML webhook for answering incoming calls and bridging to the WebSocket stream.
    """
    host = request.headers.get("host", request.base_url.hostname)
    protocol = "wss" if "localhost" not in host else "ws"
    stream_url = f"{protocol}://{host}/api/twilio/stream"

    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="alice">Connecting you to the AI receptionist.</Say>
    <Connect>
        <Stream url="{stream_url}" />
    </Connect>
</Response>"""

    return Response(content=twiml_response, media_type="text/xml")


@router.websocket("/stream")
async def twilio_stream(websocket: WebSocket):
    """
    Full-duplex WebSocket endpoint for routing live bidirectional audio 
    between Twilio and the OpenAI Realtime API.
    """
    await websocket.accept()
    stream_sid = None
    openai_ws = None

    # Connect to OpenAI Realtime API if the API key is present
    if OPENAI_API_KEY:
        try:
            headers = {
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1"
            }
            openai_ws = await websockets.connect(OPENAI_WS_URL, extra_headers=headers)
            print("Successfully connected to OpenAI Realtime API.")

            # Send session configuration to OpenAI
            session_update = {
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "instructions": (
                        "You are a professional AI receptionist. "
                        "Keep answers short, clear, and natural for telephone speech."
                    ),
                    "voice": "alloy",
                    "input_audio_format": "g711_ulaw",
                    "output_audio_format": "g711_ulaw"
                }
            }
            await openai_ws.send(json.dumps(session_update))

        except Exception as e:
            print(f"Error connecting to OpenAI Realtime API: {e}. Falling back to echo/mock.")
            openai_ws = None

    async def receive_from_twilio():
        nonlocal stream_sid
        try:
            while True:
                message = await websocket.receive_text()
                data = json.loads(message)

                if data.get("event") == "start":
                    stream_sid = data["start"]["streamSid"]
                    print(f"[{stream_sid}] Media stream started.")

                elif data.get("event") == "media":
                    payload = data["media"]["payload"]
                    
                    if openai_ws:
                        # Stream raw audio buffer directly to OpenAI
                        openai_payload = {
                            "type": "input_audio_buffer.append",
                            "audio": payload
                        }
                        await openai_ws.send(json.dumps(openai_payload))
                    else:
                        # Fallback/Mock - Echo or log the live audio input
                        pass

                elif data.get("event") == "stop":
                    print(f"[{stream_sid}] Media stream stopped.")
                    break

        except WebSocketDisconnect:
            print("Twilio WebSocket disconnected.")
        except Exception as e:
            print(f"Error reading from Twilio: {e}")

    async def send_to_twilio():
        nonlocal stream_sid
        if not openai_ws:
            return

        try:
            async for openai_message in openai_ws:
                openai_data = json.loads(openai_message)

                # Process assistant's generated audio response
                if openai_data.get("type") == "response.audio.delta":
                    audio_chunk = openai_data["delta"]

                    if stream_sid:
                        twilio_payload = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": audio_chunk
                            }
                        }
                        await websocket.send_text(json.dumps(twilio_payload))

        except Exception as e:
            print(f"Error streaming response from OpenAI to Twilio: {e}")

    # Orchestrate bidirectional async tasks
    try:
        await asyncio.gather(
            receive_from_twilio(),
            send_to_twilio()
        )
    finally:
        if openai_ws:
            await openai_ws.close()
        print("Bidirectional voice session closed.")
