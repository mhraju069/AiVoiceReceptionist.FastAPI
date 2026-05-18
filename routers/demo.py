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
from services.prompts import system_prompt
from routers.twilio import ADS
router = APIRouter(
    prefix="/api/demo",
    tags=["Demo & Debug"]
)

# Global list of connected debug WebSockets
debug_clients: List[WebSocket] = []

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_WS_URL = os.getenv(
    "OPENAI_WS_URL",
    "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"
)


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
    print("\n🔌 [Demo] Browser WebSocket connected.")

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
                    "OpenAI-Beta": "realtime=v1"
                }
                openai_ws = await websockets.connect(OPENAI_WS_URL, additional_headers=headers)
                await _debug("openai_connected", "🟢 OpenAI Realtime API connected!")

                instructions, selected_greeting = system_prompt()
                
                session_update = {
                    "type": "session.update",
                    "session": {
                        "modalities": ["text", "audio"],
                        "instructions": instructions + """
                        
                        # ADDITIONAL SESSION RULES
                        - You are BILINGUAL: English and Bangla ONLY.
                        - If the caller speaks Bangla, respond in Dhaka Bangla.
                        - If the caller speaks English, respond in English.
                        - IGNORE any Spanish, Chinese, or Portuguese hallucinations from the transcription.
                        - If you hear noise, static, or irrelevant foreign words, REMAIN SILENT.
                        - NEVER switch to any other language.
                        - If you are not sure if the caller is speaking to you, stay silent.
                        """,
                        # + """

                        # # CALLER CRM PROFILE (Simulated for Demo)
                        # Caller Name: Test Simon (Demo User)
                        # Client Type: Class A Client
                        # Group: A
                        # Invoice Due: No
                        # Phone: +1234567890

                        # Note: Since this is a Demo session, assume the user is this Class A Client. Greet them by name and handle as VIP.
                        # """
                        "voice": "shimmer",
                        "input_audio_format": "pcm16",
                        "output_audio_format": "pcm16",
                        "input_audio_transcription": {"model": "whisper-1"},
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.85,
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
                        "modalities": ["text", "audio"],
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
            try:
                async for openai_message in openai_ws:
                    if call_done.is_set():
                        break
                    openai_data = json.loads(openai_message)
                    evt = openai_data.get("type", "")

                    if evt == "response.created":
                        interrupt_event.clear()  # Allow audio for this new response
                        response_audio_sent_ms = 0

                    elif evt == "response.audio.delta":
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

                    elif evt in ["response.text.done", "response.audio_transcript.done"]:
                        text = openai_data.get("text") or openai_data.get("transcript")
                        if text:
                            ai_transcripts.append(text)
                            await _debug("ai_transcript", f"🤖 AI: {text}")
                            await _send({"type": "transcript", "role": "assistant", "text": text})
                            

                    elif evt == "conversation.item.input_audio_transcription.completed":
                        user_text = openai_data.get("transcript")
                        if user_text:
                            user_transcripts.append(user_text)
                            await _debug("user_transcript", f"👤 User: {user_text}")
                            await _send({"type": "transcript", "role": "user", "text": user_text})
                            

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
                        
                        if func_name == "transfer_call":
                            target = args.get("target", "tanzina").lower()
                            await _debug("transfer_call", f"📲 Transfer started to {target}. Simulating hold flow...")
                            
                            ad_msg = random.choice(ADS)
                            
                            # Intro + Ad
                            await openai_ws.send(json.dumps({
                                "type": "response.create",
                                "response": {
                                    "modalities": ["text", "audio"],
                                    "instructions": f"Translate and say this in the SAME LANGUAGE the user is currently speaking (English or Bangla): 'Please hold on for a moment while I connect you to {target}.' Then, switch to ENGLISH and say this advertisement naturally: '{ad_msg}'. Then switch back to the user's language and say: 'I am still trying to connect you, please wait.'"
                                }
                            }))

                            async def _simulated_transfer_flow():
                                await asyncio.sleep(12)
                                if call_done.is_set(): return
                                await _debug("transfer_hold", "⏳ Still trying to connect (12s mark)...")
                                await openai_ws.send(json.dumps({
                                    "type": "response.create",
                                    "response": {
                                        "modalities": ["text", "audio"],
                                        "instructions": "In the SAME LANGUAGE the user is speaking, say: 'I am sorry, they haven\'t picked up yet. I am still trying to connect, please stay on the line.'"
                                    }
                                }))
                                
                                await asyncio.sleep(12)
                                if call_done.is_set(): return
                                await _debug("transfer_fail", "❌ Transfer failed (target unavailable).")
                                await openai_ws.send(json.dumps({
                                    "type": "response.create",
                                    "response": {
                                        "modalities": ["text", "audio"],
                                        "instructions": f"In the SAME LANGUAGE the user is speaking, say: 'I am sorry, {target} is not available right now. I\'ll make sure they get your message. Is there anything else I can help you with today?'"
                                    }
                                }))
                            
                            asyncio.create_task(_simulated_transfer_flow())
                            
                            # Send tool output so OpenAI knows it was accepted
                            await openai_ws.send(json.dumps({
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "function_call_output",
                                    "call_id": call_id,
                                    "output": json.dumps({"status": "success", "message": f"Transferring to {target}"})
                                }
                            }))
                            continue

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
                                await _debug("tool_result", f"✅ Booking result: {result.get('status')}")
                            except Exception as e:
                                result = {"status": "error", "message": "Sorry, there was a technical issue booking your appointment. Please try again later."}
                                await _debug("tool_error", f"🔴 Booking exception: {e}")
                        
                        elif func_name == "get_available_slots":
                            from services.booking_service import get_slots
                            try:
                                result = await get_slots(
                                    calendar_type=args.get("calendar_type", "follow_up_b")
                                )
                                await _debug("tool_result", f"✅ Slots fetched successfully.")
                            except Exception as e:
                                result = {"status": "error", "message": "Could not fetch available slots."}
                                await _debug("tool_error", f"🔴 Slot fetch exception: {e}")
                        
                        if func_name == "end_call":
                            reason = args.get("reason", "task_complete")
                            await _debug("end_call_tool", f"👋 end_call tool called: {reason}")
                            # Send result back but do NOT trigger another response
                            await openai_ws.send(json.dumps({
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "function_call_output",
                                    "call_id": call_id,
                                    "output": json.dumps({"status": "success", "message": "Call ended."})
                                }
                            }))
                            await _end_demo_call()
                            continue  # skip response.create

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
                                "modalities": ["text", "audio"],
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
