"""OneTimeSecret API integration for encrypting phone numbers."""
import requests
from config import Config
from typing import Optional, Dict


class OneTimeSecret:
    """OneTimeSecret API client."""
    
    def __init__(self):
        self.url = Config.ONETIMESECRET_URL
        self.username = Config.ONETIMESECRET_USERNAME
        self.api_key = Config.ONETIMESECRET_API_KEY
        self.passphrase = Config.ONETIMESECRET_PASSPHRASE
        self.link_base = getattr(Config, "ONETIMESECRET_LINK_BASE", "https://clientsphonenumber.com/secret/")
    
    def encrypt_phone(self, phone_number: str) -> Optional[Dict[str, str]]:
        """
        Encrypt phone number using OneTimeSecret.
        
        Returns:
            Dict with 'secret_key' and 'metadata_key', or None on error
        """
        try:
            response = requests.post(
                self.url,
                auth=(self.username, self.api_key),
                data={
                    "secret": phone_number,
                    "passphrase": self.passphrase,
                    "ttl": 2592000  # 30 days in seconds
                },
                timeout=10
            )
            
            if response.status_code == 200:
                data = response.json()
                return {
                    "secret_key": data.get("secret_key"),
                    "metadata_key": data.get("metadata_key"),
                    "link": f"{self.link_base}{data.get('secret_key')}"
                }
            else:
                print(f"OneTimeSecret API error: {response.status_code} - {response.text}")
                return None
                
        except Exception as e:
            print(f"Error encrypting phone with OneTimeSecret: {e}")
            return None

    def share_secret(self, secret: str) -> Optional[str]:
        """
        Store any secret in OneTimeSecret and return the one-time link.
        Use for redacting phone numbers (or other sensitive text) from messages.
        """
        try:
            response = requests.post(
                self.url,
                auth=(self.username, self.api_key),
                data={
                    "secret": secret.strip(),
                    "passphrase": self.passphrase,
                    "ttl": 2592000,
                },
                timeout=10,
            )
            if response.status_code == 200:
                data = response.json()
                return f"{self.link_base}{data.get('secret_key')}"
            return None
        except Exception as e:
            print(f"Error sharing secret with OneTimeSecret: {e}")
            return None
