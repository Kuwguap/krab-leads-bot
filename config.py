"""Configuration module for environment variables."""
import os
from pathlib import Path
from dotenv import load_dotenv

# Explicitly load .env file from the project root
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)


class Config:
    """Application configuration from environment variables."""
    
    # Telegram
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    DRIVER_TELEGRAM_ID = os.getenv("DRIVER_TELEGRAM_ID")
    GROUP_TELEGRAM_ID = os.getenv("GROUP_TELEGRAM_ID")
    SUPERVISORY_TELEGRAM_ID = os.getenv("SUPERVISORY_TELEGRAM_ID")
    
    # Monday.com
    MONDAY_API_KEY = os.getenv("MONDAY_API_KEY")
    MONDAY_BOARD_ID = os.getenv("MONDAY_BOARD_ID")
    MONDAY_API_URL = "https://api.monday.com/v2"
    
    # OneTimeSecret (strip whitespace — Windows .env often adds CR/LF and breaks Basic auth)
    ONETIMESECRET_USERNAME = (os.getenv("ONETIMESECRET_USERNAME") or "").strip() or None
    ONETIMESECRET_API_KEY = (os.getenv("ONETIMESECRET_API_KEY") or "").strip() or None
    # OneTimeSecret-compatible endpoint (hosted app in `clientsphonenumber/`, e.g. Vercel + custom domain)
    ONETIMESECRET_URL = (os.getenv("ONETIMESECRET_URL") or "https://clientsphonenumber.com/api/v1/share").strip()
    # Public base URL for unlock links (must end with `/secret/`)
    ONETIMESECRET_LINK_BASE = (os.getenv("ONETIMESECRET_LINK_BASE") or "https://clientsphonenumber.com/secret/").strip()
    ONETIMESECRET_PASSPHRASE = (os.getenv("ONETIMESECRET_PASSPHRASE") or "DispatchPassword").strip()
    
    # Supabase
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")

    # AI / Vision (optional – for image → structured Phase 1)
    OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip() or None
    OPENAI_VISION_MODEL = os.getenv("OPENAI_VISION_MODEL", "gpt-4o").strip() or "gpt-4o"

    # VIN decode: choose provider in .env (nhtsa = free, api_ninjas = premium)
    VIN_PROVIDER = (os.getenv("VIN_PROVIDER") or "nhtsa").strip().lower()
    API_NINJAS_API_KEY = (os.getenv("API_NINJAS_API_KEY") or "").strip() or None

    @classmethod
    def is_vin_lookup_configured(cls) -> bool:
        """True if VIN lookup is available (nhtsa always, or api_ninjas when key set)."""
        if cls.VIN_PROVIDER == "api_ninjas":
            return bool(cls.API_NINJAS_API_KEY)
        return True  # nhtsa or any other → assume available

    @classmethod
    def is_ai_vision_configured(cls) -> bool:
        """Whether image upload in Phase 1 can use AI to extract details."""
        return bool(cls.OPENAI_API_KEY)

    @classmethod
    def validate(cls):
        """Validate that all required environment variables are set."""
        required_vars = [
            "TELEGRAM_BOT_TOKEN",
            "ONETIMESECRET_USERNAME",
            "ONETIMESECRET_API_KEY",
            "SUPABASE_URL",
            "SUPABASE_KEY",
        ]
        
        # Monday.com is optional - warn if not set but don't fail
        optional_vars = [
            "MONDAY_API_KEY",
            "MONDAY_BOARD_ID",
        ]
        
        missing = []
        for var in required_vars:
            value = getattr(cls, var)
            # Check if value is None or empty string
            if not value or (isinstance(value, str) and value.strip() == ""):
                missing.append(var)
        
        if missing:
            raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
        
        # Warn about optional variables
        missing_optional = []
        for var in optional_vars:
            value = getattr(cls, var)
            if not value or (isinstance(value, str) and value.strip() == ""):
                missing_optional.append(var)
        
        if missing_optional:
            import warnings
            warnings.warn(f"Optional Monday.com variables not set: {', '.join(missing_optional)}. Monday.com integration will be disabled.")
        
        return True
    
    @classmethod
    def is_monday_configured(cls) -> bool:
        """Check if Monday.com is properly configured."""
        return bool(cls.MONDAY_API_KEY and cls.MONDAY_BOARD_ID)

