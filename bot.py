# ======================
# SECTION 1: IMPORTS AND CONSTANTS
# ======================
import asyncio
import datetime
import re

import gspread
import pytz
from gspread.exceptions import APIError, GSpreadException
from oauth2client.service_account import ServiceAccountCredentials
from telegram import Update, ReplyKeyboardRemove
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackContext,
    ConversationHandler,
    filters
)

# Google Sheets Configuration
SERVICE_ACCOUNT_JSON = json.loads(getenv('SERVICE_ACCOUNT_JSON'))
SPREADSHEET_ID = getenv('SPREADSHEET_ID')
SHEET_NAME = 'Suguan Logs'
PERSONAL_INFO_SHEET = 'Per Info DB Logs'
INBOX_SHEET = 'Inbox Message'
REVIEW_SHEET = 'Review Request'
REGISTRATION_SHEET = 'Registration'
STATS_SHEET = 'Stats Request'
YES_SHEET = 'Yes Log'


# Telegram Bot Configuration
TOKEN = getenv('TOKEN')

# Conversation states
INPUT_DATA, CONFIRM_DATA = range(2)  # For /send command
INFO_INPUT, INFO_CONFIRM = range(2, 4)  # For /info command
CONCERN_INPUT = range(4, 5)  # For /concern command
REVIEW_INPUT = range(5, 6)  # For /review command

# Valid gampanin codes
VALID_GAMPANIN = {'S1', 'S2', 'R1', 'R2', 'S', 'R', 'SL1', 'SL2', 'SLR1', 'SLR2'}

# Valid languages
VALID_LANGUAGES = {
    'Tag', 'Eng', 'Spa', 'Por', 'Ita', 'Ger', 'Fre', 'Jap', 'Kor',
    'Man', 'Can', 'Ind', 'Mal', 'Hin', 'Ara', 'Tha', 'Vie', 'Bur',
    'Rus', 'Swa', 'Tam', 'Ben', 'Tel', 'Tur', 'Cam'
}

# Valid URI values
VALID_URI = {'Minister', 'Min', 'M', 'Regular', 'Reg', 'R', 'Student', 'Stu', 'S'}

# Day mapping to full names
DAY_FULL_NAMES = {
    'Mon': 'Monday',
    'Tue': 'Tuesday',
    'Wed': 'Wednesday',
    'Thu': 'Thursday',
    'Fri': 'Friday',
    'Sat': 'Saturday',
    'Sun': 'Sunday'
}

# Timezone for Philippines
PH_TZ = pytz.timezone('Asia/Manila')

# Track pending notifications
pending_notifications = set()


# ======================
# SECTION 2: HELPER FUNCTIONS
# ======================
def init_google_sheets(sheet_name=SHEET_NAME):
    try:
        scope = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        creds = ServiceAccountCredentials.from_json_keyfile_dict(SERVICE_ACCOUNT_JSON, scope)
        client = gspread.authorize(creds)
        spreadsheet = client.open_by_key(SPREADSHEET_ID)
        return spreadsheet.worksheet(sheet_name)
    except (APIError, GSpreadException) as e:
        print(f"Error initializing Google Sheets: {str(e)}")
        raise



def format_day(day):
    day = day.strip().lower()
    days_mapping = {
        'mon': 'Mon', 'monday': 'Mon',
        'tue': 'Tue', 'tuesday': 'Tue',
        'wed': 'Wed', 'wednesday': 'Wed',
        'thu': 'Thu', 'thursday': 'Thu',
        'fri': 'Fri', 'friday': 'Fri',
        'sat': 'Sat', 'saturday': 'Sat',
        'sun': 'Sun', 'sunday': 'Sun'
    }
    return days_mapping.get(day, day.capitalize()[:3])


def format_time(time_str):
    time_str = time_str.strip().upper()
    time_str = time_str.replace(" ", "")

    if 'AM' in time_str or 'PM' in time_str:
        time_part = re.sub(r'[^0-9]', '', time_str)
        period = 'AM' if 'AM' in time_str else 'PM'
    else:
        time_part = re.sub(r'[^0-9]', '', time_str)
        period = ''

    # Check if time_part is empty (invalid input)
    if not time_part:
        return None

    try:
        if len(time_part) <= 2:
            hours = int(time_part)
            minutes = 0
        else:
            hours = int(time_part[:-2])
            minutes = int(time_part[-2:])
    except ValueError:
        return None

    if not period:
        if hours >= 12:
            period = 'PM'
            if hours > 12:
                hours -= 12
        else:
            period = 'AM'
            if hours == 0:
                hours = 12

    if hours < 1 or hours > 12 or minutes < 0 or minutes > 59:
        return None

    return f"{hours}:{minutes:02d} {period}"


def format_gampanin(code):
    code = code.strip().upper()
    return code if code in VALID_GAMPANIN else None


def format_language(lang):
    lang = lang.strip().title()[:3]
    return lang if lang in VALID_LANGUAGES else None


def format_local(local):
    return ' '.join(word.capitalize() for word in local.strip().split())


def format_name(name):
    return ' '.join(word.capitalize() for word in name.strip().split())


def format_uri(uri):
    uri = uri.strip().title()
    if uri in VALID_URI:
        return uri
    uri_lower = uri.lower()
    if uri_lower.startswith('m'):
        return 'Minister'
    elif uri_lower.startswith('r'):
        return 'Regular'
    elif uri_lower.startswith('s'):
        return 'Student'
    return None


def format_housing(housing):
    return ' '.join(word.capitalize() for word in housing.strip().split())


def parse_and_validate_schedule_input(text):
    parts = [part.strip() for part in text.split(',')]
    if len(parts) != 5:
        return None, "*Please provide exactly 5 values separated by commas:* day, time, local, gampanin, language"

    day, time_str, local, gampanin, language = parts

    formatted_day = format_day(day)
    if formatted_day not in ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']:
        return None, f"*Invalid day:* _{day}_. Please use day abbreviations (Mon, Tue, etc.)"

    formatted_time = format_time(time_str)
    if not formatted_time:
        return None, f"*Invalid time:* _{time_str}_. Please use formats like '5 AM', '5:30PM', '0530AM'"

    formatted_gampanin = format_gampanin(gampanin)
    if not formatted_gampanin:
        return None, f"*Invalid gampanin:* _{gampanin}_. Please use one of: {', '.join(VALID_GAMPANIN)}"

    formatted_language = format_language(language)
    if not formatted_language:
        return None, f"*Invalid language:* _{language}_. Please use 3-letter codes like Eng, Tag, Spa"

    formatted_local = format_local(local)

    return {
        'day': formatted_day,
        'day_full': DAY_FULL_NAMES.get(formatted_day, formatted_day),
        'time': formatted_time,
        'gampanin': formatted_gampanin,
        'language': formatted_language,
        'local': formatted_local
    }, None


def parse_and_validate_personal_info(text):
    # First try splitting by periods
    parts = [part.strip() for part in text.split('.')]

    # If that doesn't give us 8 parts, try splitting by commas (backward compatibility)
    if len(parts) != 8:
        parts = [part.strip() for part in text.split(',')]

    if len(parts) != 8:
        return None, (
            "*Please provide exactly 8 values po separated by periods (.)*:\n"
            "_First Name. Last Name. Uri. Assigned Lokal. District. Housing Address. Wife Chat ID. Wife Name_\n\n"
            "_Example:_ Juan. Dela Cruz. Minister. V Luna. District 1. Green Condo Unit#. 55247753. Maria Dela Cruz"
        )

    first_name, last_name, uri, lokal, district, housing, wife_chat_id, wife_name = parts

    formatted_first = format_name(first_name)
    formatted_last = format_name(last_name)
    formatted_uri = format_uri(uri)
    formatted_lokal = format_name(lokal)
    formatted_district = format_name(district)
    formatted_housing = format_housing(housing)
    formatted_wife_name = format_name(wife_name)

    if not formatted_uri:
        return None, f"*Invalid Uri:* _{uri}_. *Please use:* Minister/Min/M, Regular/Reg/R, Student/Stu/S"

    if not wife_chat_id.isdigit():
        return None, "*Wife Chat ID must be a number po.*\n\n_Example:_ 55251000053"

    uri_display = {
        'Minister': 'Minister',
        'Min': 'Minister',
        'M': 'Minister',
        'Regular': 'Regular',
        'Reg': 'Regular',
        'R': 'Regular',
        'Student': 'Student',
        'Stu': 'Student',
        'S': 'Student'
    }.get(formatted_uri, formatted_uri)

    return {
        'first_name': formatted_first,
        'last_name': formatted_last,
        'uri': formatted_uri,
        'uri_display': uri_display,
        'lokal': formatted_lokal,
        'district': formatted_district,
        'housing': formatted_housing,
        'wife_chat_id': wife_chat_id,
        'wife_name': formatted_wife_name
    }, None


# ======================
# SECTION 3: COMMAND HANDLERS - IMPLEMENT ALL HANDLERS FIRST
# ======================

async def start(update: Update, context: CallbackContext) -> int:
    # Get user's first name or username for personal greeting
    user = update.message.from_user
    greeting_name = user.first_name or user.username or "Kuya"

    # Send friendly greeting
    await update.message.reply_text(
        f"Hello po Ka {greeting_name}! 👋\n\n"
        #"*Welcome to the Suguan Reminder Bot!*\n"
        #"This bot will help remind you of your suguan schedules po."
        ,
        parse_mode=ParseMode.MARKDOWN
    )
    await log_registration(update)
    await help_command(update, context)
    return ConversationHandler.END

async def help_command(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text(
        "*Welcome po! This bot helps remind you of your suguan from time to time and is currently running on a local server po.*\n\n"
        
        "*Available commands:*\n"
        "/send - _Submit your suguan po._\n"
        "/review - _Request a review of your suguan po._\n"
        "/chatid - _Ate will provide this to you po._\n"
        "/info - _Submit your basic personal information po._\n"
        "/concern - _Send your concern to the bot owner po._\n"
        "/help - _Show this help message po._\n"
        "/cancel - _Cancel the current operation po._\n"
"/guidelines - _Read more for this bot._"
        ,
        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

async def chatid_command(update: Update, context: CallbackContext) -> int:
    chat_id = update.message.chat_id
    await update.message.reply_text(
        f"*Good day po, your chatID is:* _{chat_id}_\n\n"
        "Ate should give you her own Chat ID po, then you can submit it po together with your information po from /info.",
        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

async def cancel(update: Update, context: CallbackContext) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "*Current operation cancelled po.*",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

async def send_command(update: Update, context: CallbackContext) -> int:
    pending_notifications.discard(update.message.chat_id)
    await update.message.reply_text(
        "*Please send your suguan po (one at a time po):*\n"
        "_Format:_ Day, Time, Lokal, Gampanin, Language\n\n"
        "_Example:_ *Thu, 5:45AM, Green Condo, R1, Tag*\n\n"
        "*Thanks po🙏🏻.*",
        parse_mode=ParseMode.MARKDOWN
    )
    return INPUT_DATA


async def handle_schedule_input(update: Update, context: CallbackContext) -> int:
    # First check if we have a valid message
    if not update or not update.message or not update.message.text:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Please send your suguan as text po. Example:\n"
                 "*Thu, 5:45AM, Green Condo, R1, Tag*",
            parse_mode=ParseMode.MARKDOWN
        )
        return INPUT_DATA

    try:
        user_input = update.message.text
        data, error = parse_and_validate_schedule_input(user_input)

        if error:
            await update.message.reply_text(error, parse_mode=ParseMode.MARKDOWN)
            return INPUT_DATA

        # Calculate the date for the specified day in the current week (Monday-Sunday)
        today = datetime.datetime.now(PH_TZ).date()
        current_weekday = today.weekday()  # Monday=0, Sunday=6

        # Get the target day's weekday number (0=Monday, 6=Sunday)
        day_mapping = {'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3, 'Fri': 4, 'Sat': 5, 'Sun': 6}
        target_weekday = day_mapping.get(data['day'], 0)

        # Calculate the date
        days_difference = (target_weekday - current_weekday) % 7
        target_date = today + datetime.timedelta(days=days_difference)

        # Format the date as "Month Day, Year" (e.g., "May 17, 2025")
        date_display = target_date.strftime("%B %d, %Y")

        context.user_data['formatted_data'] = data
        context.user_data['user_name'] = update.message.from_user.full_name

        formatted_message = (
            "*Please confirm your suguan po:*\n\n"
            f"*Date:* {date_display}\n"
            f"*Day:* {data['day_full']}\n"
            f"*Time:* {data['time']}\n"
            f"*Lokal:* {data['local']}\n"
            f"*Gampanin:* {data['gampanin']}\n"
            f"*Language:* {data['language']}\n\n"
            "If everything is correct po, send /submit\n"
            "To start over po, send /send"
        )

        await update.message.reply_text(formatted_message, parse_mode=ParseMode.MARKDOWN)
        return CONFIRM_DATA

    except Exception as e:
        print(f"Error in handle_schedule_input: {str(e)}")
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌",
            parse_mode=ParseMode.MARKDOWN
        )
        return INPUT_DATA

async def submit_schedule(update: Update, context: CallbackContext) -> int:
    data = context.user_data.get('formatted_data')
    user_name = context.user_data.get('user_name', '')

    if not data:
        await update.message.reply_text("*No data to submit. Please start over.*", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    try:
        worksheet = init_google_sheets()
        timestamp = datetime.datetime.now(PH_TZ).strftime('%Y-%m-%d %H:%M:%S')
        row_data = [
            update.message.chat_id,
            timestamp,
            data['day'],
            data['time'],
            data['gampanin'],
            data['local'],
            user_name,
            data['language']
        ]

        worksheet.append_row(row_data)
        await update.message.reply_text(
            "*🤝Your Suguan has been successfully submitted, po!*\n\n"
            "If you have more suguan po, feel free to send another one or more po."
,
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data.clear()
    except (APIError, GSpreadException) as e:
        pending_notifications.add(update.message.chat_id)
        await update.message.reply_text(
            f"*⚠️ Error saving data:* _{str(e)}_",
            parse_mode=ParseMode.MARKDOWN
        )
    return INPUT_DATA

async def info_command(update: Update, context: CallbackContext) -> int:
    pending_notifications.discard(update.message.chat_id)
    if 'personal_info' in context.user_data:
        del context.user_data['personal_info']

    await update.message.reply_text(
        "*Please send your personal information po. It will help the system remind you more reliably po:*\n\n"
        "_Use this format po:_ First Name. Last Name. Uri. Assigned Lokal. District. Housing Address. Wife Chat ID. Wife Name\n\n"
        "_Example:_ *Juan. Dela Cruz. Minister. V Luna. Central. Green Condo Unit#. 5524775355. Maria*\n\n"
        "Note: To get your wife's Chat ID po, kindly send the bot (@R507RemBot) to your wife po, then ask her to click /chatid."
        "Please copy that ID po and send it here so that the reminders can also be sent to her po.",
        parse_mode=ParseMode.MARKDOWN
    )
    return INFO_INPUT

async def handle_personal_info_input(update: Update, context: CallbackContext) -> int:
    user_input = update.message.text
    data, error = parse_and_validate_personal_info(user_input)

    if error:
        await update.message.reply_text(error, parse_mode=ParseMode.MARKDOWN)
        return INFO_INPUT

    context.user_data['personal_info'] = data

    confirmation_message = (
        "*Is your information correct?*\n\n"
        f"*First Name:* {data['first_name']}\n"
        f"*Last Name:* {data['last_name']}\n"
        f"*Uri:* {data['uri_display']}\n"
        f"*Lokal:* {data['lokal']}\n"
        f"*District:* {data['district']}\n"
        f"*Housing:* {data['housing']}\n"
        f"*Wife Chat ID:* {data['wife_chat_id']}\n"
        f"*Wife Name:* {data['wife_name']}\n\n"
        "*If correct, send* /submit\n"
        "*To start over, send* /info"
    )

    await update.message.reply_text(confirmation_message, parse_mode=ParseMode.MARKDOWN)
    return INFO_CONFIRM

async def submit_personal_info(update: Update, context: CallbackContext) -> int:
    personal_info = context.user_data.get('personal_info')

    if not personal_info:
        await update.message.reply_text("*No information to submit.*", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    try:
        worksheet = init_google_sheets(PERSONAL_INFO_SHEET)
        timestamp = datetime.datetime.now(PH_TZ).strftime('%Y-%m-%d %H:%M:%S')
        row_data = [
            update.message.chat_id,
            timestamp,
            personal_info['first_name'],
            personal_info['last_name'],
            personal_info['uri'],
            personal_info['lokal'],
            personal_info['district'],
            personal_info['housing'],
            personal_info['wife_chat_id'],
            personal_info['wife_name']
        ]

        worksheet.append_row(row_data)
        await update.message.reply_text(
            "*🤝Your information has been recorded po!*",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data.clear()
    except (APIError, GSpreadException) as e:
        pending_notifications.add(update.message.chat_id)
        await update.message.reply_text(
            f"*⚠️ Error saving information:* _{str(e)}_",
            parse_mode=ParseMode.MARKDOWN
        )
    return ConversationHandler.END

async def concern_command(update: Update, context: CallbackContext) -> range:
    pending_notifications.discard(update.message.chat_id)
    await update.message.reply_text(
        "*Please write your concern or message po.*",
        parse_mode=ParseMode.MARKDOWN
    )
    return CONCERN_INPUT

async def handle_concern_input(update: Update, context: CallbackContext) -> range:
    context.user_data['concern_message'] = update.message.text
    context.user_data['user_name'] = update.message.from_user.full_name

    await update.message.reply_text(
        "*Your message has been recorded. Please type* /submit *to send it.*",
        parse_mode=ParseMode.MARKDOWN
    )
    return CONCERN_INPUT

async def submit_concern(update: Update, context: CallbackContext) -> int:
    concern_message = context.user_data.get('concern_message')
    user_name = context.user_data.get('user_name', '')

    if not concern_message:
        await update.message.reply_text("*No message to submit.*", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    try:
        worksheet = init_google_sheets(INBOX_SHEET)
        timestamp = datetime.datetime.now(PH_TZ).strftime('%Y-%m-%d %H:%M:%S')
        row_data = [
            update.message.chat_id,
            timestamp,
            user_name,
            concern_message
        ]

        worksheet.append_row(row_data)
        await update.message.reply_text(
            "*🤝Your concern has been sent po!*",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data.clear()
    except (APIError, GSpreadException) as e:
        pending_notifications.add(update.message.chat_id)
        await update.message.reply_text(
            f"*⚠️ Error sending your concern:* _{str(e)}_",
            parse_mode=ParseMode.MARKDOWN
        )
    return ConversationHandler.END

async def review_command(update: Update, context: CallbackContext) -> int:
    pending_notifications.discard(update.message.chat_id)
    await update.message.reply_text(
        "Your review request has been sent po! Please wait 1–2 minutes.\n\n"
        
        "Take note that if you haven’t submitted your suguan yet po or your suguan is already done, you won’t receive a message.\n\n"
"*Thank you for patiently waiting po!🙏🏻*",

        parse_mode=ParseMode.MARKDOWN
    )

    try:
        worksheet = init_google_sheets(REVIEW_SHEET)
        timestamp = datetime.datetime.now(PH_TZ).strftime('%Y-%m-%d %H:%M:%S')
        row_data = [
            update.message.chat_id,
            timestamp,
            update.message.from_user.full_name,
            "Automatic review request"
        ]
        worksheet.append_row(row_data)
    except (APIError, GSpreadException) as e:
        pending_notifications.add(update.message.chat_id)
        await update.message.reply_text(
            f"*⚠️ Error saving your review request:* _{str(e)}_",
            parse_mode=ParseMode.MARKDOWN
        )
    return ConversationHandler.END

async def guidelines_command(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text(
        "*📝 Guidelines for Using This Bot:*\n\n"
"▫️ *ONLINE/OFFLINE*\n"
"  - If you click any command and nothing happens, it means the *server is currently offline*. Please try again later po.\n"
"  - Being offline *only affects submissions*, but *reminders will continue to work 24/7* po.\n\n"

"▫️ *SUBMISSION*\n"
"  - Please review your *suguan* before and after submission po.\n"
"  - If you want to delete your submitted *suguan* or personal information, just *resubmit it* and the old data will be *automatically deleted* po.\n\n"

"▫️ *WIFE CHAT ID*\n"
"  - This is a unique feature of the bot. Once you enter your *Wife Chat ID*, the system will work at full capacity po, including reminding your wife when needed.\n"
"  - If you are single po, please enter *\"0\"* (or multiple zeros) for *\"Wife Chat ID\"* and write *\"None\"* for *\"Wife Name\"*.\n"
"  - To get your wife’s Chat ID po, send the bot (@R507RemBot) to your wife and ask her to click */chatid*. Then copy that ID and send it with this format:\n"
"    *First Name. Last Name. Uri. Assigned Lokal. District. Housing Address. Wife Chat ID. Wife Name*\n\n"

"▫️ *REMINDERS (currently)*\n"
"  - The bot sends reminders *10 to 16 hours* before your suguan.\n"
"  - It also sends a reminder *2 hours before* the suguan.\n"
"  - If you haven’t logged your suguan, the bot will remind you *twice (Monday and Tuesday)*. If still not submitted, the bot will message your wife (if provided) to remind you po.\n"
"  - *Future Feature:* Even if you don’t enter your suguan, the bot will send default reminders *every Tuesday at 6 PM and Friday at 6 PM*.\n"
"  - *Future Feature:* The bot will also remind you *2 days in advance* to study a lesson and will send the lesson po.\n\n"

"▫️ *ERRORS*\n"
"  - If you encounter errors or wrong replies, please click */cancel* to reset the current operation.\n\n"

"▫️ *RESPONSES*\n"
"  - For */review-*, it’s normal to take up to *1 minute* because the system is compiling information and waiting for the exact send time (sent time + 1 min).\n"
"  - We appreciate your patience po.\n\n"

"🧾 Always check this */guidelines* command po for updates.",

        parse_mode=ParseMode.MARKDOWN
    )
    return ConversationHandler.END

async def stats_command(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text(
        "*Your stats request has been submitted po!*",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        worksheet = init_google_sheets(STATS_SHEET)
        timestamp = datetime.datetime.now(PH_TZ).strftime('%Y-%m-%d %H:%M:%S')
        row_data = [
            update.message.chat_id,
            timestamp,
            update.message.from_user.full_name,
            "Automatic stats request"
        ]
        worksheet.append_row(row_data)
    except (APIError, GSpreadException) as e:
        pending_notifications.add(update.message.chat_id)
        await update.message.reply_text(
            f"*⚠️ Error saving your stats request:* _{str(e)}_",
            parse_mode=ParseMode.MARKDOWN
        )
    return ConversationHandler.END

async def yes_command(update: Update, context: CallbackContext) -> int:
    await update.message.reply_text(
        "*TY po.*",
        parse_mode=ParseMode.MARKDOWN
    )

    try:
        worksheet = init_google_sheets(YES_SHEET)  # Use the global YES_SHEET constant
        timestamp = datetime.datetime.now(PH_TZ).strftime('%Y-%m-%d %H:%M:%S')
        row_data = [
            update.message.chat_id,
            timestamp,
            update.message.from_user.full_name,
            "Automatic yes request"
        ]
        worksheet.append_row(row_data)
    except (APIError, GSpreadException) as e:
        pending_notifications.add(update.message.chat_id)
        await update.message.reply_text(
            f"*⚠️ Error saving your yes request:* _{str(e)}_",
            parse_mode=ParseMode.MARKDOWN
        )
    return ConversationHandler.END


async def log_registration(update: Update):
    try:
        worksheet = init_google_sheets(REGISTRATION_SHEET)
        timestamp = datetime.datetime.now(PH_TZ).strftime('%Y-%m-%d %H:%M:%S')
        location = "Unknown"
        if update.message and update.message.location:
            location = f"{update.message.location.latitude}, {update.message.location.longitude}"

        row_data = [
            update.message.chat_id,
            timestamp,
            update.message.from_user.full_name,
            location
        ]
        worksheet.append_row(row_data)
    except Exception as e:
        print(f"Error logging registration: {str(e)}")

# ======================
# SECTION 4: MAIN APPLICATION SETUP
# ======================
def main() -> None:
    application = Application.builder().token(TOKEN).build()

    # Schedule submission handler
    schedule_handler = ConversationHandler(
        entry_points=[CommandHandler("send", send_command)],
        states={
            INPUT_DATA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_schedule_input),
                CommandHandler("help", help_command),
                CommandHandler("chatid", chatid_command),
                CommandHandler("send", send_command)
            ],
            CONFIRM_DATA: [
                CommandHandler("submit", submit_schedule),
                CommandHandler("send", send_command),
                CommandHandler("help", help_command),
                CommandHandler("chatid", chatid_command),
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_schedule_input)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Personal info handler
    personal_info_handler = ConversationHandler(
        entry_points=[CommandHandler("info", info_command)],
        states={
            INFO_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_personal_info_input),
                CommandHandler("cancel", cancel)
            ],
            INFO_CONFIRM: [
                CommandHandler("submit", submit_personal_info),
                CommandHandler("info", info_command),
                CommandHandler("cancel", cancel)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Concern handler
    concern_handler = ConversationHandler(
        entry_points=[CommandHandler("concern", concern_command)],
        states={
            CONCERN_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, handle_concern_input),
                CommandHandler("submit", submit_concern),
                CommandHandler("cancel", cancel)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # Add all handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("chatid", chatid_command))
    application.add_handler(CommandHandler("guidelines", guidelines_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("yes", yes_command))
    application.add_handler(schedule_handler)
    application.add_handler(personal_info_handler)
    application.add_handler(concern_handler)
    application.add_handler(CommandHandler("review", review_command))

    # Start notification loop
    async def notify_pending():
        while True:
            if pending_notifications:
                for chat_id in list(pending_notifications):
                    try:
                        await application.bot.send_message(
                            chat_id=chat_id,
                            text="*The system is now online. Please resubmit your pending schedules/operations po.*",
                            parse_mode=ParseMode.MARKDOWN
                        )
                        pending_notifications.remove(chat_id)
                    except Exception:
                        pending_notifications.discard(chat_id)
            await asyncio.sleep(60)

    application.job_queue.run_once(lambda ctx: asyncio.create_task(notify_pending()), when=0)

    print("Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()