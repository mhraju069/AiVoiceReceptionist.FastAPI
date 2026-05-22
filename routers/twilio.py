import logging
logger = logging.getLogger(__name__)

import os,json,base64,asyncio,httpx,datetime,html,random,re,urllib.parse
from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect, HTTPException
import websockets
from database import SessionLocal
from models.activity_models import CallLog
from config import FORWARD_SIMON, FORWARD_TANZINA, FORWARD_ALEX, FORWARD_NAFI
from services.known_clients import find_known_client_by_phone, profile_from_known_client
from services.openai_realtime import get_openai_realtime_model, get_openai_realtime_ws_url
from routers.common_tools import (
    ADS,
    handle_book_appointment,
    handle_get_slots,
    handle_transfer_call,
    handle_end_call,
    handle_send_link_sms,
    handle_record_message,
)

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
OPENAI_REALTIME_MODEL = get_openai_realtime_model()
OPENAI_WS_URL = get_openai_realtime_ws_url()

IGNORED_TRANSCRIPT_WORDS = {
    "hello",
    "hi",
    "hey",
    "hmm",
    "um",
    "uh",
    "hola",
}

MEANINGFUL_SHORT_WORDS = {
    "yes",
    "yeah",
    "yep",
    "no",
    "ok",
    "okay",
    "bye",
}


END_CALL_CONSENT_WORDS = {
    "yes",
    "yeah",
    "yep",
    "ya",
    "ok",
    "okay",
    "sure",
    "ji",
    "jee",
    "jii",
    "ha",
    "haan",
    "hya",
    "acha",
    "accha",
    "kato",
    "katen",
    "katun",
    "cut",
    "bye",
}

END_CALL_CONSENT_PHRASES = (
    "go ahead",
    "you can",
    "cut it",
    "cut the call",
    "end it",
    "end the call",
    "hang up",
    "disconnect",
    "no more",
    "nothing else",
    "that's all",
    "thats all",
    "all good",
    "kete dao",
    "kete den",
    "kete din",
    "cut kore den",
    "kat kore den",
    "কেটে দাও",
    "কেটে দেন",
    "কেটে দিন",
    "কল কেটে",
    "কলটা কেটে",
    "আর কিছু না",
    "আর লাগবে না",
    "কিছু লাগবে না",
)

END_CALL_BANGLA_CONSENT = (
    "হ্যাঁ",
    "হ্যা",
    "হা",
    "জি",
    "জী",
    "ঠিক আছে",
    "আচ্ছা",
    "কাটো",
    "কাটেন",
    "কাটুন",
    "কেটে",
    "শেষ",
)

END_CALL_PERMISSION_CUES = (
    "permission to end",
    "permission to hang up",
    "can i end",
    "may i end",
    "end the call now",
    "hang up now",
    "disconnect now",
    "কল শেষ",
    "কলটা শেষ",
    "কল কেটে",
    "কলটা কেটে",
    "শেষ করতে",
    "কেটে দিতে",
    "কেটে দিই",
    "কেটে দিতে পারি",
    "শেষ করে দিই",
    "শেষ করব",
)


def _normalized_words(text: str) -> list[str]:
    cleaned = re.sub(r"[^A-Za-z0-9\u0980-\u09FF\s']", " ", text or "").lower()
    return [word for word in cleaned.split() if word]


def _normalized_text(text: str) -> str:
    return " ".join(_normalized_words(text))


def _asks_end_call_permission(text: str) -> bool:
    lowered = (text or "").lower()
    normalized = _normalized_text(text)
    return any(cue in lowered or cue in normalized for cue in END_CALL_PERMISSION_CUES)


def _is_end_call_consent(text: str) -> bool:
    lowered = (text or "").lower()
    normalized = _normalized_text(text)
    words = set(normalized.split())

    if words & END_CALL_CONSENT_WORDS:
        return True
    if any(phrase in lowered or phrase in normalized for phrase in END_CALL_CONSENT_PHRASES):
        return True
    if any(phrase in (text or "") for phrase in END_CALL_BANGLA_CONSENT):
        return True
    return False


def _is_meaningful_transcript(text: str) -> bool:
    cleaned = re.sub(r"[^A-Za-z0-9\u0980-\u09FF\s]", " ", text or "").strip()
    if not cleaned:
        return False

    words = [word.lower() for word in cleaned.split()]
    if not words:
        return False

    has_bangla = any("\u0980" <= char <= "\u09FF" for char in cleaned)
    has_alnum = any(char.isalnum() for char in cleaned)
    if not has_bangla and not has_alnum:
        return False

    if len(words) == 1 and words[0] in MEANINGFUL_SHORT_WORDS:
        return True
    if len(words) == 1 and words[0] in IGNORED_TRANSCRIPT_WORDS:
        return False
    if len(cleaned) < 4 and not has_bangla:
        return False
    return True


@router.post("/session")
async def create_session():
    """
    Generate an ephemeral session token for WebRTC/WebSocket real-time client use.
    """
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OpenAI API key is not set")

    from services.prompts import system_prompt
    instructions, _ = system_prompt()
    
    url = "https://api.openai.com/v1/realtime/sessions"
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    
    data = {
        "type": "realtime",
        "model": OPENAI_REALTIME_MODEL,
        "output_modalities": ["audio"],
        "instructions": instructions,
        "audio": {
            "output": {
                "voice": "shimmer",
            },
        },
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





def _public_base_url(host: str, default_protocol: str = "https") -> str:
    host = (host or "").strip().rstrip("/")
    if not host:
        return ""
    if host.startswith(("http://", "https://")):
        return host

    is_local = host.startswith("localhost") or host.startswith("127.0.0.1")
    protocol = "http" if is_local else default_protocol
    return f"{protocol}://{host}"


def _public_twilio_url(host: str, path: str, query: dict | None = None) -> str:
    base_url = _public_base_url(host)
    if not base_url:
        return ""

    path = path if path.startswith("/") else f"/{path}"
    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{urllib.parse.urlencode(query)}"
    return url


def _forward_number_from_query(raw_number: str) -> str:
    raw_number = raw_number or ""
    # Repair old unescaped URLs where "+1555..." arrived from the query as " 1555...".
    if raw_number.startswith(" ") and raw_number.strip():
        return f"+{raw_number.strip()}"
    return raw_number.strip()


def _twiml_text(text: str) -> str:
    return html.escape(text or "", quote=False)


@router.post("/forward-call")
async def forward_call(request: Request):
    """
    TwiML endpoint Twilio calls when redirecting a call to a team member.

    Flow:
      attempt=1 → "Please hold on for a moment while I connect you..." + Ad → Dial(15s)
      attempt=2 → "I am sorry, they haven't picked up yet. I am still trying to connect, please stay on the line." → Dial(15s)
      attempt≥3 → "I am sorry, they are not available right now. I'll make sure they get your message. Is there anything else I can help you with today?"

    Ads always play at least once (attempt 1) before any failure message.
    """
    to_number = _forward_number_from_query(request.query_params.get("to", ""))
    attempt   = int(request.query_params.get("attempt", "1"))
    host      = os.getenv("PUBLIC_HOST", request.headers.get("host", request.base_url.hostname))

    logger.info(f"📲 [Forward] to={to_number} attempt={attempt}")

    if not to_number:
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">Sorry, we could not connect your call at this time. Please try again later.</Say>
</Response>"""
        return Response(content=twiml, media_type="text/xml")

    # Build the fallback URL that carries to/attempt forward
    fallback_url = _public_twilio_url(
        host,
        "/api/twilio/forward-fallback",
        {"to": to_number, "attempt": attempt},
    )
    fallback_url_xml = html.escape(fallback_url, quote=True)
    to_number_xml = html.escape(to_number, quote=False)

    if attempt == 1:
        # First attempt: play intro + ad, then dial
        ad_message = random.choice(ADS)
        logger.info(f"📣 [Forward] Ad selected: {ad_message[:50]}...")
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">Please hold on for a moment while I connect you.</Say>
    <Say voice="Polly.Joanna">{_twiml_text(ad_message)}</Say>
    <Say voice="Polly.Joanna">I'm still trying to connect you, please wait.</Say>
    <Dial callerId="{TWILIO_NUMBER}" timeout="15" action="{fallback_url_xml}">
        <Number>{to_number_xml}</Number>
    </Dial>
</Response>"""

    elif attempt == 2:
        # Second attempt: brief hold message, then dial again
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">They haven't picked up yet. I'm still trying — please stay on the line.</Say>
    <Dial callerId="{TWILIO_NUMBER}" timeout="15" action="{fallback_url_xml}">
        <Number>{to_number_xml}</Number>
    </Dial>
</Response>"""

    else:
        # All attempts exhausted — person is unavailable
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Say voice="Polly.Joanna">I am sorry, they are not available right now. I'll make sure they get your message. Is there anything else I can help you with today?</Say>
</Response>"""

    return Response(content=twiml, media_type="text/xml")


@router.post("/forward-fallback")
async def forward_fallback(request: Request):
    """
    Called by Twilio when a Dial attempt ends (no-answer, busy, failed).
    Increments attempt counter and redirects back to /forward-call.
    """
    form_data   = await request.form()
    dial_status = form_data.get("DialCallStatus", "no-answer")
    to_number   = _forward_number_from_query(request.query_params.get("to", ""))
    attempt     = int(request.query_params.get("attempt", "1"))
    host        = os.getenv("PUBLIC_HOST", request.headers.get("host", request.base_url.hostname))

    logger.info(f"📵 [Fallback] DialCallStatus={dial_status} to={to_number} attempt={attempt}")

    if dial_status == "completed":
        # Call was answered and finished normally — just hang up cleanly
        twiml = """<?xml version="1.0" encoding="UTF-8"?>
<Response></Response>"""
    else:
        # Not answered — try again or give up
        next_attempt  = attempt + 1
        next_url = _public_twilio_url(
            host,
            "/api/twilio/forward-call",
            {"to": to_number, "attempt": next_attempt},
        )
        next_url_xml = html.escape(next_url, quote=False)
        twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Redirect method="POST">{next_url_xml}</Redirect>
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
    email = ""
    business_name = ""
    client_notes = ""
    try:
        known_client = find_known_client_by_phone(caller_number)
        profile = profile_from_known_client(known_client) if known_client else await get_contact_profile_by_phone(caller_number)
        if profile.get("found"):
            contact_name = profile.get("name", "Client")
            client_type = profile.get("client_type", "Prospect")
            group = profile.get("group") or ""
            contact_id = profile.get("contact_id") or ""
            invoice_due = "true" if profile.get("invoice_due") else "false"
            email = profile.get("email") or ""
            business_name = profile.get("business_name") or ""
            client_notes = profile.get("notes") or ""
            source = profile.get("source") or "ghl"
            logger.info(f"📌 [Client:{source}] {caller_number} -> {contact_name} | {client_type} | Group:{group} | Invoice:{invoice_due}")
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
        "email": email,
        "business_name": business_name,
        "client_notes": client_notes,
        "public_host": host,
    })
    stream_url = f"{ws_protocol}://{host}/api/twilio/stream?{params}"
    stream_url_xml = html.escape(stream_url, quote=True)
    logger.info(f"📞 [Incoming] {caller_number} ({contact_name}) -> {stream_url}")

    twiml_response = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Connect>
        <Stream url="{stream_url_xml}" />
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
    email         = websocket.query_params.get("email", "")
    business_name = websocket.query_params.get("business_name", "")
    client_notes = websocket.query_params.get("client_notes", "")
    logger.info(f"\n🎙️ [WebSocket] {caller_number} | {contact_name} | {client_type} | Group:{group} | Invoice:{invoice_due}")
    # Metadata for call logging
    stream_sid = None
    call_sid = None
    transcript_accumulator = []
    openai_ws = None
    start_time_dt = datetime.datetime.utcnow()
    call_done = asyncio.Event()  # signals all tasks to stop cleanly

    # Silence watchdog state
    last_ai_response_done_at: list = [None]
    caller_spoke_after_ai: list = [False]
    watchdog_active: list = [False]
    end_call_permission_pending: list = [False]

    # Connect to OpenAI Realtime API if the API key is present
    if OPENAI_API_KEY:
        try:
            logger.info("🤖 [OpenAI] Attempting to connect to OpenAI Realtime API...")
            headers = {
                "Authorization": f"Bearer {OPENAI_API_KEY}",
            }
            openai_ws = await websockets.connect(OPENAI_WS_URL, additional_headers=headers)
            logger.info("🟢 [OpenAI] Successfully connected to OpenAI Realtime API.")

            from services.prompts import system_prompt
            instructions, selected_greeting = system_prompt()

            # Send session configuration to OpenAI
            session_update = {
                "type": "session.update",
                "session": {
                    "type": "realtime",
                    "model": OPENAI_REALTIME_MODEL,
                    "output_modalities": ["audio"],
                    "instructions": instructions + """

                    # ADDITIONAL SESSION RULES
                    - You are BILINGUAL: English and Bangla ONLY.
                    - If the caller speaks Bangla, respond in Dhaka Bangla.
                    - If the caller speaks English, respond in English.
                    - IGNORE any Spanish, Chinese, or Portuguese hallucinations from the transcription.
                    - If you hear noise, static, or irrelevant foreign words, REMAIN SILENT.
                    - NEVER switch to any other language.
                    - If you are not sure if the caller is speaking to you, stay silent.
                    - If you asked permission to end the call, treat English/Bangla/phonetic confirmations like yes, ok, sure, ji, haan, hya, kato, cut, hang up, no more, হ্যাঁ, জি, ঠিক আছে, কাটো, কেটে দেন, or আর কিছু না as permission. Then say a warm goodbye and call `end_call`.
                    """,
                    
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcmu"},
                            "transcription": {
                                "model": "whisper-1"
                            },
                            # Auto-detect when the caller finishes speaking and trigger a response
                            "turn_detection": {
                                "type": "server_vad",
                                "threshold": 0.85,
                                "prefix_padding_ms": 300,
                                "silence_duration_ms": 600,
                            },
                        },
                        "output": {
                            "format": {"type": "audio/pcmu"},
                            "voice": "shimmer",
                        },
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
                            "description": "For prospects/demo callers, send the payment link email for the selected appointment. Call this ONLY after getting name, email, phone, requested slot, and explicit caller confirmation to receive the payment link. The appointment is booked after Stripe payment.",
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
                            "description": "Transfer the caller to a team member like Simon or Tanzina. Call this when the user is a VIP or requests a human.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "target": {"type": "string", "enum": ["simon", "tanzina", "alex"]},
                                    "reason": {"type": "string"}
                                },
                                "required": ["target", "reason"]
                            }
                        },
                        {
                            "type": "function",
                            "name": "end_call",
                            "description": "End the demo session. ONLY call this AFTER you have explicitly asked the user for permission to end the call (e.g. 'Can I end the call now?') AND they have said YES. Never use this just because they say goodbye.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "reason": {
                                        "type": "string",
                                        "description": "Reason: 'caller_goodbye', 'task_complete', 'no_response', 'caller_request'"
                                    }
                                },
                                "required": ["reason"]
                            }
                        },
                        {
                            "type": "function",
                            "name": "record_message",
                            "description": "Record a callback request or message for a team member in the CRM. Call this when the client wants Simon or another team member to call them back, or wants to leave a message.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "caller_name": {"type": "string", "description": "The name of the caller"},
                                    "caller_phone": {"type": "string", "description": "The callback phone number"},
                                    "message": {"type": "string", "description": "The message details or why they want a callback"},
                                    "call_reason": {"type": "string", "description": "The reason for the call (e.g. tax, notice, callback)"}
                                },
                                "required": ["caller_name", "caller_phone", "message"]
                            }
                        },
                        {
                            "type": "function",
                            "name": "send_link_sms",
                            "description": "Send a portal sign up, login, or direct document notice upload link via SMS text message to the caller. Call this when the user agrees to receive a link via text/SMS.",
                            "parameters": {
                                "type": "object",
                                "properties": {
                                    "link_type": {
                                        "type": "string",
                                        "enum": ["signup", "login", "upload"],
                                        "description": "The type of link to send: 'signup' for portal.payminimumtax.com/signup, 'login' for portal.payminimumtax.com/login, 'upload' for www.PayMinimumTax.com/upload"
                                    },
                                    "phone_number": {
                                        "type": "string",
                                        "description": "Optional destination phone number. Defaults to the caller's phone."
                                    }
                                },
                                "required": ["link_type"]
                            }
                        }
                    ],
                    "tool_choice": "auto"
                }
            }
            await openai_ws.send(json.dumps(session_update))
            logger.info("📝 [OpenAI] Sent session configuration update with turn detection.")

            initial_greeting = {
                "type": "response.create",
                "response": {
                    "output_modalities": ["audio"],
                    "instructions": f"Greet the caller by saying: \"{selected_greeting}\". Speak it naturally and warmly. IMPORTANT: Use ONLY English or Bangla. NEVER use any other language."
                }
            }
            await openai_ws.send(json.dumps(initial_greeting))
            logger.info("🗣️ [OpenAI] Sent initial greeting trigger.")
            watchdog_active[0] = True

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
                    call_done.set()   # Signal all tasks to stop
                    break

        except WebSocketDisconnect:
            logger.info("⚠️ [Twilio WebSocket] Disconnected from Twilio.")
            call_done.set()
        except Exception as e:
            logger.info(f"🔴 [Twilio WebSocket] Error reading from Twilio: {e}")
            call_done.set()

    async def send_to_twilio():
        nonlocal stream_sid
        if not openai_ws:
            await call_done.wait()
            return

        # ── Helper: hang up via Twilio REST + set call_done ──
        async def _hangup_call():
            if call_done.is_set():
                return   # already hanging up
            call_done.set()
            if call_sid:
                try:
                    end_url = f"https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Calls/{call_sid}.json"
                    auth = (TWILIO_SID, TWILIO_AUTH_TOKEN)
                    async with httpx.AsyncClient(timeout=5) as client:
                        resp = await client.post(end_url, data={"Status": "completed"}, auth=auth)
                    if resp.status_code == 200:
                        logger.info(f"✅ [Hangup] Twilio call {call_sid} terminated.")
                    else:
                        logger.error(f"🔴 [Hangup] Twilio error: {resp.status_code} {resp.text[:80]}")
                except Exception as e:
                    logger.error(f"🔴 [Hangup] Exception: {e}")

        openai_media_count = 0
        current_response_id = None
        last_assistant_item_id = None
        response_audio_sent_ms = 0
        interrupt_event = asyncio.Event()
        end_call_in_progress = [False]

        async def _hangup_after_consent():
            if end_call_in_progress[0] or call_done.is_set():
                return
            end_call_in_progress[0] = True
            end_call_permission_pending[0] = False
            try:
                await openai_ws.send(json.dumps({"type": "response.cancel"}))
            except Exception:
                pass
            try:
                # Detect language from transcript accumulator
                is_bangla_convo = False
                for entry in reversed(transcript_accumulator):
                    if any('\u0980' <= char <= '\u09FF' for char in entry):
                        is_bangla_convo = True
                        break
                
                if is_bangla_convo:
                    goodbye_instr = "In BANGLA (Dhaka style), say a short warm goodbye like: 'ধন্যবাদ, ভালো থাকবেন। খোদা হাফেজ।' Then stop speaking."
                else:
                    goodbye_instr = "In ENGLISH, say a short warm goodbye like: 'Thank you, goodbye. Have a nice day.' Then stop speaking."

                await openai_ws.send(json.dumps({
                    "type": "response.create",
                    "response": {
                        "output_modalities": ["audio"],
                        "instructions": goodbye_instr
                    }
                }))
                logger.info(f"✅ [EndCall] Caller gave permission. Saying goodbye ({'Bangla' if is_bangla_convo else 'English'}), then hanging up.")
                await asyncio.sleep(4)
            finally:
                await _hangup_call()

        try:
            async for openai_message in openai_ws:
                if call_done.is_set():
                    break
                openai_data = json.loads(openai_message)
                event_type = openai_data.get("type", "")

                # Track when a new response starts generating
                if event_type == "response.created":
                    resp_obj = openai_data.get("response", {})
                    current_response_id = resp_obj.get("id")
                    interrupt_event.clear()  # Allow audio for this new response
                    response_audio_sent_ms = 0  # Reset audio counter
                    logger.info(f"🟢 [OpenAI] New response started: {current_response_id}")

                # Process assistant's generated audio response
                elif event_type in ("response.audio.delta", "response.output_audio.delta"):
                    # DROP all audio chunks if caller interrupted
                    if interrupt_event.is_set():
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
                    # Set event atomically — any concurrent audio.delta checks see this instantly
                    interrupt_event.set()

                    # 1) Fire-and-forget: clear Twilio buffer without blocking the event loop
                    async def _clear_twilio_buffer(sid):
                        try:
                            await websocket.send_text(json.dumps({
                                "event": "clear",
                                "streamSid": sid
                            }))
                            logger.info("🧹 [Twilio] Cleared audio playback buffer.")
                        except Exception as e:
                            logger.error(f"🔴 [Twilio] Failed to clear buffer: {e}")

                    if stream_sid:
                        asyncio.create_task(_clear_twilio_buffer(stream_sid))

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

                elif event_type == "input_audio_buffer.speech_stopped":
                    logger.info("🔇 [OpenAI] Speech ended, waiting for transcript check...")

                # Response finished or cancelled — clear interrupt so next response plays normally
                elif event_type in ("response.done", "response.cancelled"):
                    resp_obj = openai_data.get("response", {})
                    done_id = resp_obj.get("id") or current_response_id
                    interrupt_event.clear()  # Ready for next response
                    
                    # Start silence timer: AI finished generating, but audio takes time to play.
                    # We add the audio duration to the current time so the watchdog only starts counting
                    # AFTER the caller actually finishes hearing the audio.
                    audio_duration_sec = response_audio_sent_ms / 1000.0
                    last_ai_response_done_at[0] = asyncio.get_event_loop().time() + audio_duration_sec
                    caller_spoke_after_ai[0] = False
                    logger.info(f"✅ [OpenAI] Response {done_id} finished ({event_type}). Audio Duration: {audio_duration_sec:.2f}s")
                
                # Additional debug logging for other important OpenAI events
                elif event_type in (
                    "response.text.done",
                    "response.output_text.done",
                    "response.audio_transcript.done",
                    "response.output_audio_transcript.done",
                ):
                    text = openai_data.get("text") or openai_data.get("transcript")
                    if text:
                        logger.info(f"\n🤖 [AI Reply]: {text}")
                        transcript_accumulator.append(f"AI: {text}")
                        if _asks_end_call_permission(text):
                            end_call_permission_pending[0] = True
                        
                
                # Catch the user's speech transcript + detect caller goodbye
                elif event_type == "conversation.item.input_audio_transcription.completed":
                    user_text = openai_data.get("transcript")
                    if user_text:
                        logger.info(f"\n👤 [Caller]: {user_text}")
                        transcript_accumulator.append(f"Caller: {user_text}")
                        is_consent = end_call_permission_pending[0] and _is_end_call_consent(user_text)
                        is_explicit = any(cue in user_text.lower() for cue in ["kete dao", "kete den", "kete din", "cut kore den", "kat kore den", "কেটে দাও", "কেটে দেন", "কেটে দিন", "কল কেটে", "কলটা কেটে", "cut the call", "hang up", "allah hafez", "khoda hafez", "রাখলাম", "রাখছি", "rakhlam", "rakhchi", "bye bye", "allah hafiz"])
                        if is_consent or is_explicit:
                            asyncio.create_task(_hangup_after_consent())
                        

                # Handle tool calls
                elif event_type == "response.function_call_arguments.done":
                    func_name = openai_data.get("name")
                    call_id = openai_data.get("call_id")
                    args = json.loads(openai_data.get("arguments", "{}"))
                    logger.info(f"\n🛠️ [OpenAI] AI called tool '{func_name}' with args: {args}")
                    
                    async def _log_adapter(event, message):
                        logger.info(f"[{event}] {message}")

                    if func_name == "transfer_call":
                        result = await handle_transfer_call(args, openai_ws, call_done, call_id, _log_adapter)
                        await openai_ws.send(json.dumps({
                            "type": "conversation.item.create",
                            "item": {
                                "type": "function_call_output",
                                "call_id": call_id,
                                "output": json.dumps(result)
                            }
                        }))
                        continue

                    elif func_name == "book_appointment":
                        result = await handle_book_appointment(args, _log_adapter)
                    
                    elif func_name == "get_available_slots":
                        result = await handle_get_slots(args, _log_adapter)
                    
                    elif func_name == "end_call":
                        await handle_end_call(
                            args,
                            openai_ws,
                            call_done,
                            end_call_in_progress,
                            transcript_accumulator,
                            call_id,
                            _log_adapter,
                            _hangup_call
                        )
                        continue

                    elif func_name == "record_message":
                        result = await handle_record_message(
                            args,
                            contact_id,
                            contact_name,
                            caller_number,
                            _log_adapter
                        )

                    elif func_name == "send_link_sms":
                        result = await handle_send_link_sms(
                            args,
                            caller_number,
                            _log_adapter
                        )

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

    # Silence watchdog for Twilio calls
    async def silence_watchdog():
        """After 12s of caller silence post-AI response, inject a gentle nudge."""
        SILENCE_TIMEOUT = 12
        while not call_done.is_set():
            await asyncio.sleep(1)
            if not watchdog_active[0] or not openai_ws:
                continue
            t = last_ai_response_done_at[0]
            if t is None:
                continue
            elapsed = asyncio.get_event_loop().time() - t
            if elapsed >= SILENCE_TIMEOUT and not caller_spoke_after_ai[0]:
                try:
                    await openai_ws.send(json.dumps({
                        "type": "response.create",
                        "response": {
                            "output_modalities": ["audio"],
                            "instructions": "The caller has been silent for a while. Politely ask if they are still there (e.g. 'Are you still with me?'). IMPORTANT: Ask in the EXACT same language (English or Bangla) that the conversation is currently in. Keep it to one short natural sentence."
                        }
                    }))
                    logger.info("⏱️ [Watchdog] Dynamic silence nudge sent.")
                except Exception:
                    pass
                last_ai_response_done_at[0] = asyncio.get_event_loop().time()

    # Orchestrate bidirectional async tasks
    try:
        await asyncio.gather(
            receive_from_twilio(),
            send_to_twilio(),
            silence_watchdog(),
            return_exceptions=True
        )
    finally:
        call_done.set()   # make sure all tasks are unblocked in edge cases
        if openai_ws:
            try:
                await openai_ws.close()
                logger.info("🔒 [OpenAI] WebSocket closed cleanly.")
            except Exception:
                pass
        logger.info("🔒 [Twilio] Bidirectional voice session closed.")
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
            except Exception as e:
                logger.error(f"🔴 [Analysis] Error in post-call analysis: {e}")
                summary = "Error analyzing call."
                reason = "Unknown"
                intent = "Unknown"
                outcome = "Unknown"
                lead_status = "Unknown"
                tags = ""
        else:
            full_transcript = ""
            summary = "Call was too short or no transcript available."
            reason = "Short Call"
            intent = "Unknown"
            outcome = "Missed/Dropped"
            lead_status = "Unknown"
            tags = ""

        try:
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
