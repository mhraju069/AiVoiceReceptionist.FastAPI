import json
import random
import asyncio
from services.booking_service import book_appointment, get_slots

ADS = [
    "Stop overpaying. Join our waitlist for a free tax savings review with our CPA. We'll reach out as soon as a spot opens up.",
    "We don't just find savings; we help you keep them. Our team guides you through the entire process, ensuring you never feel left behind.",
    "Personalized tax strategies, not generic templates. We build a plan around your needs and execute it with precision.",
    "Paying over $30k in business taxes or earning $50k+ on a 1099? You're likely overpaying. Contact us for a complimentary CPA review to see how much you could save."
]

async def handle_book_appointment(args: dict, logger_or_debug) -> dict:
    try:
        result = await book_appointment(
            name=args.get("name", "Caller"),
            email=args.get("email", ""),
            phone=args.get("phone", ""),
            booking_slot=args.get("booking_slot", ""),
            calendar_type=args.get("calendar_type", "follow_up_b"),
            call_summary=args.get("call_summary", ""),
        )
        await logger_or_debug("tool_result", f"✅ Booking result: {result.get('status')}")
        return result
    except Exception as e:
        await logger_or_debug("tool_error", f"🔴 Booking exception: {e}")
        return {"status": "error", "message": "Sorry, there was a technical issue booking your appointment. Please try again later."}

async def handle_get_slots(args: dict, logger_or_debug) -> dict:
    try:
        result = await get_slots(
            calendar_type=args.get("calendar_type", "follow_up_b")
        )
        await logger_or_debug("tool_result", "✅ Slots fetched successfully.")
        return result
    except Exception as e:
        await logger_or_debug("tool_error", f"🔴 Slot fetch exception: {e}")
        return {"status": "error", "message": "Could not fetch available slots."}

async def handle_transfer_call(args: dict, openai_ws, call_done, call_id: str, logger_or_debug) -> dict:
    target = args.get("target", "tanzina").lower()
    await logger_or_debug("transfer_call", f"📲 Transfer started to {target}. Simulating hold flow...")
    
    ad_msg = random.choice(ADS)
    
    # Intro + Ad
    await openai_ws.send(json.dumps({
        "type": "response.create",
        "response": {
            "output_modalities": ["audio"],
            "instructions": f"Say this in the SAME LANGUAGE the user is currently speaking: (English: 'Please hold on for a moment while I connect you to {target}' or Bangla: 'Ektu hold korun, ami {target}-ke connect korchi.'). Do not paraphrase. Then, switch to ENGLISH and say this advertisement naturally: '{ad_msg}'"
        }
    }))

    async def _simulated_transfer_flow():
        await asyncio.sleep(12)
        if call_done.is_set():
            return
        await logger_or_debug("transfer_hold", "⏳ Still trying to connect (12s mark)...")
        await openai_ws.send(json.dumps({
            "type": "response.create",
            "response": {
                "output_modalities": ["audio"],
                "instructions": f"In the SAME LANGUAGE the user is speaking, say this exact text: (English: 'I am sorry, they haven\'t picked up yet. I am still trying to connect, please stay on the line.', Bangla: 'Sorry. Ami connect korar try korchi, ektu line-e thakun.'). Do not paraphrase."
            }
        }))
        
        await asyncio.sleep(12)
        if call_done.is_set():
            return
        await logger_or_debug("transfer_fail", "❌ Transfer failed (target unavailable).")
        await openai_ws.send(json.dumps({
            "type": "response.create",
            "response": {
                "output_modalities": ["audio"],
                "instructions": f"In the SAME LANGUAGE the user is speaking, say this exact text: (English: 'I am sorry, {target} is not available right now. I\'ll make sure they get your message. Is there anything else I can help you with today?', Bangla: 'Sorry, {target} ekhon available nai. Ami apnar jonno ekta message rakhte dischi. Ami ki apnake r kono help korte pari?'). Do not paraphrase."
            }
        }))
    
    asyncio.create_task(_simulated_transfer_flow())
    
    return {"status": "success", "message": f"Transferring to {target}"}

async def handle_end_call(
    args: dict, 
    openai_ws, 
    call_done, 
    end_call_in_progress, 
    transcript_history, 
    call_id: str, 
    logger_or_debug, 
    hangup_fn
) -> dict:
    reason = args.get("reason", "task_complete")
    await logger_or_debug("end_call_tool", f"👋 end_call tool called: {reason}")
    
    result = {"status": "success", "message": "Call ended."}
    
    # Send tool result back but DON'T trigger another response
    await openai_ws.send(json.dumps({
        "type": "conversation.item.create",
        "item": {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(result)
        }
    }))
    
    if not end_call_in_progress[0]:
        end_call_in_progress[0] = True
        
        async def _delayed_hangup():
            # Detect language from transcript history
            is_bangla_convo = False
            for entry in reversed(transcript_history):
                if any('\u0980' <= char <= '\u09FF' for char in entry):
                    is_bangla_convo = True
                    break
            
            if is_bangla_convo:
                goodbye_instr = "In BANGLA (Dhaka style), say a short warm goodbye like: 'ধন্যবাদ, ভালো থাকবেন। খোদা হাফেজ।' Then stop speaking."
            else:
                goodbye_instr = "In ENGLISH, say a short warm goodbye like: 'Thank you, goodbye. Have a nice day.' Then stop speaking."

            try:
                await openai_ws.send(json.dumps({"type": "response.cancel"}))
            except Exception:
                pass
            
            try:
                await openai_ws.send(json.dumps({
                    "type": "response.create",
                    "response": {
                        "output_modalities": ["audio"],
                        "instructions": goodbye_instr
                    }
                }))
                await logger_or_debug("end_call_consent", f"✅ Saying goodbye ({'Bangla' if is_bangla_convo else 'English'}), then ending.")
            except Exception as e:
                await logger_or_debug("end_call_error", f"Error triggering goodbye: {e}")
            
            await asyncio.sleep(4)
            await hangup_fn()
            
        asyncio.create_task(_delayed_hangup())
    else:
        await logger_or_debug("end_call_duplicate", "⏳ End call already in progress. Skipping duplicate.")
        
    return result
