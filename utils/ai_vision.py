"""
AI vision integration: extract structured lead fields from an image.
Uses OCR + LLM (OpenAI vision) to get the same 11-field structure as text input.
Includes validation for extracted data (VIN, line count, required fields).
"""
import base64
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Number of required lines for Phase 1 structured output
PHASE1_LINE_COUNT = 11


class AIVisionQuotaError(Exception):
    """Raised when the AI provider returns 429 / insufficient quota."""
    pass

# Expected output: exactly 11 lines in this order (used by parse_phase1_structured)
STRUCTURE_PROMPT = """You are extracting vehicle/registration and delivery details from an image (screenshot or form).

STRICT RULES:
- Output ONLY a plain text block with exactly 11 lines. One line per field—nothing else on that line.
- Each line must contain ONLY the value for that field. No phone numbers in any line (phone is collected separately later). No URLs, no extra text.
- Line 6 (VIN): exactly 17 alphanumeric characters (no spaces, no truncation, no extra digits). Or "-" if missing. Nothing else on that line.
- Line 7 (Car): only year, make, and model—e.g. "2020 Nissan Altima". Nothing else.
- If a value is missing or unreadable, put a single dash "-" for that line.

Order (one value per line, no labels):
1) Full Name
2) Registration Address (street only)
3) Registration City, State, ZIP
4) Delivery address (street only)
5) Delivery city, State, ZIP
6) VIN (exactly 17 alphanumeric characters, never cut or add)
7) Car (year, make, model only)
8) Color
9) Insurance company
10) Insurance policy number
11) Delivery Date/Time and any extra info

Example (replace with actual values):
John Doe
123 Main St
Boston, MA 02101
456 Oak Ave
Cambridge, MA 02139
1HGBH41JXMN109186
2020 Toyota Camry
Silver
State Farm
123-456-789
Tomorrow 2pm, gate code 1234

Output nothing else—no explanation, no markdown, no line numbers. Only these 11 lines."""

# For freeform text: user can send any format; we ask the model to identify and rearrange into 11 lines
TEXT_STRUCTURE_PROMPT = """The user sent the following message. It may be in any format: paragraph, bullet list, different order, labels like "Name: John", etc.

STRICT RULES:
- Output ONLY a plain text block with exactly 11 lines. One line per field—nothing else on that line.
- Each line must contain ONLY the value for that field. Do NOT put phone numbers in any of these 11 lines (phone is collected separately). No URLs. No extra text.
- Line 6 (VIN): exactly 17 alphanumeric characters (no spaces, no truncation, no extra digits). Or "-" if missing. Nothing else on that line.
- Line 7 (Car): only year, make, and model—e.g. "2020 Nissan Altima". Nothing else on that line.
- If something is missing, put a single dash "-" for that line.

Order of the 11 lines (one value per line, no labels):
1) Full Name
2) Registration Address (street only)
3) Registration City, State, ZIP
4) Delivery address (street only)
5) Delivery city, State, ZIP
6) VIN (exactly 17 alphanumeric characters, never cut or add)
7) Car (year, make, model only)
8) Color
9) Insurance company
10) Insurance policy number
11) Delivery Date/Time and any extra info

Output nothing else—no explanation, no markdown, no line numbers. Only these 11 lines.

User message:
"""


def _call_openai_text(messages: list) -> Optional[str]:
    """Call OpenAI chat completions (text only). Returns assistant content or None."""
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai package not installed. pip install openai")
        return None
    from config import Config
    api_key = Config.OPENAI_API_KEY
    if not api_key or not api_key.strip():
        return None
    model = getattr(Config, "OPENAI_VISION_MODEL", None) or "gpt-4o"
    try:
        client = OpenAI(api_key=api_key.strip())
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=1024,
        )
        text = (response.choices[0].message.content or "").strip()
        return text if text else None
    except AIVisionQuotaError:
        raise
    except Exception as e:
        err_msg = str(e).lower()
        if "429" in err_msg or "insufficient_quota" in err_msg or "quota" in err_msg or "rate limit" in err_msg:
            logger.warning("OpenAI quota exceeded: %s", e)
            raise AIVisionQuotaError("API quota exceeded") from e
        logger.exception("OpenAI text call failed: %s", e)
        return None


def extract_structured_from_text(user_message: str) -> Optional[str]:
    """
    Take freeform text from the user (any format/order) and return 11-line structured text
    suitable for parse_phase1_structured. Returns None if API not configured or request fails.
    """
    if not (user_message or user_message.strip()):
        return None
    prompt = TEXT_STRUCTURE_PROMPT + (user_message.strip()[:4000])
    return _call_openai_text([{"role": "user", "content": prompt}])


def extract_structured_from_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> Optional[str]:
    """
    Send image to OpenAI Vision and get back 11-line structured text suitable for parse_phase1_structured.
    Returns None if API is not configured or request fails.
    """
    from config import Config
    api_key = Config.OPENAI_API_KEY
    if not api_key or not api_key.strip():
        logger.warning("OPENAI_API_KEY not set; cannot process image.")
        return None

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    data_url = f"data:{mime_type};base64,{b64}"

    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key.strip())
        model = getattr(Config, "OPENAI_VISION_MODEL", None) or "gpt-4o"
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": STRUCTURE_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            max_tokens=1024,
        )
        text = (response.choices[0].message.content or "").strip()
        if not text:
            return None
        return text
    except AIVisionQuotaError:
        raise
    except Exception as e:
        err_msg = str(e).lower()
        if "429" in err_msg or "insufficient_quota" in err_msg or "quota" in err_msg or "rate limit" in err_msg:
            logger.warning("AI vision quota exceeded: %s", e)
            raise AIVisionQuotaError("API quota exceeded") from e
        logger.exception("AI vision extraction failed: %s", e)
        return None


def _has_value(val: str) -> bool:
    """True if field has a non-empty value (not blank or single dash)."""
    return bool(val and str(val).strip() and str(val).strip() != "-")


def validate_phase1_extraction(normalized_text: str, state_data: dict) -> tuple[bool, list[str]]:
    """
    Run built-in checks on AI-extracted Phase 1 data.
    Returns (is_valid, list of error messages).
    We accept >= 11 lines (use first 11); VIN format is not enforced so extraction still parses.
    """
    errors: list[str] = []

    # 1) Line count: need at least 11 lines; we use first 11, extra lines are ignored
    lines = [ln.strip() for ln in normalized_text.splitlines() if ln.strip()]
    if len(lines) < PHASE1_LINE_COUNT:
        errors.append(
            f"Expected at least 11 lines from the image, got {len(lines)}. "
            "Please send as text or try another image."
        )

    # 2) Required fields (name and at least one delivery field)
    name = (state_data.get("name") or "").strip()
    if not _has_value(name):
        errors.append("Full name is missing or unreadable.")

    delivery_addr = (state_data.get("delivery_address") or "").strip()
    delivery_csz = (state_data.get("delivery_city_state_zip") or "").strip()
    if not _has_value(delivery_addr) and not _has_value(delivery_csz):
        errors.append("Delivery address and Delivery city/state/ZIP are both missing or unreadable.")

    # VIN format is not enforced – whatever the AI extracted is kept (no block on "Bronx New York" etc.)

    return (len(errors) == 0, errors)


# Field labels for user-friendly missing-field prompts
MISSING_FIELD_PROMPTS = {
    "color": ("You missed out the vehicle color. Can you add it?", "color"),
    "vin": ("You missed out the VIN. Can you add it?", "vin"),
    "car": ("You missed out the car (year/make/model). Can you add it?", "car"),
    "insurance_company": ("You missed out the insurance company. Can you add it?", "insurance_company"),
    "delivery_date": ("You missed out the delivery date/time. Can you add it?", "extra_info"),
}


def detect_missing_fields(state_data: dict, raw_input: str) -> list[str]:
    """
    Detect important missing fields. Uses quick check first, then OpenAI if configured.
    Returns list of field keys (e.g. ["color"]). Uses OPENAI_API_KEY for AI detection.
    """
    def _has_val(key: str) -> bool:
        v = (state_data.get(key) or "").strip()
        return bool(v and v != "-")

    # Quick check: color is commonly missed
    if not _has_val("color"):
        return ["color"]

    # Use OpenAI to scan for other missing fields (if API configured)
    try:
        from config import Config
        if not Config.OPENAI_API_KEY or not str(Config.OPENAI_API_KEY).strip():
            return []
    except Exception:
        return []

    prompt = (
        "Vehicle/lead info extracted:\n"
        f"Name: {state_data.get('name') or '-'}\n"
        f"VIN: {state_data.get('vin') or '-'}\n"
        f"Car: {state_data.get('car') or '-'}\n"
        f"Color: {state_data.get('color') or '-'}\n"
        f"Insurance: {state_data.get('insurance_company') or '-'}\n"
        f"Extra/Delivery time: {state_data.get('extra_info') or '-'}\n\n"
        "Raw message: " + (raw_input[:400] or "") + "\n\n"
        "Reply with ONLY a comma-separated list of missing fields: color, vin, car, insurance_company, delivery_date. "
        "Only list fields that are clearly missing (blank or dash). If none missing, reply: none"
    )
    try:
        out = _call_openai_text([{"role": "user", "content": prompt}])
        if not out or not out.strip():
            return []
        out = out.strip().lower()
        if "none" in out:
            return []
        missing = []
        for w in out.replace(",", " ").split():
            w = w.strip()
            if w in ("color", "vin", "car", "insurance_company", "delivery_date") and w not in missing:
                if w == "delivery_date" and _has_val("extra_info"):
                    continue
                if w in ("vin", "car", "insurance_company") and _has_val(w):
                    continue
                missing.append(w)
        return missing
    except Exception as e:
        logger.warning("detect_missing_fields: %s", e)
        return []
