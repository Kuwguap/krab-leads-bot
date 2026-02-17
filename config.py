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
    
    # OneTimeSecret
    ONETIMESECRET_USERNAME = os.getenv("ONETIMESECRET_USERNAME")
    ONETIMESECRET_API_KEY = os.getenv("ONETIMESECRET_API_KEY")
    ONETIMESECRET_URL = "https://onetimesecret.com/api/v1/share"
    ONETIMESECRET_PASSPHRASE = "DispatchPassword"
    
    # Supabase
    SUPABASE_URL = os.getenv("SUPABASE_URL")
    SUPABASE_KEY = os.getenv("SUPABASE_KEY")
    
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

