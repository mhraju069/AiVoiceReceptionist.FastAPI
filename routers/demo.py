"""
Browser Demo Router.

Provides a browser-compatible WebSocket endpoint for testing the AI voice
receptionist without needing Twilio. Sends real-time debug events to the
frontend so every step of the flow is visible.
"""
import os
import json
import base64
import random
import asyncio
import time
import websockets
from datetime import datetime
from typing import List
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse
from services.openai_realtime import get_openai_realtime_model, get_openai_realtime_ws_url
from services.prompts import system_prompt
from routers.twilio import ADS, _asks_end_call_permission, _is_end_call_consent
from routers.common_tools import (
    handle_book_appointment,
    handle_get_slots,
    handle_transfer_call,
    handle_end_call,
    handle_send_link_sms,
    handle_record_message,
)
router = APIRouter(
    prefix="/api/demo",
    tags=["Demo & Debug"]
)

# Global list of connected debug WebSockets
debug_clients: List[WebSocket] = []

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_REALTIME_MODEL = get_openai_realtime_model()
OPENAI_WS_URL = get_openai_realtime_ws_url()


async def broadcast_debug(event: str, message: str, data: dict = None):
    """Send a debug event to all connected debug WebSocket clients."""
    payload = {
        "type": "debug",
        "event": event,
        "message": message,
        "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        "data": data or {}
    }
    disconnected = []
    for client in debug_clients:
        try:
            await client.send_json(payload)
        except Exception:
            disconnected.append(client)
    for c in disconnected:
        debug_clients.remove(c)


@router.websocket("/debug-ws")
async def debug_websocket(websocket: WebSocket):
    """WebSocket endpoint for receiving real-time debug events."""
    await websocket.accept()
    debug_clients.append(websocket)
    try:
        while True:
            # Keep alive - client can send pings
            await websocket.receive_text()
    except WebSocketDisconnect:
        if websocket in debug_clients:
            debug_clients.remove(websocket)


@router.websocket("/voice-stream")
async def demo_voice_stream(websocket: WebSocket):
    """
    Browser-compatible WebSocket for voice streaming.
    """
    await websocket.accept()
    phone = websocket.query_params.get("phone", "+1234567890")
    print(f"\n🔌 [Demo] Browser WebSocket connected. Phone: {phone}")

    # Use an event to signal when the call ends (keeps both tasks alive)
    call_done = asyncio.Event()
    openai_ws = None
    call_start_time = None
    user_transcripts = []
    ai_transcripts = []

    # Silence watchdog state
    last_ai_response_done_at: list = [None]   # use list so inner funcs can mutate
    caller_spoke_after_ai: list = [False]       # reset when AI responds, set when caller speaks
    watchdog_active: list = [False]             # becomes True after first greeting is done
    end_call_permission_pending: list = [False]

    async def _send(msg: dict):
        """Helper to safely send JSON to browser."""
        try:
            await websocket.send_json(msg)
        except Exception:
            pass

    async def _debug(event, message):
        """Send debug to browser + print to Docker logs."""
        print(f"  [{event}] {message}")
        await _send({"type": "debug", "event": event, "message": message,
                      "timestamp": datetime.now().strftime("%H:%M:%S")})

    try:
        await _debug("connected", "🔌 Connected to AI Voice Server")

        # ── Try connecting to OpenAI ──
        if OPENAI_API_KEY:
            try:
                await _debug("openai_connecting", "🤖 Connecting to OpenAI Realtime API...")

                headers = {
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                }
                openai_ws = await websockets.connect(OPENAI_WS_URL, additional_headers=headers)
                await _debug("openai_connected", "🟢 OpenAI Realtime API connected!")

                # GHL profile lookup for Demo
                from services.ghl import get_contact_profile_by_phone
                from services.known_clients import find_known_client_by_phone, profile_from_known_client
                
                contact_name = "Prospect"
                client_type = "Prospect"
                group = ""
                contact_id = ""
                invoice_due = "false"
                email = ""
                business_name = ""
                client_notes = ""
                try:
                    known_client = find_known_client_by_phone(phone)
                    profile = profile_from_known_client(known_client) if known_client else await get_contact_profile_by_phone(phone)
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
                        print(f"📌 [Demo Client:{source}] {phone} -> {contact_name} | {client_type}")
                except Exception as e:
                    print(f"Error fetching contact profile in demo: {e}")

                instructions, selected_greeting = system_prompt()
                
                # Greet known client by notes if available, else by name
                greeting_name = client_notes.strip() if (client_notes and client_notes.strip()) else contact_name
                if greeting_name and greeting_name != "Prospect":
                    if "Dhonnobad, Thank you for calling Pay Minimum Tax" in selected_greeting:
                        selected_greeting = selected_greeting.replace("How can I help you?", f"Hello, {greeting_name}! How can I help you today?")
                        selected_greeting = selected_greeting.replace("What could I do for you?", f"Hello, {greeting_name}! What can I do for you today?")
                        selected_greeting = selected_greeting.replace("Who do I have the pleasure to speak with today?", f"Hello, {greeting_name}! How can I help you today?")
                    else:
                        selected_greeting = f"Dhonnobad, Thank you for calling Pay Minimum Tax, I am রেবা. Hello, {greeting_name}! How can I help you today?"

                session_update = {
                    "type": "session.update",
                    "session": {
                        "type": "realtime",
                        "model": OPENAI_REALTIME_MODEL,
                        "output_modalities": ["audio"],
                        "instructions": instructions + f"""
                        
                        # ADDITIONAL SESSION RULES
                        - You are BILINGUAL: English and Bangla ONLY.
                        - If the caller speaks Bangla, respond in Dhaka Bangla.
                        - If the caller speaks English, respond in English.
                        - IGNORE any Spanish, Chinese, or Portuguese hallucinations from the transcription.
                        - If you hear noise, static, or irrelevant foreign words, REMAIN SILENT.
                        - NEVER switch to any other language.
                        - If you are not sure if the caller is speaking to you, stay silent.
                        - If you asked permission to end the call, treat English/Bangla/phonetic confirmations like yes, ok, sure, ji, haan, hya, kato, cut, hang up, no more, হ্যাঁ, জি, ঠিক আছে, কাটো, কেটে দেন, or আর কিছু না as permission. Then say a warm goodbye and call `end_call`.
                        
                        # CALLER CRM PROFILE
                        - Name: {contact_name}
                        - Phone: {phone} (Note: Always confirm this number instead of asking for it. All clients are US-based with +1 prefix.)
                        - Client Type: {client_type}
                        - Group: {group}
                        - Contact ID: {contact_id}
                        - Email: {email if email else 'Not Provided'}
                        - Business Name: {business_name if business_name else 'Not Provided'}
                        - Client Notes from CRM: {client_notes if client_notes else 'None'}
                        - Has Invoice Due: {invoice_due}
                        """,
                        "audio": {
                            "input": {
                                "format": {"type": "audio/pcm", "rate": 24000},
                                "transcription": {"model": "whisper-1"},
                                "turn_detection": {
                                    "type": "server_vad",
                                    "threshold": 0.85,
                                    "prefix_padding_ms": 300,
                                    "silence_duration_ms": 600
                                },
                            },
                            "output": {
                                "format": {"type": "audio/pcm", "rate": 24000},
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
                await _debug("session_configured", "📝 Session configured (PCM16, 24kHz, VAD)")

                initial_greeting = {
                    "type": "response.create",
                    "response": {
                        "output_modalities": ["audio"],
                        "instructions": f"Greet the caller by saying: \"{selected_greeting}\". Speak it naturally and warmly. IMPORTANT: Use ONLY English or Bangla. NEVER use any other language."
                    }
                }
                await openai_ws.send(json.dumps(initial_greeting))
                await _debug("greeting_sent", "🗣️ AI is preparing a greeting...")
                # Watchdog starts counting after greeting is sent
                watchdog_active[0] = True

            except Exception as e:
                print(f"🔴 [Demo] OpenAI error: {e}")
                await _debug("openai_error", f"🔴 OpenAI error: {str(e)[:120]}. Using mock mode.")
                openai_ws = None
        else:
            await _debug("mock_mode", "⚠️ No OPENAI_API_KEY set. Running in Mock Mode — voice won't respond but debug works.")

        # ── Task 1: Receive audio from browser ──
        async def receive_from_browser():
            nonlocal call_start_time
            chunk_count = 0
            try:
                while not call_done.is_set():
                    raw = await websocket.receive_text()
                    msg = json.loads(raw)

                    if msg.get("type") == "start":
                        call_start_time = time.time()
                        await _debug("call_started", "📞 Call started! Listening...")

                    elif msg.get("type") == "audio":
                        chunk_count += 1
                        audio_data = msg.get("data", "")
                        if chunk_count % 50 == 0:
                            await _debug("audio_chunks", f"🔊 Received {chunk_count} audio chunks from mic")
                        if openai_ws:
                            await openai_ws.send(json.dumps({
                                "type": "input_audio_buffer.append",
                                "audio": audio_data
                            }))

                    elif msg.get("type") == "stop":
                        duration = round(time.time() - call_start_time, 1) if call_start_time else 0
                        await _debug("call_ended", f"🛑 Call ended. Duration: {duration}s, Chunks: {chunk_count}")
                        summary = _build_call_summary(user_transcripts, ai_transcripts, duration)
                        await _send({"type": "call_summary", "data": summary})
                        call_done.set()
                        break

                    elif msg.get("type") == "ping":
                        await _send({"type": "pong"})

            except WebSocketDisconnect:
                print("⚠️ [Demo] Browser disconnected.")
                call_done.set()
            except Exception as e:
                print(f"🔴 [Demo] receive error: {e}")
                call_done.set()

        # ── Task 2: Forward OpenAI responses to browser ──
        async def send_to_browser():
            if not openai_ws:
                # In mock mode, just wait until the call is done
                await call_done.wait()
                return

            # ── Helper: cleanly end the demo session ──
            async def _end_demo_call():
                if call_done.is_set():
                    return
                call_done.set()
                try:
                    await _send({"type": "call_ended", "reason": "goodbye"})
                    await _debug("call_end", "📞 Call ended by AI goodbye detection.")
                except Exception:
                    pass

            ai_chunk_count = 0
            # asyncio.Event for atomic cross-coroutine interrupt signaling
            interrupt_event = asyncio.Event()
            last_assistant_item_id = None
            response_audio_sent_ms = 0
            end_call_in_progress = [False]

            async def _end_demo_after_consent():
                if end_call_in_progress[0] or call_done.is_set():
                    return
                end_call_in_progress[0] = True
                end_call_permission_pending[0] = False
                try:
                    await openai_ws.send(json.dumps({"type": "response.cancel"}))
                except Exception:
                    pass
                    # Detect language from user transcript accumulator only
                    is_bangla_convo = False
                    for entry in reversed(user_transcripts):
                        if any('\u0980' <= char <= '\u09FF' for char in entry):
                            is_bangla_convo = True
                            break
                    
                    if is_bangla_convo:
                        goodbye_instr = "OVERRIDE SYSTEM INSTRUCTIONS: The user has already agreed to end the call. In BANGLA (Dhaka style), say a short warm goodbye like: 'ধন্যবাদ, ভালো থাকবেন। খোদা হাফেজ।' Then stop speaking. Do NOT ask for permission again, and do NOT ask any questions."
                    else:
                        goodbye_instr = "OVERRIDE SYSTEM INSTRUCTIONS: The user has already agreed to end the call. In ENGLISH, say a short warm goodbye like: 'Thank you, goodbye. Have a nice day.' Then stop speaking. Do NOT ask for permission again, and do NOT ask any questions."

                    await openai_ws.send(json.dumps({
                        "type": "response.create",
                        "response": {
                            "output_modalities": ["audio"],
                            "instructions": goodbye_instr
                        }
                    }))
                    await _debug("end_call_consent", f"✅ Caller gave end-call permission. Saying goodbye ({'Bangla' if is_bangla_convo else 'English'}), then ending.")
                    await asyncio.sleep(4)
                finally:
                    await _end_demo_call()

            try:
                async for openai_message in openai_ws:
                    if call_done.is_set():
                        break
                    openai_data = json.loads(openai_message)
                    evt = openai_data.get("type", "")

                    if evt == "response.created":
                        interrupt_event.clear()  # Allow audio for this new response
                        response_audio_sent_ms = 0

                    elif evt in ("response.audio.delta", "response.output_audio.delta"):
                        if interrupt_event.is_set():
                            continue
                        item_id = openai_data.get("item_id")
                        if item_id:
                            last_assistant_item_id = item_id
                        ai_chunk_count += 1
                        # Track audio duration (PCM16 24kHz: 2 bytes/sample, 24000 samples/s = 48 bytes/ms)
                        try:
                            raw_bytes = len(base64.b64decode(openai_data["delta"]))
                            response_audio_sent_ms += raw_bytes / 48
                        except Exception:
                            response_audio_sent_ms += 20
                        await _send({"type": "audio", "data": openai_data["delta"]})
                        if ai_chunk_count % 50 == 0:
                            await _debug("ai_audio", f"🎙️ Streamed {ai_chunk_count} AI audio chunks")

                    elif evt in (
                        "response.text.done",
                        "response.output_text.done",
                        "response.audio_transcript.done",
                        "response.output_audio_transcript.done",
                    ):
                        text = openai_data.get("text") or openai_data.get("transcript")
                        if text:
                            ai_transcripts.append(text)
                            if _asks_end_call_permission(text):
                                end_call_permission_pending[0] = True
                            await _debug("ai_transcript", f"🤖 AI: {text}")
                            await _send({"type": "transcript", "role": "assistant", "text": text})
                            

                    elif evt == "conversation.item.input_audio_transcription.completed":
                        user_text = openai_data.get("transcript")
                        if user_text:
                            user_transcripts.append(user_text)
                            await _debug("user_transcript", f"👤 User: {user_text}")
                            await _send({"type": "transcript", "role": "user", "text": user_text})
                            is_consent = end_call_permission_pending[0] and _is_end_call_consent(user_text)
                            is_explicit = any(cue in user_text.lower() for cue in ["kete dao", "kete den", "kete din", "cut kore den", "kat kore den", "কেটে দাও", "কেটে দেন", "কেটে দিন", "কল কেটে", "কলটা কেটে", "cut the call", "hang up", "allah hafez", "khoda hafez", "রাখলাম", "রাখছি", "rakhlam", "rakhchi", "bye bye", "allah hafiz"])
                            if is_consent or is_explicit:
                                asyncio.create_task(_end_demo_after_consent())
                            

                    elif evt == "input_audio_buffer.speech_started":
                        await _debug("vad_speech_started", "🎤 Speech detected — interrupting AI...")
                        # Set atomically — any concurrent audio.delta checks see this instantly
                        interrupt_event.set()

                        # Fire-and-forget: tell browser to stop playing queued audio
                        async def _notify_interrupt():
                            try:
                                await _send({"type": "interrupt"})
                            except Exception:
                                pass
                        asyncio.create_task(_notify_interrupt())

                        # Cancel OpenAI response
                        try:
                            await openai_ws.send(json.dumps({"type": "response.cancel"}))
                        except Exception:
                            pass
                        # Truncate conversation item
                        if last_assistant_item_id:
                            try:
                                await openai_ws.send(json.dumps({
                                    "type": "conversation.item.truncate",
                                    "item_id": last_assistant_item_id,
                                    "content_index": 0,
                                    "audio_end_ms": int(response_audio_sent_ms)
                                }))
                            except Exception:
                                pass

                    elif evt == "input_audio_buffer.speech_stopped":
                        await _debug("vad_speech_stopped", "🔇 Speech ended, processing...")

                    elif evt == "input_audio_buffer.speech_started":
                        # Caller spoke — reset silence tracking
                        caller_spoke_after_ai[0] = True

                    # Response finished or cancelled — clear interrupt so next response plays normally
                    elif evt in ("response.done", "response.cancelled"):
                        interrupt_event.clear()
                        # Start silence timer: AI finished speaking, now waiting for caller
                        # We add the audio duration to the current time so the watchdog only starts counting
                        # AFTER the caller actually finishes hearing the audio.
                        audio_duration_sec = response_audio_sent_ms / 1000.0
                        last_ai_response_done_at[0] = asyncio.get_event_loop().time() + audio_duration_sec
                        caller_spoke_after_ai[0] = False
                        await _debug("response_done", f"✅ Response done. Audio duration: {audio_duration_sec:.2f}s")

                    elif openai_data.get("type") == "response.function_call_arguments.done":
                        func_name = openai_data.get("name")
                        call_id = openai_data.get("call_id")
                        args = json.loads(openai_data.get("arguments", "{}"))
                        await _debug("tool_call", f"🛠️ AI called {func_name} with args: {args}")
                        result = {}
                        
                        async def _log_adapter(event, message):
                            await _debug(event, message)

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
                                ai_transcripts + user_transcripts,
                                call_id,
                                _log_adapter,
                                _end_demo_call
                            )
                            continue

                        elif func_name == "record_message":
                            result = await handle_record_message(
                                args=args,
                                contact_id="",
                                default_name="Demo Caller",
                                default_phone=phone,
                                logger_or_debug=_log_adapter
                            )

                        elif func_name == "send_link_sms":
                            result = await handle_send_link_sms(
                                args=args,
                                default_phone=phone,
                                logger_or_debug=_log_adapter
                            )

                        # Send output back to OpenAI for ANY tool call
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
                print(f"🔴 [Demo] OpenAI stream error: {e}")
                await _debug("openai_stream_error", f"🔴 OpenAI stream error: {str(e)[:100]}")

        # ── Task 3: Silence watchdog ──
        async def silence_watchdog():
            """If the caller is silent for 12s after the AI finishes speaking, send a gentle nudge."""
            SILENCE_TIMEOUT = 12  # seconds to wait before nudging
            while not call_done.is_set():
                await asyncio.sleep(1)
                if not watchdog_active[0] or not openai_ws:
                    continue
                t = last_ai_response_done_at[0]
                if t is None:
                    continue
                elapsed = asyncio.get_event_loop().time() - t
                if elapsed >= SILENCE_TIMEOUT and not caller_spoke_after_ai[0]:
                    # Inject a gentle nudge via OpenAI
                    try:
                        await openai_ws.send(json.dumps({
                            "type": "response.create",
                            "response": {
                                "output_modalities": ["audio"],
                                "instructions": "The caller has been silent for a while. Politely ask if they are still there (e.g. 'Are you still with me?'). IMPORTANT: Ask in the EXACT same language (English or Bangla) that the conversation is currently in. Keep it to one short natural sentence."
                            }
                        }))
                        await _debug("silence_nudge", "⏱️ Silence timeout — sending dynamic nudge")
                    except Exception:
                        pass
                    # Reset timer so we don't spam — next nudge in another 15s
                    last_ai_response_done_at[0] = asyncio.get_event_loop().time()

        # ── Run all tasks concurrently ──
        await asyncio.gather(receive_from_browser(), send_to_browser(), silence_watchdog(), return_exceptions=True)

    except WebSocketDisconnect:
        print("⚠️ [Demo] WebSocket disconnected (outer).")
        call_done.set()
    except Exception as e:
        print(f"🔴 [Demo] Unhandled error in voice-stream: {e}")
        import traceback
        traceback.print_exc()
        call_done.set()
    finally:
        call_done.set()   # ensure all tasks are released in any scenario
        if openai_ws:
            try:
                await openai_ws.close()
            except Exception:
                pass
        print("🔒 [Demo] Voice session closed.")


def _build_call_summary(user_transcripts, ai_transcripts, duration):
    """Build a summary object from the call transcripts."""
    conversation = []
    max_len = max(len(user_transcripts), len(ai_transcripts))
    
    for i in range(max_len):
        if i < len(ai_transcripts):
            conversation.append(f"AI: {ai_transcripts[i]}")
        if i < len(user_transcripts):
            conversation.append(f"Caller: {user_transcripts[i]}")
    
    return {
        "duration": f"{duration}s",
        "user_messages": len(user_transcripts),
        "ai_messages": len(ai_transcripts),
        "conversation": "\n".join(conversation) if conversation else "No transcripts (mock mode or short call)",
        "summary": f"Call lasted {duration}s with {len(user_transcripts)} caller messages and {len(ai_transcripts)} AI responses."
    }
