"""Diagnostic script to test bot setup and identify issues."""
import sys
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def test_imports():
    """Test if all required modules can be imported."""
    logger.info("Testing imports...")
    try:
        from telegram import Update
        from telegram.ext import Application
        logger.info("✓ telegram library imported")
    except ImportError as e:
        logger.error(f"✗ Failed to import telegram: {e}")
        return False
    
    try:
        from config import Config
        logger.info("✓ config imported")
    except ImportError as e:
        logger.error(f"✗ Failed to import config: {e}")
        return False
    
    try:
        from utils.database import Database
        logger.info("✓ database imported")
    except ImportError as e:
        logger.error(f"✗ Failed to import database: {e}")
        return False
    
    try:
        from utils.onetimesecret import OneTimeSecret
        logger.info("✓ onetimesecret imported")
    except ImportError as e:
        logger.error(f"✗ Failed to import onetimesecret: {e}")
        return False
    
    try:
        from utils.monday import MondayClient
        logger.info("✓ monday imported")
    except ImportError as e:
        logger.error(f"✗ Failed to import monday: {e}")
        return False
    
    return True

def test_config():
    """Test configuration validation."""
    logger.info("\nTesting configuration...")
    try:
        from config import Config
        Config.validate()
        logger.info("✓ Configuration valid")
        return True
    except ValueError as e:
        logger.error(f"✗ Configuration error: {e}")
        return False
    except Exception as e:
        logger.error(f"✗ Unexpected error validating config: {e}")
        return False

def test_services():
    """Test service initialization."""
    logger.info("\nTesting service initialization...")
    try:
        from config import Config
        from utils.database import Database
        from utils.onetimesecret import OneTimeSecret
        from utils.monday import MondayClient
        
        logger.info("Initializing Database...")
        db = Database()
        logger.info("✓ Database initialized")
        
        logger.info("Initializing OneTimeSecret...")
        ots = OneTimeSecret()
        logger.info("✓ OneTimeSecret initialized")
        
        if Config.is_monday_configured():
            logger.info("Initializing MondayClient...")
            monday = MondayClient()
            logger.info("✓ MondayClient initialized")
        else:
            logger.info("⚠ Monday.com not configured (optional)")
        
        return True
    except Exception as e:
        logger.error(f"✗ Service initialization failed: {e}", exc_info=True)
        return False

def test_telegram_connection():
    """Test Telegram bot token."""
    logger.info("\nTesting Telegram connection...")
    try:
        from config import Config
        from telegram.ext import Application
        
        if not Config.TELEGRAM_BOT_TOKEN:
            logger.error("✗ TELEGRAM_BOT_TOKEN not set")
            return False
        
        logger.info("Creating Telegram application...")
        app = Application.builder().token(Config.TELEGRAM_BOT_TOKEN).build()
        logger.info("✓ Telegram application created successfully")
        
        # Test webhook info
        import requests
        bot_token = Config.TELEGRAM_BOT_TOKEN
        webhook_url = f"https://api.telegram.org/bot{bot_token}/getWebhookInfo"
        response = requests.get(webhook_url, timeout=5)
        if response.status_code == 200:
            webhook_data = response.json()
            if webhook_data.get("result", {}).get("url"):
                logger.warning(f"⚠ Webhook is set: {webhook_data['result']['url']}")
                logger.warning("  This may conflict with polling mode")
            else:
                logger.info("✓ No webhook set (good for polling)")
        else:
            logger.warning(f"⚠ Could not check webhook status: {response.status_code}")
        
        return True
    except Exception as e:
        logger.error(f"✗ Telegram connection test failed: {e}", exc_info=True)
        return False

def main():
    """Run all diagnostic tests."""
    logger.info("="*60)
    logger.info("Bot Setup Diagnostic Tool")
    logger.info("="*60)
    
    results = []
    
    results.append(("Imports", test_imports()))
    results.append(("Configuration", test_config()))
    results.append(("Services", test_services()))
    results.append(("Telegram Connection", test_telegram_connection()))
    
    logger.info("\n" + "="*60)
    logger.info("Summary:")
    logger.info("="*60)
    
    all_passed = True
    for name, passed in results:
        status = "✓ PASS" if passed else "✗ FAIL"
        logger.info(f"{name}: {status}")
        if not passed:
            all_passed = False
    
    if all_passed:
        logger.info("\n✓ All tests passed! Bot should be ready to run.")
        return 0
    else:
        logger.error("\n✗ Some tests failed. Please fix the issues above.")
        return 1

if __name__ == "__main__":
    sys.exit(main())
