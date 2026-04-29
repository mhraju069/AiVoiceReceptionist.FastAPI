import json
import base64
from fastapi import APIRouter, Request, Response, WebSocket, WebSocketDisconnect

router = APIRouter(
    prefix="/api/twilio",
    tags=["Twilio Webhooks"]
)

@router.post("/incoming-call")
async def incoming_call(request: Request):
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
    WebSocket endpoint for handling Twilio bidirectional audio streams.
    """
    await websocket.accept()
    stream_sid = None

    try:
        while True:
            # Twilio sends JSON messages
            message = await websocket.receive_text()
            data = json.loads(message)

            event = data.get("event")

            if event == "start":
                stream_sid = data["start"]["streamSid"]
                print(f"[{stream_sid}] Media stream started.")
                
                # Here you could initialize an AI connection, 
                # e.g. connect to OpenAI Realtime API websocket

            elif event == "media":
                # The audio coming from Twilio user (Base64 encoded mulaw format)
                payload = data["media"]["payload"]
                
                # TODO: Send 'payload' to your AI Model (STT/LLM) here.
                # Example:
                # 1. Decode base64 to raw audio bytes if needed: 
                #    audio_bytes = base64.b64decode(payload)
                # 2. Process audio with AI
                # 3. Receive AI response audio bytes, encode it to base64
                # 4. Send back to Twilio so the user hears it:
                
                # Example of sending audio back to Twilio (echoing is disabled here to avoid noise loop):
                # audio_response = {
                #     "event": "media",
                #     "streamSid": stream_sid,
                #     "media": {
                #         "payload": "YOUR_BASE64_ENCODED_AI_AUDIO"
                #     }
                # }
                # await websocket.send_json(audio_response)
                
                pass

            elif event == "stop":
                print(f"[{stream_sid}] Media stream stopped.")
                break

    except WebSocketDisconnect:
        print("WebSocket disconnected")
    except Exception as e:
        print(f"Error in Twilio stream: {e}")
    finally:
        # Cleanup AI connection here
        pass
