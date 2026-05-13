"""
Browser Demo Router.

Provides a browser-compatible WebSocket endpoint for testing the AI voice
receptionist without needing Twilio. Sends real-time debug events to the
frontend so every step of the flow is visible.
"""
import os
import json
import base64
import asyncio
import time
from datetime import datetime
from typing import List
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import HTMLResponse

router = APIRouter(
    prefix="/api/demo",
    tags=["Demo & Debug"]
)

# Global list of connected debug WebSockets
debug_clients: List[WebSocket] = []

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_WS_URL = os.getenv(
    "OPENAI_WS_URL",
    "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17"
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
                import websockets
                await _debug("openai_connecting", "🤖 Connecting to OpenAI Realtime API...")

                headers = {
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "OpenAI-Beta": "realtime=v1"
                }
                openai_ws = await websockets.connect(OPENAI_WS_URL, additional_headers=headers)
                await _debug("openai_connected", "🟢 OpenAI Realtime API connected!")

                from services.prompts import system_prompt

                session_update = {
                    "type": "session.update",
                    "session": {
                        "modalities": ["text", "audio"],
                        "instructions": system_prompt(),
                        "voice": "alloy",
                        "input_audio_format": "pcm16",
                        "output_audio_format": "pcm16",
                        "input_audio_transcription": {"model": "whisper-1"},
                        "turn_detection": {
                            "type": "server_vad",
                            "threshold": 0.7,
                            "prefix_padding_ms": 300,
                            "silence_duration_ms": 1200
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
                        "instructions": "Greet the caller warmly. Say: Hello! I'm the AI receptionist for Pay Minimum Tax. How can I help you today?"
                    }
                }
                await openai_ws.send(json.dumps(initial_greeting))
                await _debug("greeting_sent", "🗣️ AI is preparing a greeting...")

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

            ai_chunk_count = 0
            try:
                async for openai_message in openai_ws:
                    if call_done.is_set():
                        break
                    openai_data = json.loads(openai_message)

                    if openai_data.get("type") == "response.audio.delta":
                        ai_chunk_count += 1
                        await _send({"type": "audio", "data": openai_data["delta"]})
                        if ai_chunk_count % 50 == 0:
                            await _debug("ai_audio", f"🎙️ Streamed {ai_chunk_count} AI audio chunks")

                    elif openai_data.get("type") in ["response.text.done", "response.audio_transcript.done"]:
                        text = openai_data.get("text") or openai_data.get("transcript")
                        if text:
                            ai_transcripts.append(text)
                            await _debug("ai_transcript", f"🤖 AI: {text}")
                            await _send({"type": "transcript", "role": "assistant", "text": text})

                    elif openai_data.get("type") == "conversation.item.input_audio_transcription.completed":
                        user_text = openai_data.get("transcript")
                        if user_text:
                            user_transcripts.append(user_text)
                            await _debug("user_transcript", f"👤 User: {user_text}")
                            await _send({"type": "transcript", "role": "user", "text": user_text})

                    elif openai_data.get("type") == "input_audio_buffer.speech_started":
                        await _debug("vad_speech_started", "🎤 Speech detected...")

                    elif openai_data.get("type") == "input_audio_buffer.speech_stopped":
                        await _debug("vad_speech_stopped", "🔇 Speech ended, processing...")

                    elif openai_data.get("type") == "response.function_call_arguments.done":
                        func_name = openai_data.get("name")
                        call_id = openai_data.get("call_id")
                        args = json.loads(openai_data.get("arguments", "{}"))
                        await _debug("tool_call", f"🛠️ AI called {func_name} with args: {args}")
                        result = {}
                        
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

        # ── Run both tasks concurrently ──
        await asyncio.gather(receive_from_browser(), send_to_browser())

    except WebSocketDisconnect:
        print("⚠️ [Demo] WebSocket disconnected (outer).")
    except Exception as e:
        print(f"🔴 [Demo] Unhandled error in voice-stream: {e}")
        import traceback
        traceback.print_exc()
    finally:
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
