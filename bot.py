"""Main Telegram bot application."""
import io
import logging
import re
import sys
import secrets
import string
from datetime import time as dt_time
import pytz
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import Conflict
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)
from config import Config
from utils.database import Database
from utils.onetimesecret import OneTimeSecret
from utils.monday import MondayClient
from utils import ai_vision
from utils import motivation
from utils import phone_redact
from utils import vin_lookup

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Suppress duplicate error logging from telegram library for Conflict errors
logging.getLogger('telegram.ext.Updater').setLevel(logging.WARNING)
logging.getLogger('telegram.ext.Application').setLevel(logging.WARNING)

# Conversation states
STATE_PHASE1 = 1  # Waiting for vehicle and delivery details
STATE_PHASE2 = 2  # Waiting for phone number and price
STATE_SELECT_GROUP = 8   # Waiting for user to select which group (when assistants_choose_group is on)
STATE_SELECT_DRIVER = 3  # Waiting for user to select which driver(s) to notify
STATE_SELECT_CONTACT_SOURCE = 9  # After sending to drivers: select contact info source for this client
STATE_GROUP_BROADCAST_WAIT = 15  # Lead was broadcast to groups; waiting for them to accept/decline
STATE_VIN_CHOICE = 10  # VIN checker returned different car; user picks stated vs API
STATE_VIN_RETYPE = 14  # User chose to retype VIN; waiting for new VIN input
STATE_MISSING_FIELD = 11  # User must add missing field (e.g. color)
STATE_ADD_FILES = 12  # Ask "Do you want to add files?"
STATE_WAITING_FILE = 13  # Waiting for user to send file(s)

# Receipt submission states
STATE_WAITING_REFERENCE_ID = 4  # Waiting for reference ID input
STATE_WAITING_RECEIPT_CONFIRM = 5  # Waiting for receipt confirmation
STATE_WAITING_RECEIPT_IMAGE = 6  # Waiting for receipt image upload

# Initialize services
db = Database()
ots = OneTimeSecret()
monday = MondayClient() if Config.is_monday_configured() else None


SUSPENSION_THRESHOLD = 3  # 3+ pending receipts = suspended


def _get_suspended_driver_ids() -> set:
    """Driver IDs with 3+ pending receipts (suspended)."""
    suspended = set()
    try:
        drivers = db.get_all_drivers()
        for d in drivers:
            pending = db.get_driver_pending_receipts(d["id"])
            if len(pending) >= SUSPENSION_THRESHOLD:
                suspended.add(d["id"])
    except Exception as e:
        logger.warning("_get_suspended_driver_ids: %s", e)
    return suspended


def _build_driver_keyboard(drivers: list, exclude_suspended: bool = True, include_all: bool = True):
    """Build driver selection keyboard. Suspended drivers get driver_suspended_X callback and (PENALTY) label."""
    suspended = _get_suspended_driver_ids() if exclude_suspended else set()
    buttons = []
    for d in drivers:
        did = d.get("id")
        name = d.get("driver_name", "Unknown")
        if did in suspended:
            buttons.append([
                InlineKeyboardButton(
                    f"🚫 {name} (PENALTY)",
                    callback_data=f"driver_suspended_{did}"
                )
            ])
        else:
            buttons.append([
                InlineKeyboardButton(f"🚗 {name}", callback_data=f"select_driver_{did}")
            ])
    if include_all:
        elig = [d for d in drivers if d.get("id") not in suspended]
        if elig:
            buttons.append([InlineKeyboardButton("📢 Send to All Drivers", callback_data="select_driver_all")])
    return InlineKeyboardMarkup(buttons)


def _build_group_keyboard(groups: list, include_all: bool = True) -> InlineKeyboardMarkup:
    """Build group selection keyboard; optionally include broadcast-to-all."""
    buttons = [[InlineKeyboardButton(g.get("group_name", str(g["id"])), callback_data=f"select_group_{g['id']}")] for g in groups]
    if include_all and groups:
        buttons.append([InlineKeyboardButton("📢 Send to All Groups", callback_data="select_group_all")])
    return InlineKeyboardMarkup(buttons)


def _parse_chat_id(raw: str | int | None) -> int | str | None:
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError):
        return raw


async def _notify_initiator_and_supervisor(context: ContextTypes.DEFAULT_TYPE, lead: dict, text: str) -> None:
    """Send a notification to the lead initiator and global supervisor (if configured)."""
    initiator_id = lead.get("user_id")
    if initiator_id:
        try:
            await context.bot.send_message(chat_id=int(initiator_id), text=text, parse_mode="Markdown")
        except Exception as e:
            logger.warning("Could not notify initiator %s: %s", initiator_id, e)
    sup = (Config.SUPERVISORY_TELEGRAM_ID or "").strip()
    if sup:
        try:
            await context.bot.send_message(chat_id=_parse_chat_id(sup), text=text, parse_mode="Markdown")
        except Exception as e:
            logger.warning("Could not notify supervisor %s: %s", sup, e)


async def _send_driver_requests_for_group(
    context: ContextTypes.DEFAULT_TYPE,
    lead: dict,
    group: dict,
) -> tuple[int, str]:
    """Send accept/decline requests to drivers assigned to a group. Returns (count, driver_names)."""
    group_id = group.get("id")
    drivers = db.get_active_drivers_for_group(group_id) if group_id else []
    suspended = _get_suspended_driver_ids()
    selected_drivers = [d for d in (drivers or []) if d.get("id") not in suspended]
    if not selected_drivers:
        return (0, "")
    reference_id = lead.get("reference_id", "N/A")
    extra_safe = _sanitize_phones_for_send(lead.get("extra_info") or "")
    driver_request_message = (
        f"👋Hi! New client 💸 available📈❗️\n\n"
        f"📍 Delivery (City, State, Zip): {lead.get('delivery_details', '')}\n"
        f"📋 Reference ID: `{reference_id}`\n"
        f" Delivery Time 🏷️: {extra_safe}\n"
        f"Please have Car, Driver License, and Laser Printer Ready✅"
    )
    accept_keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Accept", callback_data=f"accept_lead_{lead['id']}"),
        InlineKeyboardButton("❌ Decline", callback_data=f"decline_lead_{lead['id']}"),
    ]])
    assigned_count = 0
    for driver in selected_drivers:
        cid = _parse_chat_id(driver.get("driver_telegram_id"))
        if not cid:
            continue
        try:
            db.create_lead_assignment(lead["id"], driver["id"], group_id)
            await context.bot.send_message(chat_id=cid, text=driver_request_message, parse_mode="Markdown", reply_markup=accept_keyboard)
            assigned_count += 1
        except Exception as e:
            logger.error("Error sending driver request to %s: %s", driver.get("driver_name"), e)
    driver_names = ", ".join(d.get("driver_name", "?") for d in selected_drivers)
    return (assigned_count, driver_names)


def generate_reference_id() -> str:
    """Generate a unique reference ID for lead tracking."""
    # Generate 8-character alphanumeric ID
    alphabet = string.ascii_uppercase + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(8))


def parse_phase1_structured(message_text: str) -> dict:
    """
    Parse Phase 1 structured input into individual fields.
    
    Expected structure (one item per line):
      1) Name
      2) Address
      3) City, State, ZIP
      4) Delivery address
      5) Delivery city, State, ZIP
      6) VIN
      7) Car (year, make, model)
      8) Color
      9) Insurance company
      10) Insurance policy number
      11) Extra info
    """
    lines = [line.strip() for line in message_text.splitlines() if line.strip()]
    
    def get_line(idx: int) -> str:
        return lines[idx] if idx < len(lines) else ""
    
    name = get_line(0)
    address = get_line(1)
    city_state_zip = get_line(2)
    delivery_address = get_line(3)
    delivery_city_state_zip = get_line(4)
    vin = get_line(5)
    car = get_line(6)
    color = get_line(7)
    insurance_company = get_line(8)
    insurance_policy_number = get_line(9)
    extra_info = get_line(10)
    
    # Vehicle details (for supervisor / group high-level view)
    vehicle_lines = [
        name,
        address,
        city_state_zip,
        vin,
        car,
        color,
        insurance_company,
        insurance_policy_number,
        extra_info,
    ]
    vehicle_details = "\n".join([l for l in vehicle_lines if l])
    
    # Delivery details (for driver)
    delivery_lines = [
        delivery_address,
        delivery_city_state_zip,
    ]
    delivery_details = "\n".join([l for l in delivery_lines if l])
    
    return {
        "name": name,
        "address": address,
        "city_state_zip": city_state_zip,
        "delivery_address": delivery_address,
        "delivery_city_state_zip": delivery_city_state_zip,
        "vin": vin,
        "car": car,
        "color": color,
        "insurance_company": insurance_company,
        "insurance_policy_number": insurance_policy_number,
        "extra_info": extra_info,
        "vehicle_details": vehicle_details,
        "delivery_details": delivery_details,
        "raw_text": message_text,
    }


def _apply_single_address_as_both(state_data: dict) -> None:
    """When only one address is provided (registration or delivery), use it for both."""
    def _has(v: str) -> bool:
        return bool(v and str(v).strip() and str(v).strip() != "-")
    addr = (state_data.get("address") or "").strip()
    csz = (state_data.get("city_state_zip") or "").strip()
    daddr = (state_data.get("delivery_address") or "").strip()
    dcsz = (state_data.get("delivery_city_state_zip") or "").strip()
    has_reg = _has(addr) or _has(csz)
    has_del = _has(daddr) or _has(dcsz)
    if has_reg and not has_del:
        state_data["delivery_address"] = addr or "-"
        state_data["delivery_city_state_zip"] = csz or "-"
    elif has_del and not has_reg:
        state_data["address"] = daddr or "-"
        state_data["city_state_zip"] = dcsz or "-"
    _clean_vin_and_car(state_data)


# Exactly 17 alphanumeric: the only valid VIN structure. Never cut or truncate.
VIN_PATTERN = re.compile(r"\b[A-Za-z0-9]{17}\b")


def _extract_vin_17(text: str) -> Optional[str]:
    """Return the first 17-character alphanumeric VIN found in text, or None. No truncation."""
    if not text:
        return None
    m = VIN_PATTERN.search(text)
    return m.group(0) if m else None


def _normalize_car_for_compare(car: str) -> str:
    """Normalize car string for comparison (lower, single spaces)."""
    return " ".join((car or "").lower().split())


def _vin_check_after_phase1(state_data: dict) -> tuple:
    """
    Run VIN lookup when we have a 17-char VIN. Uses provider from Config (.env).
    Returns:
      (alert_msg, conflict) where
      alert_msg: optional warning to show before Phase 2 (no result / not 17).
      conflict: (api_car_line, stated_car) if VIN returned different car; else None.
    """
    vin = (state_data.get("vin") or "").strip()
    if not vin or vin == "-" or len(vin) != 17:
        return ("⚠️ VIN not 17 characters; car not verified.", None)
    if not Config.is_vin_lookup_configured():
        return (None, None)
    result = vin_lookup.vin_lookup(
        vin,
        provider=Config.VIN_PROVIDER,
        api_key=Config.API_NINJAS_API_KEY,
    )
    if not result:
        return ("⚠️ VIN returned no result. Ensure it's 17 characters.", None)
    api_car = (result.get("car_line") or "").strip()
    stated = (state_data.get("car") or "").strip()
    if not api_car:
        return (None, None)
    if _normalize_car_for_compare(api_car) == _normalize_car_for_compare(stated):
        return (None, None)
    return (None, (api_car, stated))


def _vin_choice_keyboard(api_car: str, stated_car: str) -> InlineKeyboardMarkup:
    """Build keyboard for VIN conflict: keep driver details, use VIN search result, or retype VIN."""
    def _t(s: str, n: int = 35) -> str:
        s = s or ""
        return (s[:n] + "…") if len(s) > n else s
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Keep driver details in lead data: {_t(stated_car)}", callback_data="vin_keep")],
        [InlineKeyboardButton(f"Use VIN found in search: {_t(api_car)}", callback_data="vin_use")],
        [InlineKeyboardButton("Retype VIN", callback_data="vin_retype")],
    ])


def _clean_vin_and_car(state_data: dict) -> None:
    """Identify VIN only as a 17 alphanumeric string (no cutting). Clean car from phones and any stray VIN."""
    vin_raw = (state_data.get("vin") or "").strip()
    car_raw = (state_data.get("car") or "").strip()
    # Search for exactly 17 alphanumeric in vin field first, then vin+car, so we never miss a merged line
    search_for_vin = vin_raw + " " + car_raw
    vin_17 = _extract_vin_17(phone_redact.strip_phone_patterns(search_for_vin))
    if not vin_17:
        vin_17 = _extract_vin_17(vin_raw + " " + car_raw)
    state_data["vin"] = vin_17 if vin_17 else "-"
    # Car: strip phones and remove the 17-char VIN if it ended up in the car line (so we don't duplicate or leave fragment)
    car_cleaned = phone_redact.strip_phone_patterns(car_raw)
    if vin_17 and vin_17 in car_cleaned:
        car_cleaned = car_cleaned.replace(vin_17, " ", 1)
    car_cleaned = " ".join(car_cleaned.split()).strip()
    state_data["car"] = car_cleaned or "-"
    # Rebuild derived fields
    vehicle_lines = [
        state_data.get("name"),
        state_data.get("address"),
        state_data.get("city_state_zip"),
        state_data.get("vin"),
        state_data.get("car"),
        state_data.get("color"),
        state_data.get("insurance_company"),
        state_data.get("insurance_policy_number"),
        state_data.get("extra_info"),
    ]
    state_data["vehicle_details"] = "\n".join([l for l in vehicle_lines if l])
    delivery_lines = [
        state_data.get("delivery_address"),
        state_data.get("delivery_city_state_zip"),
    ]
    state_data["delivery_details"] = "\n".join([l for l in delivery_lines if l])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the conversation and initialize state."""
    user = update.effective_user
    user_id = user.id
    username = user.username or "Unknown"
    
    # Clear any existing state and attached files from previous leads
    db.clear_user_state(user_id)
    if context.user_data:
        context.user_data.pop("phase1_attached_files", None)
    
    # Initialize new state
    db.set_user_state(user_id, "phase1", {})
    
    phase1_instruction = (
        "Congratulations 🎊\n\n"
        "**Step 1:**\n"
        "📤 Send me\n"
        "👤 Name\n"
        "🏠 Reg Addr\n"
        "📍 Delivery Addr\n"
        "🔢 VIN #\n"
        "🚘 Car (Y/M/M)\n"
        "🎨 Color\n"
        "🛡 Insurance #\n"
        "🕒 Date & Time\n\n"
        "Send ✍️ Text or 📸 Screenshot\n\n"
        f"{motivation.get_random_quote()}\n\n"
        "🏁Automated🏎Automotive"
    )
    await update.message.reply_text(f"Welcome, @{username}! 👋\n\n{phase1_instruction}")
    
    return STATE_PHASE1


def _normalize_ai_phase1_text(text: str) -> str:
    """Strip optional leading 'N) ' from each line so parse_phase1_structured gets clean lines."""
    lines = []
    for line in text.splitlines():
        line = line.strip()
        # Remove leading "1) ", "2) ", ... "11) "
        line = re.sub(r"^\d{1,2}\)\s*", "", line).strip()
        lines.append(line)
    return "\n".join(lines)


def _sanitize_phones_for_send(text: str) -> str:
    """Replace any phone numbers in user content with OneTimeSecret links (no raw numbers)."""
    if not text or not str(text).strip():
        return text or ""
    return phone_redact.replace_phones_with_ots_links(str(text).strip(), ots)


async def handle_phase1_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle Phase 1 image upload: OCR + AI extract structured fields, then same flow as text."""
    user_id = update.effective_user.id
    if not Config.is_ai_vision_configured():
        await update.message.reply_text(
            "❌ Image extraction is not configured. Please send the details as text in the required structure."
        )
        return STATE_PHASE1
    await update.message.reply_text("⏳ Processing image…")
    if not update.message.photo:
        await update.message.reply_text("❌ No image received. Please send a screenshot or try sending as text.")
        return STATE_PHASE1
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    bio = io.BytesIO()
    await file.download_to_memory(out=bio)
    image_bytes = bio.getvalue()
    mime = "image/jpeg"
    if file.file_path and file.file_path.lower().endswith(".png"):
        mime = "image/png"
    try:
        raw_text = ai_vision.extract_structured_from_image(image_bytes, mime_type=mime)
    except ai_vision.AIVisionQuotaError:
        await update.message.reply_text(
            "❌ Image extraction is temporarily unavailable (API quota exceeded). "
            "Please send the details as text in the required structure."
        )
        return STATE_PHASE1
    if not raw_text or not raw_text.strip():
        await update.message.reply_text(
            "❌ Could not extract details from the image. Please send the details as text in the required structure."
        )
        return STATE_PHASE1
    normalized = _normalize_ai_phase1_text(raw_text)
    # Use first 11 lines only so field mapping is consistent (extra lines from AI are ignored)
    lines = [ln.strip() for ln in normalized.splitlines() if ln.strip()]
    normalized_11 = "\n".join(lines[: ai_vision.PHASE1_LINE_COUNT]) if len(lines) >= ai_vision.PHASE1_LINE_COUNT else normalized
    state_data = parse_phase1_structured(normalized_11)
    _apply_single_address_as_both(state_data)
    # Built-in checks: at least 11 lines, required fields (name + delivery); VIN format not enforced
    valid, validation_errors = ai_vision.validate_phase1_extraction(normalized_11, state_data)
    if not valid:
        err_blurb = "\n• ".join(validation_errors)
        preview = (
            f"Name: {state_data.get('name') or '-'}\n"
            f"VIN: {state_data.get('vin') or '-'}\n"
            f"Delivery: {state_data.get('delivery_address') or '-'} / {state_data.get('delivery_city_state_zip') or '-'}"
        )
        await update.message.reply_text(
            "⚠️ Extraction didn’t pass validation:\n\n• " + err_blurb + "\n\n"
            "Extracted preview:\n" + preview + "\n\n"
            "Please send the details as text in the required 11-line structure, or try another image."
        )
        return STATE_PHASE1
    db.set_user_state(user_id, "phase1", state_data)
    alert_msg, conflict = _vin_check_after_phase1(state_data)
    if conflict:
        api_car, stated_car = conflict
        context.user_data["vin_choice_api_car"] = api_car
        context.user_data["vin_choice_stated_car"] = stated_car
        keyboard = _vin_choice_keyboard(api_car, stated_car)
        await update.message.reply_text(
            "⚠️ **VIN returned different car than stated**\n\n"
            f"• Driver details in lead: {stated_car}\n"
            f"• VIN lookup result: {api_car}\n\n"
            "Choose which to use:",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return STATE_VIN_CHOICE
    if alert_msg:
        await update.message.reply_text(alert_msg)
    missing = ai_vision.detect_missing_fields(state_data, normalized_11 or "")
    if missing:
        prompts = ai_vision.MISSING_FIELD_PROMPTS
        msg = prompts.get(missing[0], (f"You missed out {missing[0]}. Can you add it?", missing[0]))[0]
        context.user_data["missing_fields"] = missing
        context.user_data["missing_field_state_data"] = state_data.copy()
        await update.message.reply_text(msg)
        return STATE_MISSING_FIELD
    return await _ask_add_files(update.message, context)


async def handle_phase1(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle Phase 1: Vehicle and delivery details. If AI is configured, accept any format and let model rearrange."""
    user_id = update.effective_user.id
    message_text = (update.message.text or "").strip()
    if not message_text:
        await update.message.reply_text("Please send the client/vehicle and delivery details (text or a screenshot).")
        return STATE_PHASE1

    if Config.is_ai_vision_configured():
        await update.message.reply_text("⏳ Processing…")
        try:
            raw_text = ai_vision.extract_structured_from_text(message_text)
        except ai_vision.AIVisionQuotaError:
            await update.message.reply_text(
                "❌ Processing is temporarily unavailable (API quota). "
                "Please try again later or send in the 11-line structure."
            )
            return STATE_PHASE1
        if not raw_text or not raw_text.strip():
            await update.message.reply_text(
                "❌ I couldn't extract the fields from that message. "
                "Try rephrasing or send name, address, delivery, VIN, car, and delivery time."
            )
            return STATE_PHASE1
        normalized = _normalize_ai_phase1_text(raw_text)
        lines = [ln.strip() for ln in normalized.splitlines() if ln.strip()]
        normalized_11 = "\n".join(lines[: ai_vision.PHASE1_LINE_COUNT]) if len(lines) >= ai_vision.PHASE1_LINE_COUNT else normalized
        state_data = parse_phase1_structured(normalized_11)
        _apply_single_address_as_both(state_data)
        valid, validation_errors = ai_vision.validate_phase1_extraction(normalized_11, state_data)
        if not valid:
            err_blurb = "\n• ".join(validation_errors)
            await update.message.reply_text(
                "⚠️ I couldn't find enough info:\n\n• " + err_blurb + "\n\n"
                "Please include at least name and delivery address/city."
            )
            return STATE_PHASE1
        db.set_user_state(user_id, "phase1", state_data)
        alert_msg, conflict = _vin_check_after_phase1(state_data)
        if conflict:
            api_car, stated_car = conflict
            context.user_data["vin_choice_api_car"] = api_car
            context.user_data["vin_choice_stated_car"] = stated_car
            keyboard = _vin_choice_keyboard(api_car, stated_car)
            await update.message.reply_text(
                "⚠️ **VIN returned different car than stated**\n\n"
                f"• Driver details in lead: {stated_car}\n"
                f"• VIN lookup result: {api_car}\n\n"
                "Choose which to use:",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
            return STATE_VIN_CHOICE
        if alert_msg:
            await update.message.reply_text(alert_msg)
        missing = ai_vision.detect_missing_fields(state_data, message_text)
        if missing:
            prompts = ai_vision.MISSING_FIELD_PROMPTS
            msg = prompts.get(missing[0], (f"You missed out {missing[0]}. Can you add it?", missing[0]))[0]
            context.user_data["missing_fields"] = missing
            context.user_data["missing_field_state_data"] = state_data.copy()
            await update.message.reply_text(msg)
            return STATE_MISSING_FIELD
        return await _ask_add_files(update.message, context)
    else:
        # No AI: require the 11-line structure
        state_data = parse_phase1_structured(message_text)
        _apply_single_address_as_both(state_data)
        db.set_user_state(user_id, "phase1", state_data)
        alert_msg, conflict = _vin_check_after_phase1(state_data)
        if conflict:
            api_car, stated_car = conflict
            context.user_data["vin_choice_api_car"] = api_car
            context.user_data["vin_choice_stated_car"] = stated_car
            keyboard = _vin_choice_keyboard(api_car, stated_car)
            await update.message.reply_text(
                "⚠️ **VIN returned different car than stated**\n\n"
                f"• Driver details in lead: {stated_car}\n"
                f"• VIN lookup result: {api_car}\n\n"
                "Choose which to use:",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
            return STATE_VIN_CHOICE
        if alert_msg:
            await update.message.reply_text(alert_msg)
        missing = ai_vision.detect_missing_fields(state_data, message_text)
        if missing:
            prompts = ai_vision.MISSING_FIELD_PROMPTS
            msg = prompts.get(missing[0], (f"You missed out {missing[0]}. Can you add it?", missing[0]))[0]
            context.user_data["missing_fields"] = missing
            context.user_data["missing_field_state_data"] = state_data.copy()
            await update.message.reply_text(msg)
            return STATE_MISSING_FIELD
        return await _ask_add_files(update.message, context)


async def _ask_add_files(message, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Ask user if they want to add files; returns STATE_ADD_FILES."""
    context.user_data["phase1_attached_files"] = []
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Yes", callback_data="add_files_yes")],
        [InlineKeyboardButton("No", callback_data="add_files_no")],
    ])
    await message.reply_text("Do you want to add files?", reply_markup=keyboard)
    return STATE_ADD_FILES


async def handle_add_files_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle add_files_yes / add_files_no."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if query.data == "add_files_no":
        state = db.get_user_state(user_id)
        if state and state.get("data"):
            d = state["data"].copy()
            d["attached_files"] = context.user_data.get("phase1_attached_files") or []
            db.set_user_state(user_id, "phase1", d)
        await query.message.reply_text(
            "✅ Phase 1 received!\n\n"
            "**Phase 2:** Please provide phone number and price.\n"
            "Format: Phone number and price (e.g., '+1234567890 $500')"
        )
        return STATE_PHASE2
    # add_files_yes
    await query.message.reply_text("📎 Send the file (photo or document).")
    return STATE_WAITING_FILE


async def handle_waiting_file_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User sent text instead of file; remind them."""
    await update.message.reply_text(
        "Please send a photo or document to attach. If you're done, tap No on the previous message."
    )
    return STATE_WAITING_FILE


async def handle_file_upload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle file (photo/document) when in STATE_WAITING_FILE."""
    files = context.user_data.get("phase1_attached_files") or []
    if update.message.photo:
        file_id = update.message.photo[-1].file_id
        files.append({"type": "photo", "file_id": file_id})
    elif update.message.document:
        file_id = update.message.document.file_id
        files.append({"type": "document", "file_id": file_id})
    context.user_data["phase1_attached_files"] = files
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("Yes", callback_data="another_file_yes")],
        [InlineKeyboardButton("No", callback_data="another_file_no")],
    ])
    await update.message.reply_text("Do you want to send another file?", reply_markup=keyboard)
    return STATE_WAITING_FILE


async def handle_another_file_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle another_file_yes / another_file_no."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if query.data == "another_file_no":
        state = db.get_user_state(user_id)
        if state and state.get("data"):
            d = state["data"].copy()
            d["attached_files"] = context.user_data.get("phase1_attached_files") or []
            db.set_user_state(user_id, "phase1", d)
        await query.message.reply_text(
            "✅ Phase 1 received!\n\n"
            "**Phase 2:** Please provide phone number and price.\n"
            "Format: Phone number and price (e.g., '+1234567890 $500')"
        )
        return STATE_PHASE2
    await query.message.reply_text("📎 Send the file (photo or document).")
    return STATE_WAITING_FILE


# Maps API field names to state_data keys (e.g. delivery_date -> extra_info)
MISSING_FIELD_TO_STATE_KEY = {"delivery_date": "extra_info"}


async def handle_missing_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle user reply when we asked for a missing field (e.g. color)."""
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        await update.message.reply_text("Please send the missing value.")
        return STATE_MISSING_FIELD
    missing_fields = context.user_data.get("missing_fields") or []
    state_data = context.user_data.get("missing_field_state_data") or {}
    field = missing_fields[0] if missing_fields else "color"
    state_key = MISSING_FIELD_TO_STATE_KEY.get(field, field)
    state_data[state_key] = text
    missing_fields = missing_fields[1:]
    context.user_data["missing_fields"] = missing_fields
    context.user_data["missing_field_state_data"] = state_data
    if missing_fields:
        next_field = missing_fields[0]
        prompts = ai_vision.MISSING_FIELD_PROMPTS
        msg = prompts.get(next_field, (f"You missed out {next_field}. Can you add it?", next_field))[0]
        await update.message.reply_text(msg)
        return STATE_MISSING_FIELD
    db.set_user_state(user_id, "phase1", state_data)
    context.user_data.pop("missing_fields", None)
    context.user_data.pop("missing_field_state_data", None)
    alert_msg, conflict = _vin_check_after_phase1(state_data)
    if conflict:
        api_car, stated_car = conflict
        context.user_data["vin_choice_api_car"] = api_car
        context.user_data["vin_choice_stated_car"] = stated_car
        keyboard = _vin_choice_keyboard(api_car, stated_car)
        await update.message.reply_text(
            "⚠️ **VIN returned different car than stated**\n\n"
            f"• Driver details in lead: {stated_car}\n"
            f"• VIN lookup result: {api_car}\n\n"
            "Choose which to use:",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return STATE_VIN_CHOICE
    if alert_msg:
        await update.message.reply_text(alert_msg)
    return await _ask_add_files(update.message, context)


async def handle_vin_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle VIN conflict choice: use API result, keep stated car, or retype VIN."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    if query.data == "vin_retype":
        await query.message.reply_text("Please type the correct VIN (17 characters):")
        return STATE_VIN_RETYPE
    if query.data == "vin_use":
        api_car = context.user_data.get("vin_choice_api_car")
        if api_car:
            state = db.get_user_state(user_id)
            if state and state.get("data"):
                d = state["data"]
                d["car"] = api_car
                vehicle_lines = [
                    d.get("name"), d.get("address"), d.get("city_state_zip"),
                    d.get("vin"), d.get("car"), d.get("color"),
                    d.get("insurance_company"), d.get("insurance_policy_number"), d.get("extra_info"),
                ]
                d["vehicle_details"] = "\n".join([l for l in vehicle_lines if l])
                db.set_user_state(user_id, "phase1", d)
        context.user_data.pop("vin_choice_api_car", None)
        context.user_data.pop("vin_choice_stated_car", None)
    else:
        context.user_data.pop("vin_choice_api_car", None)
        context.user_data.pop("vin_choice_stated_car", None)
    return await _ask_add_files(query.message, context)


async def handle_vin_retype(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle user's new VIN input; re-run lookup and either proceed or show choice again."""
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    vin_new = _extract_vin_17(text)
    if not vin_new or len(vin_new) != 17:
        await update.message.reply_text(
            "Please send a valid 17-character VIN (letters and numbers only)."
        )
        return STATE_VIN_RETYPE
    state = db.get_user_state(user_id)
    if not state or not state.get("data"):
        await update.message.reply_text("❌ Error: Phase 1 data not found. Please start over with /start")
        return ConversationHandler.END
    state_data = state["data"].copy()
    state_data["vin"] = vin_new
    _clean_vin_and_car(state_data)
    db.set_user_state(user_id, "phase1", state_data)
    alert_msg, conflict = _vin_check_after_phase1(state_data)
    if conflict:
        api_car, stated_car = conflict
        context.user_data["vin_choice_api_car"] = api_car
        context.user_data["vin_choice_stated_car"] = stated_car
        keyboard = _vin_choice_keyboard(api_car, stated_car)
        await update.message.reply_text(
            "⚠️ **VIN returned different car than stated**\n\n"
            f"• Driver details in lead: {stated_car}\n"
            f"• VIN lookup result: {api_car}\n\n"
            "Choose which to use:",
            reply_markup=keyboard,
            parse_mode="Markdown",
        )
        return STATE_VIN_CHOICE
    if alert_msg:
        await update.message.reply_text(alert_msg)
    return await _ask_add_files(update.message, context)


async def handle_phase2(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle Phase 2: Phone number and price, then process the lead."""
    user_id = update.effective_user.id
    username = update.effective_user.username or "Unknown"
    message_text = update.message.text
    
    # Get phase 1 data
    state = db.get_user_state(user_id)
    if not state or not state.get("data"):
        await update.message.reply_text("❌ Error: Phase 1 data not found. Please start over with /start")
        return ConversationHandler.END
    
    phase1_data = state.get("data", {})
    
    # Parse phone and price: any token containing $ is the price; phone = any format accepted (digits only)
    parts = message_text.split()
    price = next((p for p in parts if "$" in p), None)
    # Build text without price so we don't take digits from "$500" etc.
    non_price_text = " ".join(p for p in parts if "$" not in p)
    digits_only = re.sub(r"\D", "", non_price_text)
    if len(digits_only) == 11 and digits_only.startswith("1"):
        digits_only = digits_only[1:]
    # If 10 digits start with 1, treat as +1 (xxx) xxx-xxx so we don't store 11234567890 in OTS
    if len(digits_only) == 10 and digits_only.startswith("1"):
        digits_only = digits_only[1:]
    if len(digits_only) not in (9, 10) or not price:
        await update.message.reply_text(
            "❌ Please provide both phone number and price.\n"
            "Phone in any format (e.g. +1 (732) 534-2659, 732-534-2659, 732 534 2659) and price with $ (e.g. $500)."
        )
        return STATE_PHASE2
    # Normalize to +1XXXXXXXXXX for storage (no double 1)
    phone_number = "+1" + digits_only
    
    # Encrypt phone number via OneTimeSecret
    await update.message.reply_text("🔐 Encrypting phone number...")
    encrypted_data = ots.encrypt_phone(phone_number)
    
    if not encrypted_data:
        await update.message.reply_text("❌ Error encrypting phone number. Please try again.")
        return STATE_PHASE2
    
    # Generate unique reference ID for this lead
    reference_id = generate_reference_id()
    
    # Determine which group this lead belongs to
    groups = db.get_all_groups()
    active_groups = [g for g in groups if g.get('is_active', True)]
    
    if not active_groups:
        await update.message.reply_text("❌ Error: No active groups configured. Please contact admin.")
        return ConversationHandler.END
    
    assistants_choose_group = (db.get_setting("assistants_choose_group") or "").lower() in ("true", "1", "yes")
    
    if assistants_choose_group:
        # Anyone can send; they choose driver then group. Show group picker first (order: choose group then driver).
        # Actually user said: "choose a driver then choose a group". So order is: driver first, then group.
        # Re-read: "assistants prefer to choose groups" and "if assistants can choose groups ... anyone at all can send data choose a driver then choose a group".
        # So flow when toggle is on: send data → choose driver → choose group. So we need to show driver picker first, then group picker.
        # Wait - "assistants prefer to choose groups to send the lead to. so follow that order" - so the preferred order is choose group first. Then "if assistants can choose groups ... anyone at all can send data choose a driver then choose a group" - that might mean: anyone can send, and they choose driver then choose group (as in: they do two things: choose driver and choose group). So the order could be either. I'll do: when toggle on, show GROUP picker first, then driver picker (so: choose group → choose driver). That matches "assistants prefer to choose groups" (group choice first).
        state_data = phase1_data.copy()
        state_data.update({
            "phone_number": phone_number,
            "price": price,
            "encrypted_data": encrypted_data,
            "reference_id": reference_id,
            "username": username
        })
        db.set_user_state(user_id, "select_group", state_data)
        group_keyboard = _build_group_keyboard(active_groups, include_all=True)
        await update.message.reply_text(
            "✅ Phone and price received!\n\n**Select which group to send this lead to:**",
            parse_mode="Markdown",
            reply_markup=group_keyboard
        )
        return STATE_SELECT_GROUP
    
    # Use assigned group (assistant's group or first active)
    user_telegram_id = str(update.effective_user.id)
    assistant_group = db.get_group_by_assistant_telegram_id(user_telegram_id)
    if assistant_group and assistant_group.get('is_active', True):
        selected_group = assistant_group
        group_id = selected_group['id']
        logger.info(f"User is assistant for group '{selected_group.get('group_name')}'; using that group for lead")
    else:
        selected_group = active_groups[0]
        group_id = selected_group['id']
    logger.info(
        f"Using group '{selected_group.get('group_name')}' (id={group_id}, "
        f"group_telegram_id={selected_group.get('group_telegram_id')}) for lead"
    )
    
    state_data = phase1_data.copy()
    state_data.update({
        "phone_number": phone_number,
        "price": price,
        "encrypted_data": encrypted_data,
        "reference_id": reference_id,
        "group_id": group_id,
        "selected_group": selected_group,
        "username": username
    })
    db.set_user_state(user_id, "select_driver", state_data)
    
    drivers = db.get_all_drivers()
    active_drivers = [d for d in drivers if d.get('is_active', True)]
    if not active_drivers:
        await update.message.reply_text("❌ Error: No active drivers found. Please contact admin.")
        return ConversationHandler.END
    
    driver_keyboard = _build_driver_keyboard(drivers, exclude_suspended=True, include_all=True)
    driver_list = "\n".join([f"• {d.get('driver_name', 'Unknown')}" for d in drivers])
    await update.message.reply_text(
        f"✅ Phone and price received!\n\n"
        f"**Select which driver(s) to notify:**\n\n"
        f"Available drivers:\n{driver_list}\n\n"
        f"Click a driver below or send to all:",
        parse_mode="Markdown",
        reply_markup=driver_keyboard
    )
    return STATE_SELECT_DRIVER


async def handle_group_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle group selection when assistants_choose_group is on; then show driver picker."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    state = db.get_user_state(user_id)
    if not state or not state.get("data"):
        await query.message.reply_text("❌ Error: Lead data not found. Please start over with /start")
        return ConversationHandler.END
    lead_data = state.get("data", {}).copy()
    if query.data == "select_group_all":
        # Broadcast: create lead immediately, send offer to all active groups, first accept wins.
        phase1_data = {k: v for k, v in lead_data.items() if k not in ['phone_number', 'price', 'encrypted_data', 'reference_id', 'group_id', 'selected_group', 'resend', 'lead_id']}
        final_lead_data = {
            "user_id": user_id,
            "telegram_username": (query.from_user.username or "Unknown"),
            "vehicle_details": phase1_data.get("vehicle_details", ""),
            "delivery_details": phase1_data.get("delivery_details", ""),
            "phone_number": lead_data.get("phone_number"),
            "price": lead_data.get("price"),
            "onetimesecret_token": (lead_data.get("encrypted_data") or {}).get("secret_key"),
            "onetimesecret_secret_key": (lead_data.get("encrypted_data") or {}).get("metadata_key"),
            "encrypted_link": (lead_data.get("encrypted_data") or {}).get("link"),
            "reference_id": lead_data.get("reference_id"),
            "group_id": None,
            "extra_info": lead_data.get("extra_info", ""),
        }
        lead = db.create_lead(final_lead_data)
        if not lead:
            await query.message.reply_text("❌ Error saving lead to database.")
            return ConversationHandler.END

        reference_id = lead.get("reference_id", "N/A")
        username = query.from_user.username or "Unknown"
        group_offer_message = (
            f"🏷NEW CLIENT❗️\n\n"
            f"📋 Reference ID: `{reference_id}`\n"
            f"👤 Submitted by: @{username}\n\n"
            f"Tap below to accept/decline for your group."
        )
        offer_kb_by_group: dict[str, InlineKeyboardMarkup] = {}
        groups = db.get_all_groups()
        active_groups = [g for g in groups if g.get("is_active", True)]
        for g in active_groups:
            gid = g.get("id")
            if not gid:
                continue
            offer_kb_by_group[gid] = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Accept (Group)", callback_data=f"accept_group_{lead['id']}_{gid}"),
                InlineKeyboardButton("❌ Decline", callback_data=f"decline_group_{lead['id']}_{gid}"),
            ]])
        sent_count = 0
        for g in active_groups:
            gid = g.get("id")
            chat_id = _parse_chat_id(g.get("group_telegram_id"))
            if not gid or not chat_id:
                continue
            # Create offer row first; we'll fill message IDs after sending.
            db.create_group_lead_offer(lead["id"], gid, group_chat_id=str(chat_id), group_message_id=None)
            try:
                msg = await context.bot.send_message(
                    chat_id=chat_id,
                    text=group_offer_message,
                    parse_mode="Markdown",
                    reply_markup=offer_kb_by_group.get(gid),
                )
                db.update_group_lead_offer_message(lead["id"], gid, str(chat_id), msg.message_id)
                sent_count += 1
            except Exception as e:
                logger.error("Error sending group offer to %s: %s", g.get("group_name"), e)

        db.set_user_state(
            user_id,
            "group_broadcast_wait",
            {"lead_id": lead["id"], "reference_id": reference_id, "username": username},
        )

        await _notify_initiator_and_supervisor(
            context,
            lead,
            f"📣 **Lead broadcast to groups**\n\nReference: `{reference_id}`\nSent to **{sent_count}** group(s).",
        )

        await query.message.reply_text(
            f"📣 **Broadcast sent**\n\nReference ID: `{reference_id}`\nSent to **{sent_count}** group(s).\n\n"
            "First group to accept will get it.",
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    group_id = query.data.replace("select_group_", "")
    selected_group = db.get_group_by_id(group_id)
    if not selected_group or not selected_group.get("is_active", True):
        await query.message.reply_text("❌ Group not found or inactive. Please start over with /start")
        return ConversationHandler.END
    lead_data["group_id"] = group_id
    lead_data["selected_group"] = selected_group
    db.set_user_state(user_id, "select_driver", lead_data)
    drivers = db.get_all_drivers()
    active_drivers = [d for d in drivers if d.get("is_active", True)]
    if not active_drivers:
        await query.message.reply_text("❌ Error: No active drivers found. Please contact admin.")
        return ConversationHandler.END
    driver_keyboard = _build_driver_keyboard(drivers, exclude_suspended=True, include_all=True)
    driver_list = "\n".join([f"• {d.get('driver_name', 'Unknown')}" for d in drivers])
    await query.message.reply_text(
        f"✅ Group selected: **{selected_group.get('group_name', 'N/A')}**\n\n"
        f"**Select which driver(s) to notify:**\n\n"
        f"Available drivers:\n{driver_list}\n\n"
        f"Click a driver below or send to all:",
        parse_mode="Markdown",
        reply_markup=driver_keyboard
    )
    return STATE_SELECT_DRIVER


async def handle_driver_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle driver selection after Phase 2 (or after group selection, or after timeout resend)."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Get stored lead data
    state = db.get_user_state(user_id)
    if not state or not state.get("data"):
        await query.message.reply_text("❌ Error: Lead data not found. Please start over with /start")
        return ConversationHandler.END
    
    lead_data = state.get("data", {})
    
    # Resend flow: lead exists, just send to new drivers
    if lead_data.get("resend") and lead_data.get("lead_id"):
        return await _handle_resend_to_drivers(
            update, context, lead_data, query.data, user_id,
        )
    
    phase1_data = {k: v for k, v in lead_data.items() if k not in ['phone_number', 'price', 'encrypted_data', 'reference_id', 'group_id', 'selected_group', 'resend', 'lead_id']}
    phone_number = lead_data.get('phone_number')
    price = lead_data.get('price')
    encrypted_data = lead_data.get('encrypted_data', {})
    reference_id = lead_data.get('reference_id')
    group_id = lead_data.get('group_id')
    selected_group = lead_data.get('selected_group')
    username = query.from_user.username or "Unknown"
    
    # Determine which drivers to notify
    # Drivers work for all groups, so get all active drivers
    callback_data = query.data
    all_drivers = db.get_all_drivers()
    active_drivers = [d for d in all_drivers if d.get('is_active', True)]
    
    suspended = _get_suspended_driver_ids()
    if callback_data == "select_driver_all":
        selected_drivers = [d for d in active_drivers if d['id'] not in suspended]
        selected_driver_ids = [d['id'] for d in selected_drivers]
        if not selected_drivers:
            await query.message.reply_text("❌ No eligible drivers (all suspended). Please select a driver individually.")
            return STATE_SELECT_DRIVER
    elif callback_data.startswith("driver_suspended_"):
        driver_id = callback_data.replace("driver_suspended_", "")
        driver = next((d for d in all_drivers if d["id"] == driver_id), None)
        name = driver.get("driver_name", "Driver") if driver else "Driver"
        pending = db.get_driver_pending_receipts(driver_id) if driver_id else []
        count = len(pending)
        await query.message.reply_text(
            f"⚠️ **{name}** is temporarily suspended (PENALTY).\n\n"
            f"They owe {count} receipt(s). No leads will be sent until all receipts are uploaded."
        )
        # Notify driver that dispatcher tried to send lead
        tid = driver.get("driver_telegram_id") if driver else None
        if tid and pending:
            try:
                cid = int(str(tid).strip())
                ref_buttons = [
                    [InlineKeyboardButton(f"📋 {p['reference_id']}", callback_data=f"receipt_for_{p['reference_id']}")]
                    for p in pending[:10]
                ]
                await context.bot.send_message(
                    chat_id=cid,
                    text=(
                        f"⛔ **Temporary suspension**\n\n"
                        f"Dispatcher tried to send you a lead, but you owe **{count}** receipt(s).\n\n"
                        f"Upload all receipts below to resume receiving leads:"
                    ),
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(ref_buttons),
                )
            except Exception as e:
                logger.warning("Could not notify suspended driver: %s", e)
        return STATE_SELECT_DRIVER
    else:
        # Send to selected driver
        driver_id = callback_data.replace("select_driver_", "")
        selected_driver_ids = [driver_id]
        selected_drivers = [d for d in active_drivers if d['id'] == driver_id]
        if not selected_drivers:
            await query.message.reply_text("❌ Error: Driver not found.")
            return ConversationHandler.END
        if driver_id in suspended:
            await query.message.reply_text("❌ This driver is suspended. Please select another.")
            return STATE_SELECT_DRIVER
    
    # Create lead in database
    final_lead_data = {
        "user_id": user_id,
        "telegram_username": username,
        "vehicle_details": phase1_data.get("vehicle_details", ""),
        "delivery_details": phase1_data.get("delivery_details", ""),
        "phone_number": phone_number,
        "price": price,
        "onetimesecret_token": encrypted_data.get("secret_key"),
        "onetimesecret_secret_key": encrypted_data.get("metadata_key"),
        "encrypted_link": encrypted_data.get("link"),
        "reference_id": reference_id,
        "group_id": group_id,
        # Store extra_info explicitly so we can show it to drivers/supervisors later
        "extra_info": lead_data.get("extra_info", "")
    }
    
    lead = db.create_lead(final_lead_data)
    
    if not lead:
        await query.message.reply_text("❌ Error saving lead to database.")
        return ConversationHandler.END
    
    # Build vehicle block from individual fields so VIN and car are NEVER sanitized (no link in those lines)
    def _safe(s: str) -> str:
        return _sanitize_phones_for_send(s or "") or "-"
    vin_only = (phase1_data.get("vin") or "").strip() or "-"
    car_only = (phase1_data.get("car") or "").strip() or "-"
    vehicle_lines_display = [
        _safe(phase1_data.get("name")),
        _safe(phase1_data.get("address")),
        _safe(phase1_data.get("city_state_zip")),
        vin_only,
        car_only,
        _safe(phase1_data.get("color")),
        _safe(phase1_data.get("insurance_company")),
        _safe(phase1_data.get("insurance_policy_number")),
        _safe(phase1_data.get("extra_info")),
    ]
    vehicle_safe = "\n".join(vehicle_lines_display)
    delivery_safe = _sanitize_phones_for_send(phase1_data.get('delivery_details', '') or '')
    extra_safe = _sanitize_phones_for_send(phase1_data.get('extra_info', '') or '')

    # Create item in Monday.com (if configured)
    monday_result = None
    if monday:
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="📊 Syncing with Monday.com..."
        )
        monday_lead_data = {
            "name": phase1_data.get("name", ""),
            "phone_number": phone_number,
            "price": price,
            "delivery_address": phase1_data.get("delivery_address", ""),
            "delivery_city_state_zip": phase1_data.get("delivery_city_state_zip", ""),
            "group_message": (
                "🏷NEW CLIENT❗️\n\n"
                f"🚗 Vehicle: {vehicle_safe}\n"
                f"🔗 Encrypted Link: {encrypted_data.get('link')}"
            ),
            # Supervisor/group info (using group name as identifier)
            "supervisor_name": selected_group.get("group_name", ""),
        }
        monday_result = monday.create_item(monday_lead_data, username)
        
        if monday_result:
            # Update lead with Monday.com item ID and dates from Monday
            db.update_lead(lead["id"], {
                "monday_item_id": monday_result["item_id"],
                "issue_date": monday_result["issue_date"].isoformat(),
                "expiration_date": monday_result["expiration_date"].isoformat()
            })
        else:
            # Monday.com call failed; still compute local NY dates
            from datetime import datetime, timedelta
            import pytz
            ny_tz = pytz.timezone("America/New_York")
            issue_date = datetime.now(ny_tz)
            expiration_date = issue_date + timedelta(days=30)
            
            db.update_lead(lead["id"], {
                "issue_date": issue_date.isoformat(),
                "expiration_date": expiration_date.isoformat()
            })
            
            monday_result = {
                "issue_date": issue_date,
                "expiration_date": expiration_date
            }
    else:
        # Calculate dates locally if Monday.com is not configured
        from datetime import datetime, timedelta
        import pytz
        ny_tz = pytz.timezone("America/New_York")
        issue_date = datetime.now(ny_tz)
        expiration_date = issue_date + timedelta(days=30)
        
        db.update_lead(lead["id"], {
            "issue_date": issue_date.isoformat(),
            "expiration_date": expiration_date.isoformat()
        })
        
        monday_result = {
            "issue_date": issue_date,
            "expiration_date": expiration_date
        }
    
    # Prepare messages for distribution
    # Full message with all details (for Supervisory) – phone only via encrypted link; include reference ID
    full_message = (
        f"📋 Reference ID: `{reference_id}`\n"
        f"👤 User: @{username}\n"
        f"🚗 Vehicle: {vehicle_safe}\n"
        f"📍 Delivery: {delivery_safe}\n"
        f"📞 Phone (one-time link): {encrypted_data.get('link')}\n"
        f"💰 Price: {price}\n"
        f"📅 Issue Date: {monday_result['issue_date'].strftime('%Y-%m-%d %H:%M:%S %Z') if monday_result else 'N/A'}\n"
        f"⏰ Expires: {monday_result['expiration_date'].strftime('%Y-%m-%d %H:%M:%S %Z') if monday_result else 'N/A'}"
    )

    # Group message – no raw phone; vehicle content sanitized; include reference ID for linking
    group_message = (
        f"🏷NEW CLIENT❗️\n\n"
        f"📋 Reference ID: `{reference_id}`\n"
        f"🚗 Vehicle: {vehicle_safe}\n"
        f"🔗 Encrypted Link: {encrypted_data.get('link')}\n"
        f"📅 Issue Date: {monday_result['issue_date'].strftime('%Y-%m-%d %H:%M:%S %Z') if monday_result else 'N/A'}\n"
        f"⏰ Expires: {monday_result['expiration_date'].strftime('%Y-%m-%d %H:%M:%S %Z') if monday_result else 'N/A'}"
    )
    
    # Driver assignment message (sent to selected drivers with accept/decline)
    # NOTE: Phone and price are only revealed after driver accepts.
    driver_request_message = (
        f"👋Hi! New client 💸 available📈❗️\n\n"
        f"📍 Delivery (City, State, Zip): {phase1_data.get('delivery_city_state_zip', '')}\n"
        f"📋 Reference ID: `{reference_id}`\n"
        f" Delivery Time 🏷️: {extra_safe}\n"
        f"Please have Car, Driver License, and Laser Printer Ready✅")
    
    # Create accept/decline keyboard for driver assignment
    accept_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Accept", callback_data=f"accept_lead_{lead['id']}"),
            InlineKeyboardButton("❌ Decline", callback_data=f"decline_lead_{lead['id']}")
        ]
    ])
    
    # Send to selected drivers
    assigned_count = 0
    for driver in selected_drivers:
        driver_telegram_id_raw = driver.get('driver_telegram_id')
        if driver_telegram_id_raw:
            try:
                driver_chat_id = int(str(driver_telegram_id_raw).strip())
            except (ValueError, TypeError):
                driver_chat_id = driver_telegram_id_raw
            try:
                db.create_lead_assignment(lead['id'], driver['id'], group_id)
                await context.bot.send_message(
                    chat_id=driver_chat_id,
                    text=driver_request_message,
                    parse_mode="Markdown",
                    reply_markup=accept_keyboard
                )
                assigned_count += 1
                # Strike message: if driver has 1–2 pending receipts, remind them
                pending = db.get_driver_pending_receipts(driver['id'])
                if 1 <= len(pending) <= 2:
                    ref_buttons = [
                        [InlineKeyboardButton(f"📋 {p['reference_id']}", callback_data=f"receipt_for_{p['reference_id']}")]
                        for p in pending
                    ]
                    await context.bot.send_message(
                        chat_id=driver_chat_id,
                        text=(
                            f"⚠️ You have not submitted receipt for **{len(pending)}** lead(s):\n\n"
                            + "\n".join(f"• Ref `{p['reference_id']}`" for p in pending) +
                            "\n\nTap below to view details and upload:"
                        ),
                        parse_mode="Markdown",
                        reply_markup=InlineKeyboardMarkup(ref_buttons),
                    )
            except Exception as e:
                logger.error(f"Error sending to driver {driver.get('driver_name')} (chat_id={driver_chat_id}): {e!r}")
    
    logger.info(f"Sent lead request to {assigned_count} drivers")
    
    # Send to Group (detailed message without user, phone, and price)
    group_telegram_id_raw = selected_group.get('group_telegram_id')
    group_name = selected_group.get('group_name', 'N/A')
    if not group_telegram_id_raw:
        logger.warning(
            f"No group_telegram_id for group '{group_name}' (id={selected_group.get('id')}). "
            "Lead not sent to group. Check the group record in admin."
        )
    else:
        # Telegram expects numeric chat_id as int (e.g. -1001234567890)
        try:
            group_chat_id = int(str(group_telegram_id_raw).strip())
        except (ValueError, TypeError):
            group_chat_id = group_telegram_id_raw
        try:
            logger.info(f"Sending lead to group '{group_name}' (chat_id={group_chat_id})")
            # No parse_mode so user content (vehicle_details, etc.) can't break Markdown
            await context.bot.send_message(chat_id=group_chat_id, text=group_message)
            logger.info(f"Lead sent to group '{group_name}' successfully")
        except Exception as e:
            logger.error(
                f"Error sending to group '{group_name}' (chat_id={group_chat_id}): {e!r}. "
                "Ensure the bot is added to the group and has permission to post."
            )
    
    # Supervisory message with reference ID (use group's supervisory ID)
    supervisory_telegram_id = selected_group.get('supervisory_telegram_id')
    supervisory_message = (
        f"{full_message}\n\n"
        f"👥 Group: {selected_group.get('group_name', 'N/A')}"
    )
    
    # Create inline keyboard for supervisory (same receipt button)
    supervisory_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Driver Receipt", callback_data="driver_receipt")]
    ])
    
    # Send to Supervisory (full log with phone and price)
    if supervisory_telegram_id:
        try:
            sup_chat_id = int(str(supervisory_telegram_id).strip())
        except (ValueError, TypeError):
            sup_chat_id = supervisory_telegram_id
        try:
            await context.bot.send_message(
                chat_id=sup_chat_id,
                text=supervisory_message,
                parse_mode="Markdown",
                reply_markup=supervisory_keyboard
            )
        except Exception as e:
            logger.error(f"Error sending to supervisory (chat_id={sup_chat_id}): {e!r}")
    
    # Forward attached files to group and supervisory
    attached_files = phase1_data.get("attached_files") or []
    try:
        _group_cid = int(str(group_telegram_id_raw).strip()) if group_telegram_id_raw else None
    except (ValueError, TypeError):
        _group_cid = None
    try:
        _sup_cid = int(str(supervisory_telegram_id).strip()) if supervisory_telegram_id else None
    except (ValueError, TypeError):
        _sup_cid = None
    for f in attached_files:
        ftype = f.get("type")
        fid = f.get("file_id")
        if not fid:
            continue
        try:
            if ftype == "photo" and _group_cid:
                await context.bot.send_photo(chat_id=_group_cid, photo=fid)
            elif ftype == "document" and _group_cid:
                await context.bot.send_document(chat_id=_group_cid, document=fid)
            if ftype == "photo" and _sup_cid:
                await context.bot.send_photo(chat_id=_sup_cid, photo=fid)
            elif ftype == "document" and _sup_cid:
                await context.bot.send_document(chat_id=_sup_cid, document=fid)
        except Exception as e:
            logger.warning("Could not forward attached file to group/supervisory: %s", e)
    
    driver_names = ", ".join(d.get("driver_name", "?") for d in selected_drivers)
    contact_sources = db.get_contact_info_sources()
    
    if contact_sources:
        db.set_user_state(
            user_id,
            "select_contact_source",
            {
                "lead_id": lead["id"],
                "reference_id": reference_id,
                "driver_names": driver_names,
                "group_name": selected_group.get("group_name", "N/A"),
                "username": username,
            },
        )
        buttons = [
            [InlineKeyboardButton(s.get("label", str(s["id"])), callback_data=f"contact_source_{s['id']}")]
            for s in contact_sources
        ]
        await query.message.reply_text(
            "📋 **Select the Contact info source for this client:**",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(buttons),
        )
        return STATE_SELECT_CONTACT_SOURCE
    
    await _finish_lead_send(
        context, query.message, user_id, username, lead["id"], reference_id,
        driver_names, selected_group.get("group_name", "N/A"), contact_source_label=None,
    )
    return ConversationHandler.END


async def _finish_lead_send(
    context: ContextTypes.DEFAULT_TYPE,
    message,
    user_id: int,
    username: str,
    lead_id: str,
    reference_id: str,
    driver_names: str,
    group_name: str,
    contact_source_label: Optional[str] = None,
) -> None:
    """Update lead contact source (if any), sync Monday, notify ST, record usage, send success message."""
    lead = db.get_lead_by_id(lead_id)
    if contact_source_label and lead:
        db.update_lead(lead_id, {"contact_info_source": contact_source_label})
        monday_item_id = lead.get("monday_item_id")
        if monday and monday_item_id:
            try:
                monday.update_item_contact_source(int(monday_item_id), contact_source_label)
            except Exception as e:
                logger.error(f"Error updating Monday contact source: {e}")
    st_telegram_id = (db.get_setting("st_telegram_id") or "").strip()
    if st_telegram_id:
        try:
            st_chat_id = int(st_telegram_id.strip())
            await context.bot.send_message(
                chat_id=st_chat_id,
                text=f"📬 New lead sent\n\nReference: {reference_id}\nGroup: {group_name}\nDriver(s): {driver_names}\nBy: @{username}",
            )
        except Exception as e:
            logger.error(f"Error sending to ST (chat_id={st_telegram_id}): {e}")
    db.record_bot_usage(user_id, username or "Unknown", lead_id, group_name, driver_names)
    success_text = (
        f"✅ **Lead sent successfully**\n\n"
        f"• Sent to driver(s): **{driver_names}**\n"
        f"• Group: {group_name}\n"
        f"• Reference ID: `{reference_id}`\n\n"
        "Use /start to create another lead."
    )
    await message.reply_text(success_text, parse_mode="Markdown")
    # CORE: motivation after client submission (Pro Mode)
    try:
        motivation_text = motivation.core_after_submission()
        await message.reply_text(motivation_text, parse_mode="Markdown")
    except Exception as e:
        logger.warning("Could not send motivation after lead: %s", e)
    db.clear_user_state(user_id)


async def handle_contact_source_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle contact info source selection after lead was sent to drivers."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    username = query.from_user.username or "Unknown"
    raw = query.data.replace("contact_source_", "")
    source_id = raw.strip()
    state = db.get_user_state(user_id)
    if not state or state.get("state") != "select_contact_source":
        await query.message.reply_text("❌ Session expired. Use /start to begin again.")
        db.clear_user_state(user_id)
        return ConversationHandler.END
    data = state.get("data") or {}
    lead_id = data.get("lead_id")
    reference_id = data.get("reference_id", "")
    driver_names = data.get("driver_names", "")
    group_name = data.get("group_name", "N/A")
    source = db.get_contact_info_source_by_id(source_id)
    label = source.get("label", "") if source else ""
    await _finish_lead_send(
        context, query.message, user_id, username, lead_id, reference_id,
        driver_names, group_name, contact_source_label=label or None,
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the conversation."""
    user_id = update.effective_user.id
    db.clear_user_state(user_id)
    
    await update.message.reply_text("❌ Operation cancelled. Use /start to begin again.")
    return ConversationHandler.END


async def _handle_resend_to_drivers(
    update: Update, context: ContextTypes.DEFAULT_TYPE,
    lead_data: dict, callback_data: str, user_id: int,
) -> int:
    """Resend lead to newly selected drivers after timeout."""
    lead_id = lead_data.get("lead_id")
    reference_id = lead_data.get("reference_id", "N/A")
    group_id = lead_data.get("group_id")
    selected_group = lead_data.get("selected_group")
    lead = db.get_lead_by_id(lead_id)
    if not lead:
        await update.callback_query.message.reply_text("❌ Lead not found.")
        db.clear_user_state(user_id)
        return ConversationHandler.END
    all_drivers = db.get_all_drivers()
    active_drivers = [d for d in all_drivers if d.get("is_active", True)]
    if callback_data == "select_driver_all":
        selected_drivers = active_drivers
    else:
        driver_id = callback_data.replace("select_driver_", "")
        selected_drivers = [d for d in active_drivers if d["id"] == driver_id]
        if not selected_drivers:
            await update.callback_query.message.reply_text("❌ Driver not found.")
            return STATE_SELECT_DRIVER
    extra_safe = _sanitize_phones_for_send(lead.get("extra_info") or "")
    driver_request_message = (
        f"👋Hi! New client 💸 available📈❗️\n\n"
        f"📍 Delivery: {lead.get('delivery_details', '')}\n"
        f"📋 Reference ID: `{reference_id}`\n"
        f" Delivery Time 🏷️: {extra_safe}\n"
        f"Please have Car, Driver License, and Laser Printer Ready✅"
    )
    accept_keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Accept", callback_data=f"accept_lead_{lead_id}"),
            InlineKeyboardButton("❌ Decline", callback_data=f"decline_lead_{lead_id}"),
        ]
    ])
    assigned_count = 0
    for driver in selected_drivers:
        tid = driver.get("driver_telegram_id")
        if not tid:
            continue
        try:
            cid = int(str(tid).strip())
        except (ValueError, TypeError):
            cid = tid
        try:
            db.create_lead_assignment(lead_id, driver["id"], group_id)
            await context.bot.send_message(
                chat_id=cid,
                text=driver_request_message,
                parse_mode="Markdown",
                reply_markup=accept_keyboard,
            )
            assigned_count += 1
            pending = db.get_driver_pending_receipts(driver["id"])
            if 1 <= len(pending) <= 2:
                ref_buttons = [
                    [InlineKeyboardButton(f"📋 {p['reference_id']}", callback_data=f"receipt_for_{p['reference_id']}")]
                    for p in pending
                ]
                await context.bot.send_message(
                    chat_id=cid,
                    text=f"⚠️ You have not submitted receipt for **{len(pending)}** lead(s). Tap to view and upload:",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(ref_buttons),
                )
        except Exception as e:
            logger.error("Resend to driver %s: %s", driver.get("driver_name"), e)
    driver_names = ", ".join(d.get("driver_name", "?") for d in selected_drivers)
    group_telegram_id = selected_group.get("group_telegram_id") if selected_group else None
    if group_telegram_id and assigned_count > 0:
        try:
            gcid = int(str(group_telegram_id).strip())
            await context.bot.send_message(
                chat_id=gcid,
                text=f"🔄 Reference ID `{reference_id}`: Reassigned to driver(s) **{driver_names}**",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("Group reassign notify: %s", e)
    await update.callback_query.message.reply_text(
        f"✅ **Lead resent successfully**\n\n"
        f"Reference ID: `{reference_id}`\n"
        f"Sent to driver(s): **{driver_names}**\n\n"
        "Use /start to create another lead.",
        parse_mode="Markdown",
    )
    db.clear_user_state(user_id)
    return ConversationHandler.END


# Driver assignment handlers
async def handle_resend_driver(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle 'Pick new driver' after timeout – show driver picker for resend."""
    query = update.callback_query
    await query.answer()
    lead_id = query.data.replace("resend_driver_", "").strip()
    user_id = query.from_user.id
    lead = db.get_lead_by_id(lead_id)
    if not lead:
        await query.message.reply_text("❌ Lead not found. Use /start to create a new lead.")
        return
    group_id = lead.get("group_id")
    if not group_id:
        await query.message.reply_text("❌ Lead has no group. Use /start to create a new lead.")
        return
    selected_group = db.get_group_by_id(group_id)
    if not selected_group:
        await query.message.reply_text("❌ Group not found. Use /start to create a new lead.")
        return
    reference_id = lead.get("reference_id") or "N/A"
    resend_data = {
        "lead_id": lead_id,
        "reference_id": reference_id,
        "group_id": group_id,
        "selected_group": selected_group,
        "resend": True,
    }
    db.set_user_state(user_id, "select_driver", resend_data)
    drivers = db.get_all_drivers()
    active_drivers = [d for d in drivers if d.get("is_active", True)]
    if not active_drivers:
        await query.message.reply_text("❌ No active drivers found. Please contact admin.")
        return
    driver_keyboard = _build_driver_keyboard(drivers, exclude_suspended=True, include_all=True)
    driver_list = "\n".join([f"• {d.get('driver_name', 'Unknown')}" for d in drivers])
    await query.message.reply_text(
        f"🔄 **Pick new driver**\n\n"
        f"Reference ID: `{reference_id}`\n\n"
        f"Select which driver(s) to notify:",
        parse_mode="Markdown",
        reply_markup=driver_keyboard,
    )
    return STATE_SELECT_DRIVER


async def handle_accept_lead(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle driver accepting a lead."""
    query = update.callback_query
    await query.answer()
    
    # Extract lead_id from callback_data (format: "accept_lead_{lead_id}")
    lead_id = query.data.replace("accept_lead_", "")
    
    # Get driver info
    driver_telegram_id = str(query.from_user.id)
    
    # Find driver by telegram ID
    drivers = db.get_all_drivers()
    driver = next((d for d in drivers if d.get('driver_telegram_id') == driver_telegram_id), None)
    
    if not driver:
        await query.message.reply_text("❌ Error: Driver not found in system.")
        return
    
    # Get lead details first to check if already accepted
    lead = db.get_lead_by_id(lead_id)
    if not lead:
        await query.message.edit_text("❌ Error: Lead not found.")
        return
    
    # Check if lead is already accepted
    assignment_status = db.get_lead_assignment_status(lead_id)
    if assignment_status and assignment_status.get('status') == 'accepted':
        # Another driver already accepted this lead
        await query.message.edit_text(
            "❌ Request Already Taken\n\n"
            "1. Turn on❗telegram notifications🔔\n"
            "2. Check ✅here ⏱️hourly\n"
            "3. Go the extra🛣️mile, post ads instead of doing nothing waiting ask us how.\n\n"
            "-Thank you 🙏\n"
            "🏁Automated🏎️Automotive",
            parse_mode="Markdown"
        )
        return
    
    # Try to accept the lead assignment
    success = db.accept_lead_assignment(lead_id, driver['id'])
    
    if success:
        # Get group info for forwarding
        group_id = lead.get('group_id')
        group = db.get_group_by_id(group_id) if group_id else None
        
        # Update Monday.com driver column if possible
        monday_item_id = lead.get('monday_item_id')
        if monday and monday_item_id:
            try:
                monday.update_item_driver(monday_item_id, driver.get('driver_name', ''))
            except Exception as e:
                logger.error(f"Error updating Monday.com driver column: {e}")
        
        # Send confirmation to driver – phone only via one-time link; sanitize user content
        delivery_safe = _sanitize_phones_for_send(lead.get('delivery_details') or '')
        extra_safe = _sanitize_phones_for_send(lead.get('extra_info') or '')
        confirmation_message = (
            "✅ **Lead Accepted!**\n\n"
            f"📍 Delivery Address: {delivery_safe or 'N/A'}\n"
            f"📝 Extra info: {extra_safe}\n"
            f"📞 Phone (one-time link): {lead.get('encrypted_link', 'N/A')}\n"
            f"💰 Price: {lead.get('price', 'N/A')}\n"
            f"📋 Reference ID: `{lead.get('reference_id', 'N/A')}`\n\n"
            "Security 🚨 client must pay dealership directly\n"
            "PLEASE HAVE CLIENT PAY US THE 💵MONEY DIRECTLY\n"
            "WE ACCEPT ALL ELECTRONIC PAYMENTS:\n"
            "Cashapp: $RoyalSpending3\n"
            "Venmo: @PrivateDealership\n"
            "Zelle: OrganizeDataOnline@gmail.com\n"
            "PayPal: privatedealership@gmail.com\n\n"
            "❗️Important Message:\n"
            "Please be fast, professional, polite, the client is always right. Double check all info.\n"
            "📞Call client now to confirm 💲PRICE, ⏱️TIME, & 📍LOCATION.\n"
            "Fasten your seatbelt, both hands on the wheel. And most importantly, upload receipt 🧾 today ✅\n"
            "🙏Thank you & 🚘Drive Safe !"
        )
        
        # Add receipt submission button
        receipt_keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("📸 Driver Receipt", callback_data="driver_receipt")]
        ])
        
        await query.message.edit_text(
            "✅ **You accepted this lead!**",
            parse_mode="Markdown"
        )
        await query.message.reply_text(
            confirmation_message,
            parse_mode="Markdown",
            reply_markup=receipt_keyboard
        )
        # Strike message: remind about pending receipts
        pending = db.get_driver_pending_receipts(driver["id"])
        if pending:
            ref_buttons = [
                [InlineKeyboardButton(f"📋 {p['reference_id']}", callback_data=f"receipt_for_{p['reference_id']}")]
                for p in pending[:10]
            ]
            if len(pending) >= SUSPENSION_THRESHOLD:
                txt = (
                    f"⛔ **Temporary suspension**\n\n"
                    f"You owe **{len(pending)}** receipt(s). Upload all to resume receiving leads:"
                )
            else:
                txt = f"⚠️ You have not submitted receipt for **{len(pending)}** lead(s). Tap to view and upload:"
            await query.message.reply_text(
                txt,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(ref_buttons),
            )
        # Forward acceptance message to group
        if group:
            group_telegram_id = group.get('group_telegram_id')
            if group_telegram_id:
                try:
                    extra_safe = _sanitize_phones_for_send(lead.get('extra_info') or '')
                    acceptance_message = (
                        "✅ **Lead Accepted**\n\n"
                        f"🚗 Driver: {driver.get('driver_name', 'Unknown')}\n"
                        f"📝 Extra info: {extra_safe}\n"
                        f"📋 Reference ID: `{lead.get('reference_id', 'N/A')}`"
                    )
                    await context.bot.send_message(
                        chat_id=group_telegram_id,
                        text=acceptance_message,
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Error forwarding acceptance to group: {e}")
        # Notify initiator and global supervisor
        await _notify_initiator_and_supervisor(
            context,
            lead,
            f"✅ **Driver accepted**\n\nReference: `{lead.get('reference_id', 'N/A')}`\nDriver: **{driver.get('driver_name', 'Driver')}**",
        )
    else:
        # Fallback error
        await query.message.edit_text(
            "❌ **Error accepting lead. Please try again.**",
            parse_mode="Markdown"
        )


async def handle_decline_lead(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle driver declining a lead."""
    query = update.callback_query
    await query.answer()
    
    # Extract lead_id from callback_data
    lead_id = query.data.replace("decline_lead_", "")
    
    # Get driver info
    driver_telegram_id = str(query.from_user.id)
    
    # Find driver by telegram ID
    drivers = db.get_all_drivers()
    driver = next((d for d in drivers if d.get('driver_telegram_id') == driver_telegram_id), None)
    
    if not driver:
        await query.message.reply_text("❌ Error: Driver not found in system.")
        return
    
    # Decline the assignment
    db.decline_lead_assignment(lead_id, driver['id'])

    # Notify initiator and global supervisor
    lead = db.get_lead_by_id(lead_id)
    if lead:
        await _notify_initiator_and_supervisor(
            context,
            lead,
            f"❌ **Driver declined**\n\nReference: `{lead.get('reference_id', 'N/A')}`\nDriver: **{driver.get('driver_name', 'Driver')}**",
        )
    
    await query.message.edit_text(
        "❌ **Lead Declined**\n\n"
        "You have declined this lead.",
        parse_mode="Markdown"
    )


async def handle_accept_group_offer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a group member accepting a broadcast lead offer."""
    query = update.callback_query
    await query.answer()
    raw = query.data.replace("accept_group_", "")
    try:
        lead_id, group_id = raw.split("_", 1)
    except ValueError:
        await query.message.reply_text("❌ Invalid request.")
        return

    lead = db.get_lead_by_id(lead_id)
    group = db.get_group_by_id(group_id)
    if not lead or not group or not group.get("is_active", True):
        try:
            await query.message.edit_text("❌ Offer not found or expired.")
        except Exception:
            pass
        return

    accepted = db.accept_group_lead_offer(lead_id, group_id, accepted_by_telegram_id=str(query.from_user.id))
    if not accepted:
        # Someone else already accepted.
        accepted_row = db.get_accepted_group_for_lead(lead_id)
        accepted_group = db.get_group_by_id(accepted_row.get("group_id")) if accepted_row else None
        gname = accepted_group.get("group_name") if accepted_group else "another group"
        try:
            await query.message.edit_text(f"❌ **Taken**\n\nThis lead was accepted by **{gname}**.", parse_mode="Markdown")
        except Exception:
            pass
        return

    # Set lead.group_id to winning group
    db.update_lead(lead_id, {"group_id": group_id})

    reference_id = lead.get("reference_id", "N/A")
    winner_name = group.get("group_name", "Group")

    # Update all group offer messages to reflect taken/accepted
    offers = db.get_group_lead_offers(lead_id)
    for o in offers:
        ocid = _parse_chat_id(o.get("group_chat_id"))
        mid = o.get("group_message_id")
        ogid = o.get("group_id")
        if not ocid or not mid:
            continue
        try:
            if ogid == group_id:
                await context.bot.edit_message_text(
                    chat_id=ocid,
                    message_id=int(mid),
                    text=f"✅ **Accepted by {winner_name}**\n\nReference ID: `{reference_id}`",
                    parse_mode="Markdown",
                )
            else:
                await context.bot.edit_message_text(
                    chat_id=ocid,
                    message_id=int(mid),
                    text=f"❌ **Taken by another group**\n\nAccepted by: **{winner_name}**\nReference ID: `{reference_id}`",
                    parse_mode="Markdown",
                )
        except Exception as e:
            logger.warning("Could not edit group offer message: %s", e)

    # Notify initiator + global supervisor
    await _notify_initiator_and_supervisor(
        context,
        lead,
        f"👥 **Group accepted**\n\nReference: `{reference_id}`\nGroup: **{winner_name}**\nAccepted by: @{query.from_user.username or query.from_user.first_name}",
    )

    # Send driver requests to drivers for this group (keeps existing driver-accept flow)
    count, driver_names = await _send_driver_requests_for_group(context, lead, group)
    if count > 0:
        await context.bot.send_message(
            chat_id=_parse_chat_id(group.get("group_telegram_id")),
            text=f"🚗 Sent to driver(s): **{driver_names}**\nReference: `{reference_id}`",
            parse_mode="Markdown",
        )
    else:
        await context.bot.send_message(
            chat_id=_parse_chat_id(group.get("group_telegram_id")),
            text=f"⚠️ No eligible drivers to notify for this group (inactive/suspended/missing Telegram IDs).\nReference: `{reference_id}`",
            parse_mode="Markdown",
        )


async def handle_decline_group_offer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a group member declining a broadcast lead offer (for that group only)."""
    query = update.callback_query
    await query.answer()
    raw = query.data.replace("decline_group_", "")
    try:
        lead_id, group_id = raw.split("_", 1)
    except ValueError:
        await query.message.reply_text("❌ Invalid request.")
        return
    db.decline_group_lead_offer(lead_id, group_id)
    try:
        await query.message.edit_text("❌ **Declined**", parse_mode="Markdown")
    except Exception:
        pass


# Receipt submission handlers
async def handle_receipt_for_ref_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """When driver clicks ref in strike message – show lead details and Upload button."""
    query = update.callback_query
    await query.answer()
    ref = query.data.replace("receipt_for_", "").strip()
    lead = db.get_lead_by_reference_id(ref)
    if not lead:
        await query.message.reply_text(f"❌ Reference ID `{ref}` not found.")
        return ConversationHandler.END
    context.user_data["receipt_lead_id"] = lead["id"]
    context.user_data["receipt_reference_id"] = ref
    context.user_data["receipt_monday_item_id"] = lead.get("monday_item_id")
    delivery_safe = _sanitize_phones_for_send(lead.get("delivery_details") or "")
    msg = (
        f"📋 **Reference ID:** `{ref}`\n\n"
        f"📍 Delivery: {delivery_safe or 'N/A'}\n"
        f"🚗 Vehicle: {lead.get('vehicle_details', 'N/A')[:300]}\n\n"
        "Upload receipt for this lead:"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Upload Receipt", callback_data="confirm_receipt")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_receipt")],
    ])
    db.set_user_state(query.from_user.id, "waiting_receipt_confirm", context.user_data)
    await query.message.reply_text(msg, parse_mode="Markdown", reply_markup=kb)
    return STATE_WAITING_RECEIPT_CONFIRM


async def handle_driver_receipt_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle driver receipt button callback."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Set state to waiting for reference ID
    db.set_user_state(user_id, "waiting_reference_id", {})
    
    await query.message.reply_text(
        "📋 **Driver Receipt Submission**\n\n"
        "Please enter the Reference ID for the lead you want to submit a receipt for."
    )
    
    return STATE_WAITING_REFERENCE_ID


async def handle_reference_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle reference ID input."""
    user_id = update.effective_user.id
    reference_id = update.message.text.strip().upper()
    
    # Get lead by reference ID
    lead = db.get_lead_by_reference_id(reference_id)
    
    if not lead:
        await update.message.reply_text(
            "❌ Reference ID not found. Please check and try again.\n"
            "Or type /cancel to cancel."
        )
        return STATE_WAITING_REFERENCE_ID
    
    # Store lead ID in context for later use
    context.user_data['receipt_lead_id'] = lead['id']
    context.user_data['receipt_reference_id'] = reference_id
    context.user_data['receipt_monday_item_id'] = lead.get('monday_item_id')
    
    # Show lead details for confirmation – phone only via one-time link
    delivery_safe = _sanitize_phones_for_send(lead.get('delivery_details') or '')
    confirmation_message = (
        f"✅ **Lead Found**\n\n"
        f"📍 Delivery Address: {delivery_safe or 'N/A'}\n"
        f"📞 Phone (one-time link): {lead.get('encrypted_link', 'N/A')}\n"
        f"📋 Reference ID: `{reference_id}`\n\n"
        f"Please confirm this is the correct lead, then upload the receipt image."
    )
    
    # Create confirmation keyboard
    confirm_keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Confirm & Upload Receipt", callback_data="confirm_receipt")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_receipt")]
    ])
    
    await update.message.reply_text(
        confirmation_message,
        parse_mode="Markdown",
        reply_markup=confirm_keyboard
    )
    
    return STATE_WAITING_RECEIPT_CONFIRM


async def handle_receipt_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle receipt confirmation callback."""
    query = update.callback_query
    
    if query.data == "cancel_receipt":
        await query.answer("Cancelled")
        user_id = query.from_user.id
        db.clear_user_state(user_id)
        await query.message.reply_text("❌ Receipt submission cancelled.")
        return ConversationHandler.END
    
    await query.answer()
    
    # Set state to waiting for image
    user_id = query.from_user.id
    db.set_user_state(user_id, "waiting_receipt_image", context.user_data)
    
    await query.message.reply_text(
        "📸 **Upload Receipt**\n\n"
        "Please upload the receipt image now🧾.\n\n"
    )
    
    return STATE_WAITING_RECEIPT_IMAGE


async def handle_receipt_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle receipt image upload."""
    user_id = update.effective_user.id
    
    if not update.message.photo:
        await update.message.reply_text(
            "❌ Please send a photo/image. Upload the receipt image."
        )
        return STATE_WAITING_RECEIPT_IMAGE
    
    # Get the highest resolution photo
    photo = update.message.photo[-1]
    file = await context.bot.get_file(photo.file_id)
    
    # Build a Telegram file URL for storage (Supabase)
    image_url = f"https://api.telegram.org/file/bot{Config.TELEGRAM_BOT_TOKEN}/{file.file_path}"
    
    # Download bytes so we can upload a real file to Monday.com
    import io
    bio = io.BytesIO()
    # For python-telegram-bot v20+, use download_to_memory
    await file.download_to_memory(out=bio)
    image_bytes = bio.getvalue()
    file_name = (file.file_path.split("/")[-1] if file.file_path else "receipt.jpg")
    
    lead_id = context.user_data.get('receipt_lead_id')
    reference_id = context.user_data.get('receipt_reference_id')
    monday_item_id = context.user_data.get('receipt_monday_item_id')
    
    if not lead_id:
        await update.message.reply_text("❌ Error: Lead information not found. Please start over.")
        db.clear_user_state(user_id)
        return ConversationHandler.END
    
    # Get lead and driver info
    lead = db.get_lead_by_id(lead_id)
    if not lead:
        await update.message.reply_text("❌ Error: Lead not found.")
        db.clear_user_state(user_id)
        return ConversationHandler.END
    
    # Get the driver who accepted this lead
    assignment_status = db.get_lead_assignment_status(lead_id)
    driver_name = "Driver"
    if assignment_status:
        driver_id = assignment_status.get('driver_id')
        driver = next((d for d in db.get_all_drivers() if d['id'] == driver_id), None)
        if driver:
            driver_name = driver.get('driver_name', 'Driver')
    
    # Update lead with receipt image (stored in Supabase / DB)
    success = db.update_lead_receipt(lead_id, image_url)
    
    if success and monday and monday_item_id:
        # First, try to upload the actual image file to the Monday files4 column.
        upload_ok = False
        try:
            upload_ok = monday.update_item_receipt(monday_item_id, file_name, image_bytes)
        except Exception as e:
            logger.error(f"Error uploading receipt to Monday.com: {e}")
        
        # If the direct file upload failed, fall back to storing a public URL
        # (e.g. Supabase public URL or Telegram file URL) into a text column
        # so the team still has access to the receipt.
        if not upload_ok:
            try:
                monday.update_item_receipt_link(monday_item_id, image_url)
            except Exception as e:
                logger.error(f"Error updating Monday.com with receipt URL fallback: {e}")
        
        # Always attempt to update status after trying to attach the receipt
        try:
            monday.update_item_status(monday_item_id, "PAID RECEIPT")
        except Exception as e:
            logger.error(f"Error updating Monday.com status: {e}")
    
    if success:
        await update.message.reply_text(
            "✅ **Receipt Submitted Successfully!**\n\n"
            f"Receipt for Reference ID `{reference_id}` has been uploaded and processed.\n"
            "Status updated to 'PAID RECEIPT' in Monday.com."
        )
        
        # Notify ST Telegram ID and Supervisory Telegram ID (group that received this lead)
        group_id = lead.get("group_id")
        group_name = "—"
        if group_id:
            group = db.get_group_by_id(group_id)
            if group:
                group_name = group.get("group_name") or group_name
                supervisory_telegram_id = (group.get("supervisory_telegram_id") or "").strip()
                if supervisory_telegram_id:
                    try:
                        sup_chat_id = int(supervisory_telegram_id.strip())
                        await context.bot.send_message(
                            chat_id=sup_chat_id,
                            text=(
                                f"🧾 **Receipt submitted**\n\n"
                                f"Driver **{driver_name}** submitted a receipt for Reference ID `{reference_id}`\n"
                                f"Group: **{group_name}**"
                            ),
                            parse_mode="Markdown",
                        )
                    except (ValueError, TypeError) as e:
                        logger.warning("Invalid supervisory_telegram_id for group %s: %s", group_id, e)
                    except Exception as e:
                        logger.warning("Could not send receipt notification to supervisory %s: %s", supervisory_telegram_id, e)
        st_telegram_id = (db.get_setting("st_telegram_id") or "").strip()
        if st_telegram_id:
            try:
                st_chat_id = int(st_telegram_id.strip())
                await context.bot.send_message(
                    chat_id=st_chat_id,
                    text=(
                        f"🧾 **Receipt submitted**\n\n"
                        f"Driver **{driver_name}** submitted a receipt for Reference ID `{reference_id}`\n"
                        f"Group: **{group_name}**"
                    ),
                    parse_mode="Markdown",
                )
            except (ValueError, TypeError) as e:
                logger.warning("Invalid st_telegram_id: %s", e)
            except Exception as e:
                logger.warning("Could not send receipt notification to ST %s: %s", st_telegram_id, e)
        
        # Send completion message via @krabsenderbot to driver_name
        try:
            completion_message = (
                f"✅ **Delivery Completed**\n\n"
                f"Receipt has been submitted for Reference ID: `{reference_id}`\n"
                f"Thank you for completing this delivery!"
            )
            
            # Use @krabsenderbot to send message to driver_name
            # Format: @krabsenderbot send to driver_name
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"@krabsenderbot send to {driver_name}\n\n{completion_message}",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Error sending completion message via @krabsenderbot: {e}")
    else:
        await update.message.reply_text(
            "❌ Error uploading receipt. Please try again or contact support."
        )
    
    # Clear user state
    db.clear_user_state(user_id)
    
    return ConversationHandler.END


def main():
    """Main function to start the bot."""
    logger.info("Bot starting...")
    sys.stdout.flush()
    sys.stderr.flush()
    # Validate configuration
    try:
        Config.validate()
    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        logger.error("\nPlease check your .env file and ensure all required variables have non-empty values.")
        logger.error("Missing variables should be set in the .env file in the project root directory.")
        return
    
    # Create application
    application = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
    
    # Always delete webhook before polling (avoids 409 when webhook was set elsewhere)
    import requests
    import time
    bot_token = Config.TELEGRAM_BOT_TOKEN
    delete_url = f"https://api.telegram.org/bot{bot_token}/deleteWebhook"
    try:
        logger.info("Clearing webhook...")
        delete_response = requests.post(delete_url, json={"drop_pending_updates": True}, timeout=5)
        if delete_response.status_code == 200:
            data = delete_response.json()
            if data.get("ok"):
                logger.info("Webhook cleared (or was already clear) - safe to poll.")
            else:
                logger.warning(f"deleteWebhook response: {data}")
        else:
            logger.warning(f"deleteWebhook HTTP {delete_response.status_code}: {delete_response.text}")
    except Exception as e:
        logger.warning(f"Could not clear webhook (continuing anyway): {e}")
    time.sleep(1)
    
    # Add error handler for all errors
    _conflict_logged = False  # Flag to prevent repeated logging
    
    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle errors."""
        nonlocal _conflict_logged
        error = context.error
        
        # Handle Telegram Conflict (multiple bot instances)
        if isinstance(error, Conflict):
            if not _conflict_logged:
                _conflict_logged = True
                logger.error(
                    "\n" + "="*60 + "\n"
                    "TELEGRAM CONFLICT ERROR: Another process is already receiving updates for this bot.\n\n"
                    "On Render: Use only ONE Background Worker running 'python bot.py'. "
                    "Do not run bot.py on a Web service (it can spawn multiple instances).\n"
                    "Also: clear any webhook (e.g. run check_webhook.py once), then redeploy.\n"
                    "="*60
                )
                # Exit gracefully after logging
                logger.info("Exiting due to conflict error...")
                import asyncio
                # Schedule application shutdown
                asyncio.create_task(application.stop())
            # Suppress the error to prevent spam
            return
        
        # Log other errors (only once per error type)
        if isinstance(error, Exception):
            error_type = type(error).__name__
            if not hasattr(error_handler, f'_logged_{error_type}'):
                logger.error(f"Exception while handling an update: {error}", exc_info=error)
                setattr(error_handler, f'_logged_{error_type}', True)
    
    # Add error handler - must be added before handlers
    application.add_error_handler(error_handler)
    
    # Create conversation handler for lead creation
    conv_handler = ConversationHandler(
        entry_points=[
            CommandHandler("start", start),
            CallbackQueryHandler(handle_resend_driver, pattern="^resend_driver_"),
        ],
        states={
            STATE_PHASE1: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phase1),
                MessageHandler(filters.PHOTO, handle_phase1_photo),
            ],
            STATE_MISSING_FIELD: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_missing_field)],
            STATE_ADD_FILES: [CallbackQueryHandler(handle_add_files_callback, pattern="^(add_files_yes|add_files_no)$")],
            STATE_WAITING_FILE: [
                MessageHandler(filters.PHOTO | filters.Document.ALL, handle_file_upload),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_waiting_file_text),
                CallbackQueryHandler(handle_another_file_callback, pattern="^(another_file_yes|another_file_no)$"),
            ],
            STATE_PHASE2: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phase2)],
            STATE_VIN_CHOICE: [CallbackQueryHandler(handle_vin_choice_callback, pattern="^(vin_use|vin_keep|vin_retype)$")],
            STATE_VIN_RETYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_vin_retype)],
            STATE_SELECT_GROUP: [CallbackQueryHandler(handle_group_selection, pattern="^select_group_")],
            STATE_SELECT_DRIVER: [CallbackQueryHandler(handle_driver_selection, pattern="^(select_driver_|driver_suspended_)")],
            STATE_SELECT_CONTACT_SOURCE: [CallbackQueryHandler(handle_contact_source_selection, pattern="^contact_source_")],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )
    
    # Create conversation handler for receipt submission
    receipt_handler = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(handle_driver_receipt_callback, pattern="^driver_receipt$"),
            CallbackQueryHandler(handle_receipt_for_ref_callback, pattern="^receipt_for_"),
        ],
        states={
            STATE_WAITING_REFERENCE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reference_id_input)],
            STATE_WAITING_RECEIPT_CONFIRM: [CallbackQueryHandler(handle_receipt_confirm_callback, pattern="^(confirm_receipt|cancel_receipt)$")],
            STATE_WAITING_RECEIPT_IMAGE: [MessageHandler(filters.PHOTO, handle_receipt_image)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", start)],
    )
    
    # Add handlers
    application.add_handler(conv_handler)
    application.add_handler(receipt_handler)
    
    # Add accept/decline handlers for driver assignments
    application.add_handler(CallbackQueryHandler(handle_accept_lead, pattern="^accept_lead_"))
    application.add_handler(CallbackQueryHandler(handle_decline_lead, pattern="^decline_lead_"))

    # Add accept/decline handlers for group broadcast offers
    application.add_handler(CallbackQueryHandler(handle_accept_group_offer, pattern="^accept_group_"))
    application.add_handler(CallbackQueryHandler(handle_decline_group_offer, pattern="^decline_group_"))
    
    # Driver timeout: every minute, check for leads where no driver accepted within 10 min
    async def check_driver_timeout(context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            overdue = db.get_leads_pending_driver_timeout(minutes=10)
            for item in overdue:
                lead_id = item.get("lead_id")
                user_id = item.get("user_id")
                reference_id = item.get("reference_id", "N/A")
                drivers = item.get("drivers") or []
                driver_names = ", ".join(d.get("driver_name", "?") for d in drivers)
                # Mark FIRST to prevent spam: if mark fails (e.g. migration not run), skip sending
                if not db.mark_driver_timeout_notified(lead_id):
                    logger.error(
                        "Driver timeout: could not mark lead %s as notified (run database/migration_driver_timeout.sql). Skipping send to avoid spam.",
                        lead_id,
                    )
                    continue
                try:
                    user_chat = int(user_id) if isinstance(user_id, (int, str)) else user_id
                except (ValueError, TypeError):
                    user_chat = user_id
                for d in drivers:
                    tid = d.get("driver_telegram_id")
                    if not tid:
                        continue
                    try:
                        cid = int(str(tid).strip())
                        await context.bot.send_message(
                            chat_id=cid,
                            text=f"⏰ **Lead expired.**\n\nReference ID: `{reference_id}`\n\nNo one accepted in time.",
                            parse_mode="Markdown",
                        )
                    except Exception as e:
                        logger.warning("Driver timeout notify to %s: %s", tid, e)
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Pick new driver", callback_data=f"resend_driver_{lead_id}")],
                ])
                try:
                    await context.bot.send_message(
                        chat_id=user_chat,
                        text=(
                            f"⏰ **Lead not accepted**\n\n"
                            f"Driver(s) **{driver_names}** did not accept the lead.\n\n"
                            f"Reference ID: `{reference_id}`\n\n"
                            "Tap below to pick a new driver:"
                        ),
                        parse_mode="Markdown",
                        reply_markup=keyboard,
                    )
                except Exception as e:
                    logger.warning("User timeout notify: %s", e)
                logger.info("Driver timeout notified for lead %s ref %s", lead_id, reference_id)
        except Exception as e:
            logger.error("Driver timeout job failed: %s", e)
    if application.job_queue:
        application.job_queue.run_repeating(check_driver_timeout, interval=60, first=120)
        logger.info("Driver timeout job scheduled (every 60s, first in 120s)")

    # Receipt reminder: every hour, send a reminder to drivers who accepted 24+ hours ago and haven't submitted receipt
    async def send_receipt_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            overdue = db.get_accepted_leads_without_receipt_over_24h()
            for item in overdue:
                ref = item.get("reference_id") or "N/A"
                chat_id = item.get("driver_telegram_id")
                assignment_id = item.get("assignment_id")
                if not chat_id or not assignment_id:
                    continue
                try:
                    chat_id_int = int(str(chat_id).strip())
                except (ValueError, TypeError):
                    chat_id_int = chat_id
                try:
                    await context.bot.send_message(
                        chat_id=chat_id_int,
                        text=f"🧾 **Receipt reminder**\n\nReference ID: `{ref}`\n\nPlease submit your receipt when you can.",
                        parse_mode="Markdown",
                    )
                    db.mark_receipt_reminder_sent(assignment_id)
                    logger.info("Receipt reminder sent to driver for ref %s", ref)
                except Exception as e:
                    logger.warning("Could not send receipt reminder to %s: %s", chat_id, e)
        except Exception as e:
            logger.error("Receipt reminder job failed: %s", e)
    if application.job_queue:
        application.job_queue.run_repeating(send_receipt_reminders, interval=3600, first=60)
        logger.info("Receipt reminder job scheduled (every hour, first in 60s)")

    # Daily motivation (Pro Mode): morning PSYCHOLOGY, evening AGGRESSIVE, no-lead-24h AGGRESSIVE, top performer BONUS
    async def send_morning_motivation(context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user_ids = db.get_lead_sender_telegram_ids()
            text = motivation.morning_psychology()
            for uid in user_ids:
                try:
                    chat_id = int(uid) if isinstance(uid, str) else uid
                    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
                except Exception as e:
                    logger.warning("Morning motivation to %s: %s", uid, e)
        except Exception as e:
            logger.error("Morning motivation job failed: %s", e)

    async def send_evening_motivation(context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            recipients = db.get_motivation_recipients()
            top_count = max((r.get("leads_count_7d") or 0) for r in recipients) if recipients else 0
            top_performer_uid = None
            if top_count > 0:
                for r in recipients:
                    if (r.get("leads_count_7d") or 0) == top_count:
                        top_performer_uid = r.get("user_id")
                        break
            for r in recipients:
                uid = r.get("user_id")
                if not uid:
                    continue
                try:
                    chat_id = int(uid) if isinstance(uid, str) else uid
                    if r.get("no_lead_24h"):
                        text = motivation.no_clients_24h_aggressive()
                    else:
                        text = motivation.evening_aggressive()
                    await context.bot.send_message(chat_id=chat_id, text=text, parse_mode="Markdown")
                    if uid == top_performer_uid and top_count > 0:
                        bonus = motivation.top_performer_bonus()
                        await context.bot.send_message(chat_id=chat_id, text=bonus, parse_mode="Markdown")
                except Exception as e:
                    logger.warning("Evening motivation to %s: %s", uid, e)
        except Exception as e:
            logger.error("Evening motivation job failed: %s", e)

    if application.job_queue:
        eastern = pytz.timezone("America/New_York")
        application.job_queue.run_daily(send_morning_motivation, time=dt_time(hour=8, minute=0, tzinfo=eastern))
        application.job_queue.run_daily(send_evening_motivation, time=dt_time(hour=18, minute=0, tzinfo=eastern))
        logger.info("Daily motivation jobs scheduled (8 AM ET, 6 PM ET)")

    logger.info("Make sure only ONE instance of the bot is running!")
    logger.info("Starting polling - bot is live. Press Ctrl+C to stop.")

    try:
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
    except Conflict as e:
        # This should only happen if conflict occurs during startup
        logger.error(
            "\n" + "="*60 + "\n"
            "TELEGRAM CONFLICT ERROR: Multiple bot instances detected!\n\n"
            "Possible causes:\n"
            "1. Another instance of THIS bot is running\n"
            "2. A webhook is set for this bot token\n"
            "3. A background process is still running\n\n"
            "Solution:\n"
            "1. Run: python stop_bot.py (to find running processes)\n"
            "2. Stop all bot instances (Ctrl+C in all terminals)\n"
            "3. Wait 10 seconds\n"
            "4. Run: python check_webhook.py (to clear webhooks)\n"
            "5. Start only ONE instance: python bot.py\n"
            "="*60
        )
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)


if __name__ == "__main__":
    main()

