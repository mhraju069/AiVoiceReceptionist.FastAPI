import os
import json
import base64
import asyncio
import httpx
from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect, HTTPException
import websockets

router = APIRouter(
    prefix="/api/twilio",
    tags=["Twilio Webhooks"]
)

# Configuration for Twilio API
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_SID = os.getenv("TWILIO_SID", "")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER", "")

# Configuration for real-time conversational AI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_WS_URL = os.getenv(
    "OPENAI_WS_URL", 
    "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17"
)


@router.post("/session")
async def create_session():
    """
    Generate an ephemeral session token for WebRTC/WebSocket real-time client use.
    """
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OpenAI API key is not set")

    from services.prompts import system_prompt
    
    url = "https://api.openai.com/v1/realtime/sessions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    
    data = {
        "model": "gpt-4o-realtime-preview-2024-12-17",
        "modalities": ["audio", "text"],
        "voice": "alloy",
        "instructions": system_prompt(),
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, json=data)
            if response.status_code != 200:
                raise HTTPException(status_code=response.status_code, detail=response.text)
            return response.json()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))



@router.post("/make-call")
async def make_outbound_call(request: Request):
    """
    Triggers an outbound call via Twilio and bridges it to the Realtime AI voice stream.
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    to_number = body.get("to")
    if not to_number:
        raise HTTPException(status_code=400, detail="Missing 'to' phone number")

    if not TWILIO_SID or not TWILIO_AUTH_TOKEN or not TWILIO_NUMBER:
        raise HTTPException(status_code=500, detail="Twilio credentials are not set")

    # Define the TwiML webhook URL for the outbound call
    host = request.headers.get("host", request.base_url.hostname)
    protocol = "https" if ("localhost" not in host or "ngrok" in host) else "http"
    if "ngrok" in host:
        protocol = "https"
    twiml_url = f"{protocol}://{host}/api/twilio/incoming-call"

    url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Calls.json"
    
    # Twilio uses Form URL Encoded data for outbound calls
    auth_header = base64.b64encode(f"{TWILIO_SID}:{TWILIO_AUTH_TOKEN}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    
    data = {
        "To": to_number,
        "From": TWILIO_NUMBER,
        "Url": twiml_url
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, data=data)
            if response.status_code not in (200, 201):
                raise HTTPException(status_code=response.status_code, detail=response.text)
            return response.json()
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))


@router.post("/incoming-call")
async def incoming_call(request: Request):
    """
    TwiML webhook for answering incoming calls and bridging to the WebSocket stream.
    """
    host = request.headers.get("host", request.base_url.hostname)
    print(f"\n📞 [Incoming Call] Received a call! Host header is: {host}")

    # Always wss for public/ngrok hosts, ws only for pure localhost
    is_local = host.startswith("localhost") or host.startswith("127.0.0.1")
    ws_protocol = "ws" if is_local else "wss"
    stream_url = f"{ws_protocol}://{host}/api/twilio/stream"
    print(f"📞 [Incoming Call] Bridging call to WebSocket: {stream_url}")

    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say language="en-US">Hello! Welcome to Voca AI. Please wait a moment while I connect you.</Say>
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
    print("\n🔌 [WebSocket] Twilio WebSocket connection accepted.")
    stream_sid = None
    openai_ws = None

    # Connect to OpenAI Realtime API if the API key is present
    if OPENAI_API_KEY:
        try:
            print("🤖 [OpenAI] Attempting to connect to OpenAI Realtime API...")
            headers = {
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1"
            }
            openai_ws = await websockets.connect(OPENAI_WS_URL, additional_headers=headers)
            print("🟢 [OpenAI] Successfully connected to OpenAI Realtime API.")

            from services.prompts import system_prompt

            # Send session configuration to OpenAI
            session_update = {
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "instructions": system_prompt(),
                    "voice": "alloy",
                    "input_audio_format": "g711_ulaw",
                    "output_audio_format": "g711_ulaw",
                    "input_audio_transcription": {
                        "model": "whisper-1"
                    },
                    # Auto-detect when the caller finishes speaking and trigger a response
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.7,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 600
                    },
                    "tools": [
                        {
                            "type": "function",
                            "name": "get_available_slots",
                            "description": "Fetch available booking slots for the next 7 days.",
                            "parameters": {
                                "type": "object",
                                "properties": {},
                                "required": []
                            }
                        },
                        {
                            "type": "function",
                            "name": "book_appointment",
                            "description": "Book an appointment for the caller. Call this ONLY after getting their name, email, phone, and requested time slot.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "name": {"type": "string"},
                                    "email": {"type": "string"},
                                    "phone": {"type": "string"},
                                    "booking_slot": {"type": "string", "description": "ISO date format like 2026-06-10T10:00:00Z"},
                                    "call_summary": {"type": "string"}
                                },
                                "required": ["name", "email", "phone", "booking_slot", "call_summary"]
                            }
                        }
                    ],
                    "tool_choice": "auto"
                }
            }
            await openai_ws.send(json.dumps(session_update))
            print("📝 [OpenAI] Sent session configuration update with turn detection.")

            # Trigger an initial greeting in Bangla immediately
            initial_greeting = {
                "type": "response.create",
                "response": {
                    "modalities": ["text", "audio"],
                    "instructions": "Greet the caller in Bangla warmly. Say: হ্যালো! আমি Voca AI এর AI রিসেপশনিস্ট। আমি আপনাকে কিভাবে সাহায্য করতে পারি?"
                }
            }
            await openai_ws.send(json.dumps(initial_greeting))
            print("🗣️ [OpenAI] Sent initial Bangla greeting trigger.")

        except Exception as e:
            print(f"🔴 [OpenAI] Error connecting to OpenAI Realtime API: {e}. Falling back to echo/mock.")
            openai_ws = None
    else:
        print("⚠️ [OpenAI] OPENAI_API_KEY not found. Operating in fallback mode.")

    async def receive_from_twilio():
        nonlocal stream_sid
        media_count = 0
        try:
            while True:
                message = await websocket.receive_text()
                data = json.loads(message)

                if data.get("event") == "start":
                    stream_sid = data["start"]["streamSid"]
                    print(f"🎬 [Twilio -> Server] Media stream started. Stream SID: [{stream_sid}]")

                elif data.get("event") == "media":
                    payload = data["media"]["payload"]
                    media_count += 1
                    
                    if media_count % 100 == 0:
                        print(f"🔊 [Twilio -> Server] Received {media_count} audio chunks from caller...")
                    
                    if openai_ws:
                        # Stream raw audio buffer directly to OpenAI
                        openai_payload = {
                            "type": "input_audio_buffer.append",
                            "audio": payload
                        }
                        await openai_ws.send(json.dumps(openai_payload))
                    else:
                        # Fallback/Mock - Echo back a tiny beep or silence to prove connection
                        if media_count % 50 == 0:
                            print(f"🛠️ [Mock Mode] Echoing dummy response for chunk {media_count}")
                            mock_response = {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {
                                    "payload": payload # Echoing back user audio as a test
                                }
                            }
                            await websocket.send_text(json.dumps(mock_response))

                elif data.get("event") == "stop":
                    print(f"🛑 [Twilio -> Server] Media stream stopped. Total chunks: {media_count}")
                    break

        except WebSocketDisconnect:
            print("⚠️ [Twilio WebSocket] Disconnected from Twilio.")
        except Exception as e:
            print(f"🔴 [Twilio WebSocket] Error reading from Twilio: {e}")

    async def send_to_twilio():
        nonlocal stream_sid
        if not openai_ws:
            return

        openai_media_count = 0
        try:
            async for openai_message in openai_ws:
                openai_data = json.loads(openai_message)

                # Process assistant's generated audio response
                if openai_data.get("type") == "response.audio.delta":
                    audio_chunk = openai_data["delta"]
                    openai_media_count += 1

                    if openai_media_count % 100 == 0:
                        print(f"🎙️ [OpenAI -> Server] Received {openai_media_count} audio chunks from AI...")

                    if stream_sid:
                        twilio_payload = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": audio_chunk
                            }
                        }
                        await websocket.send_text(json.dumps(twilio_payload))
                
                # Additional debug logging for other important OpenAI events
                elif openai_data.get("type") in ["response.text.done", "response.audio_transcript.done"]:
                    text = openai_data.get("text") or openai_data.get("transcript")
                    if text:
                        print(f"\n🤖 [AI Reply]: {text}")
                
                # Catch the user's speech transcript
                elif openai_data.get("type") == "conversation.item.input_audio_transcription.completed":
                    user_text = openai_data.get("transcript")
                    if user_text:
                        print(f"\n👤 [Caller]: {user_text}")

                # Handle tool calls
                elif openai_data.get("type") == "response.function_call_arguments.done":
                    func_name = openai_data.get("name")
                    call_id = openai_data.get("call_id")
                    args = json.loads(openai_data.get("arguments", "{}"))
                    print(f"\n🛠️ [OpenAI] AI called tool '{func_name}' with args: {args}")
                    
                    if func_name == "book_appointment":
                        from services.booking_service import book_appointment
                        try:
                            result = await book_appointment(
                                name=args.get("name", "Caller"),
                                email=args.get("email", ""),
                                phone=args.get("phone", ""),
                                booking_slot=args.get("booking_slot", ""),
                                call_summary=args.get("call_summary", ""),
                            )
                            print(f"✅ [OpenAI] Booking result: {result.get('status')}")
                        except Exception as e:
                            result = {"status": "error", "message": "Sorry, there was a technical issue booking your appointment. Please try again later."}
                            print(f"🔴 [OpenAI] Booking exception: {e}")
                    
                    elif func_name == "get_available_slots":
                        from services.booking_service import get_slots
                        try:
                            result = await get_slots()
                            print(f"✅ [OpenAI] Slots fetched successfully.")
                        except Exception as e:
                            result = {"status": "error", "message": "Could not fetch available slots."}
                            print(f"🔴 [OpenAI] Slot fetch exception: {e}")
                    
                    # Send output back to OpenAI
                    await openai_ws.send(json.dumps({
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": call_id,
                            "output": json.dumps(result)
                        }
                    }))
                    # Trigger response
                    await openai_ws.send(json.dumps({"type": "response.create"}))

        except Exception as e:
            print(f"🔴 [OpenAI -> Twilio] Error streaming response from OpenAI to Twilio: {e}")

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
