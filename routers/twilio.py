import logging
logger = logging.getLogger(__name__)

import os,json,base64,asyncio,httpx,datetime
from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect, HTTPException
import websockets
from database import SessionLocal
from models.activity_models import CallLog
from config import FORWARD_SIMON, FORWARD_TANZINA, FORWARD_ALEX, FORWARD_NAFI

router = APIRouter(
    prefix="/api/twilio",
    tags=["Twilio Webhooks"]
)

# Configuration for Twilio API
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_SID = os.getenv("TWILIO_SID", "")
TWILIO_NUMBER = os.getenv("TWILIO_NUMBER", "")

# Map of forward targets to phone numbers
FORWARD_MAP = {
    "simon":   FORWARD_SIMON,
    "tanzina": FORWARD_TANZINA,
    "alex":    FORWARD_ALEX,
    "nafi":    FORWARD_NAFI,
}

# Configuration for real-time conversational AI
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_WS_URL = os.getenv(
    "OPENAI_WS_URL", 
    "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"
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
        "model": "gpt-4o-realtime-preview",
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


@router.post("/forward-call")
async def forward_call(request: Request):
    """
    TwiML endpoint Twilio calls when redirecting a call to a team member.
    Reads ?to= from query params and dials that number.
    """
    to_number = request.query_params.get("to", "")
    if not to_number:
        twiml = """<?xml version="1.0" encoding="UTF-8"?><Response><Say>Sorry, we could not connect your call at this time. Please try again later.</Say></Response>"""
    else:
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Dial callerId="{TWILIO_NUMBER}" timeout="30" action="/api/twilio/forward-fallback">
        <Number>{to_number}</Number>
    </Dial>
</Response>"""
    return Response(content=twiml, media_type="text/xml")


@router.post("/forward-fallback")
async def forward_fallback(request: Request):
    """
    TwiML endpoint called when the forwarded call ends or is not answered.
    Plays a fallback message.
    """
    twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say>I'm sorry, the team member is not available right now. Please leave your details and we will call you back shortly.</Say>
</Response>"""
    return Response(content=twiml, media_type="text/xml")



@router.post("/incoming-call")
async def incoming_call(request: Request):
    host = request.headers.get("host", str(request.base_url.hostname))
    form_data = await request.form()
    caller_number = form_data.get("From", "Unknown")
    from services.ghl import get_contact_profile_by_phone
    import urllib.parse

    # Full GHL profile lookup
    contact_name = "Prospect"
    client_type = "Prospect"
    group = ""
    contact_id = ""
    invoice_due = "false"
    try:
        profile = await get_contact_profile_by_phone(caller_number)
        if profile.get("found"):
            contact_name = profile.get("name", "Client")
            client_type = profile.get("client_type", "Prospect")
            group = profile.get("group") or ""
            contact_id = profile.get("contact_id") or ""
            invoice_due = "true" if profile.get("invoice_due") else "false"
            logger.info(f"📌 [GHL] {caller_number} -> {contact_name} | {client_type} | Group:{group} | Invoice:{invoice_due}")
    except Exception as e:
        logger.error(f"Error fetching contact profile: {e}")

    # Always wss for public/ngrok hosts, ws only for pure localhost
    is_local = host.startswith("localhost") or host.startswith("127.0.0.1")
    ws_protocol = "ws" if is_local else "wss"
    params = urllib.parse.urlencode({
        "caller_number": caller_number,
        "contact_name": contact_name,
        "client_type": client_type,
        "group": group,
        "contact_id": contact_id,
        "invoice_due": invoice_due,
    })
    stream_url = f"{ws_protocol}://{host}/api/twilio/stream?{params}"
    logger.info(f"📞 [Incoming] {caller_number} ({contact_name}) -> {stream_url}")

    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
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
    caller_number = websocket.query_params.get("caller_number", "Unknown")
    contact_name  = websocket.query_params.get("contact_name", "Prospect")
    client_type   = websocket.query_params.get("client_type", "Prospect")
    group         = websocket.query_params.get("group", "")
    contact_id    = websocket.query_params.get("contact_id", "")
    invoice_due   = websocket.query_params.get("invoice_due", "false") == "true"
    logger.info(f"\n🎙️ [WebSocket] {caller_number} | {contact_name} | {client_type} | Group:{group} | Invoice:{invoice_due}")
    # Metadata for call logging
    stream_sid = None
    call_sid = None
    transcript_accumulator = []
    openai_ws = None
    start_time_dt = datetime.datetime.utcnow()

    # Connect to OpenAI Realtime API if the API key is present
    if OPENAI_API_KEY:
        try:
            logger.info("🤖 [OpenAI] Attempting to connect to OpenAI Realtime API...")
            headers = {
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1"
            }
            openai_ws = await websockets.connect(OPENAI_WS_URL, additional_headers=headers)
            logger.info("🟢 [OpenAI] Successfully connected to OpenAI Realtime API.")

            from services.prompts import system_prompt

            # Send session configuration to OpenAI
            session_update = {
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "instructions": system_prompt() + f"""

                    # CALLER CRM PROFILE (Pre-loaded from GHL)
                    Caller Name: {contact_name}
                    Client Type: {client_type}
                    Group: {group if group else 'Unknown'}
                    Invoice Due: {'Yes' if invoice_due else 'No'}
                    Phone: {caller_number}

                    Use this information immediately:
                    - If Client Type is 'Class A/B/C/D Client', treat as VIP and prioritize Simon transfer.
                    - If Invoice Due is Yes, include it in the internal staff handoff note only. Never mention invoices or billing to the caller directly.
                    - If the caller is a known client, address them by name without asking.
                    - If Prospect, follow the standard intro and qualification workflow.
                    """,
                    
                    "voice": "alloy",
                    "input_audio_format": "g711_ulaw",
                    "output_audio_format": "g711_ulaw",
                    "input_audio_transcription": {
                        "model": "whisper-1"
                    },
                    # Auto-detect when the caller finishes speaking and trigger a response
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 400,
                        "silence_duration_ms": 300,
                        "create_response": True,
                    },
                    "tools": [
                        {
                            "type": "function",
                            "name": "get_available_slots",
                            "description": "Fetch available booking slots for the next 7 days.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "calendar_type": {
                                        "type": "string", 
                                        "enum": ["follow_up_c", "follow_up_b", "virtual_consult_15", "virtual_cpa_45", "office_cpa_45", "test_calendar"],
                                        "description": "The type of meeting to check slots for"
                                    }
                                },
                                "required": ["calendar_type"]
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
                                    "calendar_type": {
                                        "type": "string", 
                                        "enum": ["follow_up_c", "follow_up_b", "virtual_consult_15", "virtual_cpa_45", "office_cpa_45", "test_calendar"],
                                        "description": "The type of meeting the user selected"
                                    },
                                    "call_summary": {"type": "string"}
                                },
                                "required": ["name", "email", "phone", "booking_slot", "calendar_type", "call_summary"]
                            }
                        },
                        {
                            "type": "function",
                            "name": "transfer_call",
                            "description": "Transfer the current live call to a human team member. Use this when the caller is a VIP/class client or explicitly requests to speak to someone.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "target": {
                                        "type": "string",
                                        "enum": ["simon", "tanzina", "alex", "nafi"],
                                        "description": "The team member to transfer the call to. Use 'simon' for VIP/class clients. Use 'tanzina', 'alex', or 'nafi' for standard transfers."
                                    },
                                    "reason": {
                                        "type": "string",
                                        "description": "Brief reason for the transfer."
                                    }
                                },
                                "required": ["target", "reason"]
                            }
                        },
                        {
                            "type": "function",
                            "name": "record_message",
                            "description": "Record a caller's message in the CRM when no team member is available. Call this after the caller leaves their message.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "caller_name":   {"type": "string", "description": "Name of the caller"},
                                    "caller_phone":  {"type": "string", "description": "Phone number of the caller"},
                                    "message":       {"type": "string", "description": "The message or reason the caller left"},
                                    "call_reason":   {"type": "string", "description": "Category: personal_tax, business_tax, notice, appointment, other"}
                                },
                                "required": ["caller_name", "caller_phone", "message", "call_reason"]
                            }
                        }
                    ],
                    "tool_choice": "auto"
                }
            }
            await openai_ws.send(json.dumps(session_update))
            logger.info("📝 [OpenAI] Sent session configuration update with turn detection.")

            # Trigger initial greeting based on caller profile
            is_known = client_type != "Prospect"
            if is_known:
                greeting_instruction = (
                    f"The caller is {contact_name}, a {client_type} of Pay Minimum Tax. "
                    f"Greet them with an Islamic greeting by name in Dhaka Bangla. "
                    f"Say: আসসালামু আলাইকুম {contact_name} ভাই, আমি রেবা, আপনার জন্য আজ কি করতে পারি?"
                )
            else:
                greeting_instruction = (
                    "The caller is a new prospect. "
                    "Greet them using the greeting specified in the GREETING section of your system instructions. Speak naturally and warmly."
                )
            initial_greeting = {
                "type": "response.create",
                "response": {
                    "modalities": ["text", "audio"],
                    "instructions": greeting_instruction
                }
            }
            await openai_ws.send(json.dumps(initial_greeting))
            logger.info("🗣️ [OpenAI] Sent initial Bangla greeting trigger.")

        except Exception as e:
            logger.info(f"🔴 [OpenAI] Error connecting to OpenAI Realtime API: {e}. Falling back to echo/mock.")
            openai_ws = None
    else:
        logger.info("⚠️ [OpenAI] OPENAI_API_KEY not found. Operating in fallback mode.")

    async def receive_from_twilio():
        nonlocal stream_sid, call_sid, caller_number
        media_count = 0
        try:
            while True:
                message = await websocket.receive_text()
                data = json.loads(message)

                if data.get("event") == "start":
                    stream_sid = data["start"]["streamSid"]
                    call_sid = data["start"].get("callSid")
                    # Use caller_number from query params as primary, but check custom params too
                    new_caller = data["start"].get("customParameters", {}).get("callerNumber")
                    if new_caller and new_caller != "Unknown":
                        caller_number = new_caller
                    logger.info(f"🎬 [Twilio -> Server] Media stream started. Stream SID: [{stream_sid}], Call SID: [{call_sid}], Caller: [{caller_number}]")

                elif data.get("event") == "media":
                    payload = data["media"]["payload"]
                    media_count += 1
                    
                    if media_count % 100 == 0:
                        logger.info(f"🔊 [Twilio -> Server] Received {media_count} audio chunks from caller...")
                    
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
                            logger.info(f"🛠️ [Mock Mode] Echoing dummy response for chunk {media_count}")
                            mock_response = {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {
                                    "payload": payload # Echoing back user audio as a test
                                }
                            }
                            await websocket.send_text(json.dumps(mock_response))

                elif data.get("event") == "stop":
                    logger.info(f"🛑 [Twilio -> Server] Media stream stopped. Total chunks: {media_count}")
                    break

        except WebSocketDisconnect:
            logger.info("⚠️ [Twilio WebSocket] Disconnected from Twilio.")
        except Exception as e:
            logger.info(f"🔴 [Twilio WebSocket] Error reading from Twilio: {e}")

    async def send_to_twilio():
        nonlocal stream_sid
        if not openai_ws:
            return

        openai_media_count = 0
        current_response_id = None   # Track which response is actively generating
        last_assistant_item_id = None  # Track current assistant item for truncation
        response_audio_sent_ms = 0   # Cumulative ms of audio sent to Twilio
        is_interrupted = False       # Flag to immediately drop audio on interruption
        try:
            async for openai_message in openai_ws:
                openai_data = json.loads(openai_message)
                event_type = openai_data.get("type", "")

                # Track when a new response starts generating
                if event_type == "response.created":
                    resp_obj = openai_data.get("response", {})
                    current_response_id = resp_obj.get("id")
                    is_interrupted = False  # Allow audio for this new response
                    response_audio_sent_ms = 0  # Reset audio counter
                    logger.info(f"🟢 [OpenAI] New response started: {current_response_id}")

                # Process assistant's generated audio response
                elif event_type == "response.audio.delta":
                    # DROP all audio chunks if caller interrupted
                    if is_interrupted:
                        continue

                    # Track item_id for conversation truncation on interruption
                    item_id = openai_data.get("item_id")
                    if item_id:
                        last_assistant_item_id = item_id

                    audio_chunk = openai_data["delta"]
                    openai_media_count += 1

                    # Track audio duration for accurate truncation
                    # G.711 μ-law: 8000 Hz, 1 byte/sample → 8 bytes per ms
                    try:
                        raw_bytes = len(base64.b64decode(audio_chunk))
                        response_audio_sent_ms += raw_bytes / 8
                    except Exception:
                        response_audio_sent_ms += 20  # ~20ms fallback

                    if openai_media_count % 100 == 0:
                        logger.info(f"🎙️ [OpenAI -> Server] Received {openai_media_count} audio chunks from AI...")

                    if stream_sid:
                        twilio_payload = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": audio_chunk
                            }
                        }
                        await websocket.send_text(json.dumps(twilio_payload))

                # Handle user interruption: Stop AI and clear Twilio buffer
                elif event_type == "input_audio_buffer.speech_started":
                    logger.info("🛑 [OpenAI] User interrupted! Stopping AI immediately...")
                    is_interrupted = True

                    # 1) Clear Twilio's playback buffer FIRST — stop caller hearing AI
                    if stream_sid:
                        await websocket.send_text(json.dumps({
                            "event": "clear",
                            "streamSid": stream_sid
                        }))
                        logger.info("🧹 [Twilio] Cleared audio playback buffer.")

                    # 2) Cancel the current OpenAI response generation
                    try:
                        await openai_ws.send(json.dumps({"type": "response.cancel"}))
                        logger.info("🛑 [OpenAI] Sent response.cancel to stop AI generation.")
                    except Exception as cancel_err:
                        logger.error(f"🔴 [OpenAI] Failed to send response.cancel: {cancel_err}")

                    # 3) Truncate the conversation so OpenAI only remembers what
                    #    the caller actually heard (not the full unplayed response)
                    if last_assistant_item_id:
                        try:
                            await openai_ws.send(json.dumps({
                                "type": "conversation.item.truncate",
                                "item_id": last_assistant_item_id,
                                "content_index": 0,
                                "audio_end_ms": int(response_audio_sent_ms)
                            }))
                            logger.info(f"✂️ [OpenAI] Truncated item at {int(response_audio_sent_ms)}ms")
                        except Exception as trunc_err:
                            logger.error(f"🔴 [OpenAI] Failed to truncate: {trunc_err}")

                # Log when a response finishes
                elif event_type in ("response.done", "response.cancelled"):
                    resp_obj = openai_data.get("response", {})
                    done_id = resp_obj.get("id") or current_response_id
                    logger.info(f"✅ [OpenAI] Response {done_id} finished ({event_type}).")
                
                # Additional debug logging for other important OpenAI events
                elif event_type in ("response.text.done", "response.audio_transcript.done"):
                    text = openai_data.get("text") or openai_data.get("transcript")
                    if text:
                        logger.info(f"\n🤖 [AI Reply]: {text}")
                        transcript_accumulator.append(f"AI: {text}")
                
                # Catch the user's speech transcript
                elif event_type == "conversation.item.input_audio_transcription.completed":
                    user_text = openai_data.get("transcript")
                    if user_text:
                        logger.info(f"\n👤 [Caller]: {user_text}")
                        transcript_accumulator.append(f"Caller: {user_text}")

                # Handle tool calls
                elif event_type == "response.function_call_arguments.done":
                    func_name = openai_data.get("name")
                    call_id = openai_data.get("call_id")
                    args = json.loads(openai_data.get("arguments", "{}"))
                    logger.info(f"\n🛠️ [OpenAI] AI called tool '{func_name}' with args: {args}")
                    
                    if func_name == "book_appointment":
                        from services.booking_service import book_appointment
                        try:
                            result = await book_appointment(
                                name=args.get("name", "Caller"),
                                email=args.get("email", ""),
                                phone=args.get("phone", ""),
                                booking_slot=args.get("booking_slot", ""),
                                calendar_type=args.get("calendar_type", "follow_up_b"),
                                call_summary=args.get("call_summary", ""),
                            )
                            logger.info(f"✅ [OpenAI] Booking result: {result.get('status')}")
                        except Exception as e:
                            result = {"status": "error", "message": "Sorry, there was a technical issue booking your appointment. Please try again later."}
                            logger.info(f"🔴 [OpenAI] Booking exception: {e}")
                    
                    elif func_name == "get_available_slots":
                        from services.booking_service import get_slots
                        try:
                            result = await get_slots(
                                calendar_type=args.get("calendar_type", "follow_up_b")
                            )
                            logger.info(f"✅ [OpenAI] Slots fetched successfully.")
                        except Exception as e:
                            result = {"status": "error", "message": "Could not fetch available slots."}
                            logger.info(f"🔴 [OpenAI] Slot fetch exception: {e}")

                    elif func_name == "transfer_call":
                        target = args.get("target", "tanzina").lower()
                        reason = args.get("reason", "")
                        to_number = FORWARD_MAP.get(target, "")
                        logger.info(f"📲 [Transfer] AI requested transfer to '{target}' ({to_number}). Reason: {reason}")

                        if not to_number:
                            result = {"status": "error", "message": f"No phone number configured for {target}."}
                            logger.error(f"🔴 [Transfer] No number configured for '{target}'.")
                        elif not call_sid:
                            result = {"status": "error", "message": "Call SID not available yet to redirect."}
                            logger.error("🔴 [Transfer] call_sid is None, cannot redirect.")
                        else:
                            try:
                                # Build the forward TwiML URL — must be publicly accessible
                                host = os.getenv("PUBLIC_HOST", "")
                                forward_url = f"https://{host}/api/twilio/forward-call?to={to_number}"
                                redirect_payload = {"Url": forward_url, "Method": "POST"}
                                redirect_url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Calls/{call_sid}.json"
                                auth = (TWILIO_SID, TWILIO_AUTH_TOKEN)
                                async with httpx.AsyncClient() as client:
                                    resp = await client.post(redirect_url, data=redirect_payload, auth=auth)
                                if resp.status_code == 200:
                                    result = {"status": "success", "message": f"Transferring call to {target}."}
                                    logger.info(f"✅ [Transfer] Successfully redirected call to {target}.")
                                else:
                                    result = {"status": "error", "message": f"Twilio redirect failed: {resp.text}"}
                                    logger.error(f"🔴 [Transfer] Twilio API error: {resp.text}")
                            except Exception as e:
                                result = {"status": "error", "message": "Transfer failed due to a technical error."}
                                logger.error(f"🔴 [Transfer] Exception during transfer: {e}")

                    elif func_name == "record_message":
                        caller_name_arg  = args.get("caller_name", contact_name)
                        caller_phone_arg = args.get("caller_phone", caller_number)
                        message_text     = args.get("message", "")
                        call_reason_arg  = args.get("call_reason", "other")
                        logger.info(f"📝 [CRM] Recording message from {caller_name_arg}: {message_text}")
                        note = (
                            f"📞 Missed Call Note\n"
                            f"Name: {caller_name_arg}\n"
                            f"Phone: {caller_phone_arg}\n"
                            f"Reason: {call_reason_arg}\n"
                            f"Message: {message_text}"
                        )
                        from services.ghl import add_crm_note
                        saved = await add_crm_note(contact_id, note)
                        if saved:
                            result = {"status": "success", "message": "Message recorded in CRM."}
                        else:
                            logger.info(f"📝 [CRM] No contact ID, logging locally: {note}")
                            result = {"status": "success", "message": "Message noted. Team will follow up."}

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
            logger.info(f"🔴 [OpenAI -> Twilio] Error streaming response from OpenAI to Twilio: {e}")

    # Orchestrate bidirectional async tasks
    try:
        await asyncio.gather(
            receive_from_twilio(),
            send_to_twilio()
        )
    finally:
        if openai_ws:
            await openai_ws.close()
        
        # Save Call Log to Database
        if transcript_accumulator:
            full_transcript = "\n".join(transcript_accumulator)
            try:
                # Generate AI Summary/Outcome
                summary = "AI handled the call."
                intent = "General Inquiry"
                outcome = "Completed"
                
                if OPENAI_API_KEY:
                    from services.ai_call import generate_ai_response
                    analysis_prompt = f"""
                    Analyze this call transcript between an AI Receptionist and a Caller.
                    Return a JSON object with: 
                    "summary" (concise 2-3 sentences), 
                    "reason" (EXACTLY 2-3 words summary of the call purpose),
                    "intent" (short string like "Tax Preparation"), 
                    "outcome" (If an appointment was booked, the date like "May 12", else "Completed", "Inquiry", etc.),
                    "lead_status" (one of: Qualified Lead, Warm Lead, Cold Lead),
                    "tags" (list of strings).
                    
                    Transcript:
                    {full_transcript}
                    """
                    try:
                        analysis_raw = await generate_ai_response(analysis_prompt, system_context="You are a call analyst. Return JSON ONLY.")
                        # Strip markdown if present
                        if "```json" in analysis_raw:
                            analysis_raw = analysis_raw.split("```json")[1].split("```")[0].strip()
                        analysis = json.loads(analysis_raw)
                        summary = analysis.get("summary", summary)
                        reason = analysis.get("reason", "Inquiry")
                        intent = analysis.get("intent", intent)
                        outcome = analysis.get("outcome", outcome)
                        lead_status = analysis.get("lead_status")
                        tags = ",".join(analysis.get("tags", []))
                    except Exception as e:
                        reason = "Inquiry"
                        lead_status = "Inquiry"
                        tags = ""

                db = SessionLocal()
                try:
                    new_log = CallLog(
                        call_sid=call_sid or stream_sid,
                        caller_number=caller_number,
                        transcript=full_transcript,
                        summary=summary,
                        reason=reason,
                        intent=intent,
                        outcome=outcome,
                        lead_status=lead_status,
                        tags=tags,
                        start_time=start_time_dt,
                        end_time=datetime.datetime.utcnow(),
                        duration=int((datetime.datetime.utcnow() - start_time_dt).total_seconds())
                    )
                    db.add(new_log)
                    db.commit()
                    logger.info(f"💾 [Database] Call log saved for SID: {call_sid or stream_sid}")
                except Exception as db_err:
                    logger.error(f"🔴 [Database] Error saving call log: {db_err}")
                    db.rollback()
                finally:
                    db.close()
            except Exception as e:
                logger.error(f"🔴 [Database] Error in post-call processing: {e}")

        logger.info("Bidirectional voice session closed.")
