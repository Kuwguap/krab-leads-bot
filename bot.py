"""Main Telegram bot application."""
import logging
import sys
import secrets
import string
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
STATE_SELECT_DRIVER = 3  # Waiting for user to select which driver(s) to notify

# Receipt submission states
STATE_WAITING_REFERENCE_ID = 4  # Waiting for reference ID input
STATE_WAITING_RECEIPT_CONFIRM = 5  # Waiting for receipt confirmation
STATE_WAITING_RECEIPT_IMAGE = 6  # Waiting for receipt image upload

# Initialize services
db = Database()
ots = OneTimeSecret()
monday = MondayClient() if Config.is_monday_configured() else None


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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Start the conversation and initialize state."""
    user = update.effective_user
    user_id = user.id
    username = user.username or "Unknown"
    
    # Clear any existing state
    db.clear_user_state(user_id)
    
    # Initialize new state
    db.set_user_state(user_id, "phase1", {})
    
    await update.message.reply_text(
        f"Welcome, @{username}! 👋\n\n"
        "I'll help you create a dispatch lead.\n\n"
        "**Phase 1:** Please send details in this exact structure (one item per line):\n\n"
        "1) Name\n"
        "2) Address\n"
        "3) City, State, ZIP\n"
        "4) Delivery address\n"
        "5) Delivery city, State, ZIP\n"
        "6) VIN\n"
        "7) Car (year, make, model)\n"
        "8) Color\n"
        "9) Insurance company\n"
        "10) Insurance policy number\n"
        "11) Extra info (any other notes)\n\n"
        "Please keep this structure so drivers and supervisors can read it fast."
    )
    
    return STATE_PHASE1


async def handle_phase1(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle Phase 1: Vehicle and delivery details."""
    user_id = update.effective_user.id
    message_text = update.message.text
    
    # Parse and store structured Phase 1 data
    state_data = parse_phase1_structured(message_text)
    db.set_user_state(user_id, "phase1", state_data)
    
    await update.message.reply_text(
        "✅ Phase 1 received!\n\n"
        "**Phase 2:** Please provide phone number and price.\n"
        "Format: Phone number and price (e.g., '+1234567890 $500')"
    )
    
    return STATE_PHASE2


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
    
    # Parse phone and price (simple parsing - you may want to improve this)
    # Expected format: phone number and price
    parts = message_text.split()
    phone_number = None
    price = None
    
    for part in parts:
        if part.startswith("+") or part.replace("-", "").replace("(", "").replace(")", "").isdigit():
            phone_number = part
        elif part.startswith("$"):
            price = part
    
    if not phone_number or not price:
        await update.message.reply_text(
            "❌ Please provide both phone number and price.\n"
            "Format: Phone number and price (e.g., '+1234567890 $500')"
        )
        return STATE_PHASE2
    
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
    
    # If user is an assistant for a group, use that group; else use first active group
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
    
    # Store lead data temporarily in user state (before creating lead)
    # We'll create the lead after driver selection
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
    
    # Get all active drivers (drivers work for all groups)
    drivers = db.get_all_drivers()
    active_drivers = [d for d in drivers if d.get('is_active', True)]
    
    if not active_drivers:
        await update.message.reply_text("❌ Error: No active drivers found. Please contact admin.")
        return ConversationHandler.END
    
    # Create inline keyboard with all drivers
    keyboard_buttons = []
    for driver in drivers:
        keyboard_buttons.append([
            InlineKeyboardButton(
                f"🚗 {driver.get('driver_name', 'Unknown')}",
                callback_data=f"select_driver_{driver['id']}"
            )
        ])
    
    # Add "Send to All" option
    keyboard_buttons.append([
        InlineKeyboardButton("📢 Send to All Drivers", callback_data="select_driver_all")
    ])
    
    driver_keyboard = InlineKeyboardMarkup(keyboard_buttons)
    
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


async def handle_driver_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle driver selection after Phase 2."""
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Get stored lead data
    state = db.get_user_state(user_id)
    if not state or not state.get("data"):
        await query.message.reply_text("❌ Error: Lead data not found. Please start over with /start")
        return ConversationHandler.END
    
    lead_data = state.get("data", {})
    phase1_data = {k: v for k, v in lead_data.items() if k not in ['phone_number', 'price', 'encrypted_data', 'reference_id', 'group_id', 'selected_group']}
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
    
    if callback_data == "select_driver_all":
        # Send to all active drivers
        selected_driver_ids = [d['id'] for d in active_drivers]
        selected_drivers = active_drivers
    else:
        # Send to selected driver
        driver_id = callback_data.replace("select_driver_", "")
        selected_driver_ids = [driver_id]
        selected_drivers = [d for d in active_drivers if d['id'] == driver_id]
        if not selected_drivers:
            await query.message.reply_text("❌ Error: Driver not found.")
            return ConversationHandler.END
    
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
    
    # Create item in Monday.com (if configured)
    # Monday.com only receives: user, raw phone, and price(not limited to this only that will be sent to the moday board)
    monday_result = None
    if monday:
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text="📊 Syncing with Monday.com..."
        )
        # Prepare data for Monday.com
        # Map Phase 1 + routing info into named fields for Monday columns
        monday_lead_data = {
            "name": phase1_data.get("name", ""),
            "phone_number": phone_number,
            "price": price,
            "delivery_address": phase1_data.get("delivery_address", ""),
            "delivery_city_state_zip": phase1_data.get("delivery_city_state_zip", ""),
            # Text for Monday long_text__1: same structure as group message but without dates
            "group_message": (
                "🏷NEW CLIENT❗️\n\n"
                f"🚗 Vehicle: {phase1_data.get('vehicle_details', '')}\n"
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
    # Full message with all details (for Supervisory)
    full_message = (
        f"👤 User: @{username}\n"
        f"🚗 Vehicle: {phase1_data.get('vehicle_details', '')}\n"
        f"📍 Delivery: {phase1_data.get('delivery_details', '')}\n"
        f"📞 Phone: {phone_number}\n"
        f"💰 Price: {price}\n"
        f"🔗 Encrypted Link: {encrypted_data.get('link')}\n"
        f"📅 Issue Date: {monday_result['issue_date'].strftime('%Y-%m-%d %H:%M:%S %Z') if monday_result else 'N/A'}\n"
        f"⏰ Expires: {monday_result['expiration_date'].strftime('%Y-%m-%d %H:%M:%S %Z') if monday_result else 'N/A'}"
    )
    
    # Group message (without user, phone, price, or delivery) - starts with "NEW CLIENT"
    group_message = (
        f"🏷NEW CLIENT❗️\n\n"
        f"🚗 Vehicle: {phase1_data.get('vehicle_details', '')}\n"
        f"🔗 Encrypted Link: {encrypted_data.get('link')}\n"
        f"📅 Issue Date: {monday_result['issue_date'].strftime('%Y-%m-%d %H:%M:%S %Z') if monday_result else 'N/A'}\n"
        f"⏰ Expires: {monday_result['expiration_date'].strftime('%Y-%m-%d %H:%M:%S %Z') if monday_result else 'N/A'}"
    )
    
    # Driver assignment message (sent to selected drivers with accept/decline)
    # NOTE: Phone number is intentionally NOT shown here; it is only revealed after driver accepts.
    driver_request_message = (
        f"👋Hi! New client 💸 available📈❗️\n\n"
        f"📍 Delivery Address: {phase1_data.get('delivery_details', '')}\n"
        f"💰 Price: {price}\n"
        f"📋 Reference ID: `{reference_id}`\n"
        f" Delivery Time 🏷️: {phase1_data.get('extra_info', '')}\n"
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
                # Create assignment record
                db.create_lead_assignment(lead['id'], driver['id'], group_id)
                await context.bot.send_message(
                    chat_id=driver_chat_id,
                    text=driver_request_message,
                    parse_mode="Markdown",
                    reply_markup=accept_keyboard
                )
                assigned_count += 1
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
        f"📋 Reference ID: `{reference_id}`\n"
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
    
    # Clear user state
    db.clear_user_state(user_id)
    
    # Build success message
    success_parts = [
        "✅ Lead processed successfully!\n\n",
        "Your lead has been:\n",
        "• Encrypted and stored\n"
    ]
    
    if monday and monday_result:
        success_parts.append("• Synced to Monday.com\n")
    
    success_parts.append("• Sent to selected driver(s)\n\n")
    success_parts.append("Use /start to create another lead.")
    
    await query.message.reply_text("".join(success_parts))
    
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel the conversation."""
    user_id = update.effective_user.id
    db.clear_user_state(user_id)
    
    await update.message.reply_text("❌ Operation cancelled. Use /start to begin again.")
    return ConversationHandler.END


# Driver assignment handlers
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
        
        # Send confirmation to driver with full details (including extra info)
        confirmation_message = (
            "✅ **Lead Accepted!**\n\n"
            f"📍 Delivery Address: {lead.get('delivery_details', 'N/A')}\n"
            f"📝 Extra info: {lead.get('extra_info', '')}\n"
            f"📞 Phone: {lead.get('phone_number', 'N/A')}\n"
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
            "📞Call client now to confirm time & location.\n"
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
        
        # Forward acceptance message to group
        if group:
            group_telegram_id = group.get('group_telegram_id')
            if group_telegram_id:
                try:
                    acceptance_message = (
                        "✅ **Lead Accepted**\n\n"
                        f"🚗 Driver: {driver.get('driver_name', 'Unknown')}\n"
                        f"📝 Extra info: {lead.get('extra_info', '')}\n"
                        f"📋 Reference ID: `{lead.get('reference_id', 'N/A')}`"
                    )
                    await context.bot.send_message(
                        chat_id=group_telegram_id,
                        text=acceptance_message,
                        parse_mode="Markdown"
                    )
                except Exception as e:
                    logger.error(f"Error forwarding acceptance to group: {e}")
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
    
    await query.message.edit_text(
        "❌ **Lead Declined**\n\n"
        "You have declined this lead.",
        parse_mode="Markdown"
    )


# Receipt submission handlers
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
    
    # Show lead details for confirmation
    confirmation_message = (
        f"✅ **Lead Found**\n\n"
        f"📍 Delivery Address: {lead.get('delivery_details', 'N/A')}\n"
        f"📞 Phone: {lead.get('phone_number', 'N/A')}\n"
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
        delete_response = requests.post(delete_url, json={"drop_pending_updates": True}, timeout=10)
        if delete_response.status_code == 200:
            data = delete_response.json()
            if data.get("ok"):
                logger.info("Webhook cleared (or was already clear) - safe to poll.")
            else:
                logger.warning(f"deleteWebhook response: {data}")
        else:
            logger.warning(f"deleteWebhook HTTP {delete_response.status_code}: {delete_response.text}")
    except Exception as e:
        logger.warning(f"Could not clear webhook: {e}")
    # Brief delay so Telegram releases any previous consumer before we start polling
    time.sleep(2)
    
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
        entry_points=[CommandHandler("start", start)],
        states={
            STATE_PHASE1: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phase1)],
            STATE_PHASE2: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phase2)],
            STATE_SELECT_DRIVER: [CallbackQueryHandler(handle_driver_selection, pattern="^select_driver_")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    # Create conversation handler for receipt submission
    receipt_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(handle_driver_receipt_callback, pattern="^driver_receipt$")],
        states={
            STATE_WAITING_REFERENCE_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_reference_id_input)],
            STATE_WAITING_RECEIPT_CONFIRM: [CallbackQueryHandler(handle_receipt_confirm_callback, pattern="^(confirm_receipt|cancel_receipt)$")],
            STATE_WAITING_RECEIPT_IMAGE: [MessageHandler(filters.PHOTO, handle_receipt_image)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    
    # Add handlers
    application.add_handler(conv_handler)
    application.add_handler(receipt_handler)
    
    # Add accept/decline handlers for driver assignments
    application.add_handler(CallbackQueryHandler(handle_accept_lead, pattern="^accept_lead_"))
    application.add_handler(CallbackQueryHandler(handle_decline_lead, pattern="^decline_lead_"))
    
    # Start the bot
    logger.info("Bot starting...")
    logger.info("Make sure only ONE instance of the bot is running!")
    
    try:
        # Use drop_pending_updates to avoid conflicts
        # The application will automatically check and clear webhooks if needed
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

