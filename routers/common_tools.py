import json
import random
import asyncio
import os
import base64
import httpx
from zoneinfo import ZoneInfo
from datetime import datetime, time as datetime_time
from services.booking_service import book_appointment, get_slots

OFFICE_TIMEZONE = "America/New_York"
OFFICE_TZ = ZoneInfo(OFFICE_TIMEZONE)
OFFICE_OPEN = datetime_time(10, 0)
OFFICE_CLOSE = datetime_time(16, 0)

def is_office_open() -> bool:
    now_et = datetime.now(OFFICE_TZ)
    if now_et.weekday() >= 5: # Saturday or Sunday
        return False
    return OFFICE_OPEN <= now_et.time() < OFFICE_CLOSE

async def send_sms(to_number: str, message_body: str, logger_or_debug=None) -> bool:
    """Send an SMS using GoHighLevel or Twilio REST API."""
    sms_provider = os.getenv("SMS_PROVIDER", "ghl").lower()
    
    clean_to = to_number.strip()
    if clean_to and not clean_to.startswith("+"):
        if len(clean_to) == 10 and clean_to.isdigit():
            clean_to = f"+1{clean_to}"
        elif clean_to.startswith("1") and len(clean_to) == 11 and clean_to.isdigit():
            clean_to = f"+{clean_to}"
        else:
            clean_to = f"+{clean_to}"

    # Try sending via GHL if configured as provider
    if sms_provider == "ghl":
        from services.ghl import send_sms_via_ghl
        try:
            success = await send_sms_via_ghl(clean_to, message_body)
            if success:
                msg = f"✅ [SMS] Sent successfully via GHL to {clean_to}."
                if logger_or_debug:
                    await logger_or_debug("sms_success", msg)
                else:
                    print(msg)
                return True
            else:
                msg = f"⚠️ [SMS] GHL SMS failed. Falling back to Twilio..."
                if logger_or_debug:
                    await logger_or_debug("sms_warn", msg)
                else:
                    print(msg)
        except Exception as e:
            msg = f"⚠️ [SMS] Exception in GHL SMS: {e}. Falling back to Twilio..."
            if logger_or_debug:
                await logger_or_debug("sms_warn", msg)
            else:
                print(msg)

    # Twilio / Fallback Code
    twilio_sid = os.getenv("TWILIO_SID", "")
    twilio_token = os.getenv("TWILIO_AUTH_TOKEN", "")
    twilio_number = os.getenv("TWILIO_NUMBER", "")

    if not twilio_sid or not twilio_token or not twilio_number:
        msg = "❌ [SMS] Twilio credentials not configured. Cannot send SMS."
        if logger_or_debug:
            await logger_or_debug("sms_error", msg)
        else:
            print(msg)
        return False

    url = f"https://api.twilio.com/2010-04-01/Accounts/{twilio_sid}/Messages.json"
    auth_header = base64.b64encode(f"{twilio_sid}:{twilio_token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth_header}",
        "Content-Type": "application/x-www-form-urlencoded"
    }
    data = {
        "To": clean_to,
        "From": twilio_number,
        "Body": message_body
    }

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, headers=headers, data=data)
            if resp.status_code in (200, 201):
                msg = f"✅ [SMS] Sent successfully via Twilio fallback to {clean_to}."
                if logger_or_debug:
                    await logger_or_debug("sms_success", msg)
                else:
                    print(msg)
                return True
            else:
                msg = f"❌ [SMS] Failed to send to {clean_to} via Twilio fallback: {resp.status_code} - {resp.text}"
                if logger_or_debug:
                    await logger_or_debug("sms_error", msg)
                else:
                    print(msg)
                return False
    except Exception as e:
        msg = f"❌ [SMS] Exception sending to {clean_to} via Twilio fallback: {e}"
        if logger_or_debug:
            await logger_or_debug("sms_exception", msg)
        else:
            print(msg)
        return False

ADS = [
    "Stop overpaying. Join our waitlist for a free tax savings review with our CPA. We'll reach out as soon as a spot opens up.",
    "We don't just find savings; we help you keep them. Our team guides you through the entire process, ensuring you never feel left behind.",
    "Personalized tax strategies, not generic templates. We build a plan around your needs and execute it with precision.",
    "Paying over $30k in business taxes or earning $50k+ on a 1099? You're likely overpaying. Contact us for a complimentary CPA review to see how much you could save."
]

async def handle_book_appointment(args: dict, logger_or_debug) -> dict:
    try:
        name = args.get("name", "Caller")
        phone = args.get("phone", "")
        result = await book_appointment(
            name=name,
            email=args.get("email", ""),
            phone=phone,
            booking_slot=args.get("booking_slot", ""),
            calendar_type=args.get("calendar_type", "follow_up_b"),
            call_summary=args.get("call_summary", ""),
        )
        await logger_or_debug("tool_result", f"✅ Booking result: {result.get('status')}")

        if result.get("status") == "payment_required" and phone:
            payment_url = result.get("payment_url")
            sms_body = f"Hello {name}, here is your payment link to confirm your appointment: {payment_url}"
            sms_sent = await send_sms(phone, sms_body, logger_or_debug)

            # Update the message to reflect actual SMS delivery
            if sms_sent:
                result["sms_sent"] = True
                if result.get("email_sent"):
                    result["message"] = (
                        f"To confirm your appointment, a payment of ${result.get('price', '')} is required. "
                        f"A secure payment link has been sent to {args.get('email', '')} and texted to {phone}."
                    )
                else:
                    result["message"] = (
                        f"To confirm your appointment, a payment of ${result.get('price', '')} is required. "
                        f"The payment link has been texted to {phone}. "
                        f"Email delivery failed — our team will follow up."
                    )
            else:
                result["sms_sent"] = False
                if not result.get("email_sent"):
                    # Both channels failed — be fully honest
                    result["message"] = (
                        f"SMS_DELIVERY_FAILED EMAIL_DELIVERY_FAILED: "
                        f"The payment link was generated but could NOT be delivered to {phone} by SMS or email. "
                        f"Inform the caller that both delivery channels failed, and our team will contact them manually with the payment link."
                    )
                else:
                    result["message"] = (
                        f"To confirm your appointment, a payment of ${result.get('price', '')} is required. "
                        f"A secure payment link has been sent to {args.get('email', '')}. "
                        f"SMS_DELIVERY_FAILED: The text message to {phone} could not be delivered."
                    )

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
    
    if not is_office_open():
        await logger_or_debug("transfer_closed", f"📲 [Transfer] Transfer to {target} blocked: Office is closed.")
        return {"status": "office_closed", "message": "Office is closed. Will callback tomorrow."}

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
            # Detect language from caller/user speech only — skip AI transcript entries
            # Entries may be raw strings (demo.py) or prefixed with "Caller:"/"AI:" (twilio.py)
            is_bangla_convo = False
            banglish_indicators = {"ami", "apne", "apnar", "tumi", "kemon", "accha", "acha", "thik", "kore", "korechi", "koren", "kete", "den", "din", "ji", "ha", "na", "bhai", "somossa", "rakhlam", "rakhchi", "allah", "hafez", "khoda"}
            for entry in reversed(transcript_history):
                # Skip AI-generated transcript lines to avoid false Bangla detection
                if entry.startswith("AI:"):
                    continue
                # Strip "Caller: " prefix if present
                text = entry[len("Caller:"):].strip() if entry.startswith("Caller:") else entry
                if any('\u0980' <= char <= '\u09FF' for char in text):
                    is_bangla_convo = True
                    break
                words = [w.strip("?,.!") for w in text.lower().split()]
                if any(w in banglish_indicators for w in words):
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
                await asyncio.sleep(8)
            except Exception as e:
                await logger_or_debug("end_call_error", f"Error triggering goodbye: {e}")
            finally:
                await hangup_fn()
            
        asyncio.create_task(_delayed_hangup())
    else:
        await logger_or_debug("end_call_duplicate", "⏳ End call already in progress. Skipping duplicate.")
        
    return result

async def handle_send_link_sms(args: dict, default_phone: str, logger_or_debug) -> dict:
    link_type = args.get("link_type")
    phone = args.get("phone_number", default_phone)
    
    if not phone or phone == "N/A" or phone.strip() == "":
        return {"status": "error", "message": "No phone number available to send text."}
        
    links = {
        "signup": "portal.payminimumtax.com/signup",
        "login": "portal.payminimumtax.com/login",
        "upload": "www.PayMinimumTax.com/upload"
    }
    
    url = links.get(link_type)
    if not url:
        return {"status": "error", "message": f"Invalid link type '{link_type}'."}
        
    messages = {
        "signup": f"Here is the link to signup for Pay Minimum Tax services: {url}",
        "login": f"Here is the link to access your client portal: {url}",
        "upload": f"Please upload your tax notice directly using this link: {url}"
    }
    
    body = messages[link_type]
    sent = await send_sms(phone, body, logger_or_debug)
    if sent:
        return {
            "status": "success",
            "sms_sent": True,
            "message": f"Text message sent successfully to {phone}."
        }
    else:
        return {
            "status": "sms_failed",
            "sms_sent": False,
            "message": (
                f"SMS_DELIVERY_FAILED: The text message could NOT be delivered to {phone}. "
                f"Inform the caller that the link could not be texted at this time, "
                f"and our team will send it manually."
            )
        }

async def handle_record_message(args: dict, contact_id: str, default_name: str, default_phone: str, logger_or_debug) -> dict:
    caller_name_arg  = args.get("caller_name", default_name) or "Caller"
    caller_phone_arg = args.get("caller_phone", default_phone) or "N/A"
    message_text     = args.get("message", "")
    call_reason_arg  = args.get("call_reason", "other")
    
    await logger_or_debug("record_message_start", f"📝 [CRM] Recording message from {caller_name_arg}: {message_text}")
    note = (
        f"📞 Missed Call Note\n"
        f"Name: {caller_name_arg}\n"
        f"Phone: {caller_phone_arg}\n"
        f"Reason: {call_reason_arg}\n"
        f"Message: {message_text}"
    )
    
    from services.ghl import add_crm_note
    saved = False
    if contact_id:
        try:
            saved = await add_crm_note(contact_id, note)
        except Exception as e:
            await logger_or_debug("record_message_err", f"⚠️ Failed to save note to GHL: {e}")
            
    if saved:
        result = {"status": "success", "message": "Message recorded in CRM."}
    else:
        await logger_or_debug("record_message_local", f"📝 [CRM] No contact ID or CRM error, logging locally:\n{note}")
        result = {"status": "success", "message": "Message noted. Team will follow up."}
        
    # Send real-time SMS alerts to team members if mentioned
    target_lower = message_text.lower() + " " + call_reason_arg.lower()
    from config import FORWARD_SIMON, FORWARD_TANZINA, FORWARD_ALEX, FORWARD_NAFI
    alert_number = None
    alert_name = None
    if "simon" in target_lower:
        alert_number = FORWARD_SIMON
        alert_name = "Simon"
    elif "tanzina" in target_lower:
        alert_number = FORWARD_TANZINA
        alert_name = "Tanzina"
    elif "alex" in target_lower:
        alert_number = FORWARD_ALEX
        alert_name = "Alex"
    elif "nafi" in target_lower:
        alert_number = FORWARD_NAFI
        alert_name = "Nafi"

    if alert_number:
        alert_body = f"🔔 [PMT Alert] {caller_name_arg} ({caller_phone_arg}) left a message for {alert_name}: '{message_text}'"
        await send_sms(alert_number, alert_body, logger_or_debug)
        
    return result
