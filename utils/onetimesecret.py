"""OneTimeSecret API integration for encrypting phone numbers."""
import json
import logging
import requests
from config import Config
from typing import Optional, Dict

# #region agent log
_DBG_LOG = r"c:\Users\tatia\Downloads\krableads\.cursor\debug.log"
def _dbg(msg, data=None, hyp=""):
    import time; entry = {"timestamp": int(time.time()*1000), "location": "onetimesecret.py", "message": msg, "hypothesisId": hyp}
    if data: entry["data"] = data
    try:
        with open(_DBG_LOG, "a", encoding="utf-8") as f: f.write(json.dumps(entry) + "\n")
    except Exception: pass
# #endregion

logger = logging.getLogger(__name__)


def _normalize_share_url(url: str) -> str:
    """
    Use HTTPS for production hosts. HTTP→HTTPS redirects often become GET and drop the POST body,
    which breaks the share API (looks like 'missing secret' / 405).
    """
    u = (url or "").strip().lstrip("=").rstrip("/")
    if u.startswith("http://") and "localhost" not in u and "127.0.0.1" not in u:
        u = "https://" + u[len("http://") :]
    return u


def _normalize_link_base(base: str) -> str:
    b = (base or "").strip()
    if not b.endswith("/"):
        b = b + "/"
    return b


class OneTimeSecret:
    """OneTimeSecret API client."""
    
    def __init__(self):
        self.url = _normalize_share_url(Config.ONETIMESECRET_URL or "")
        self.username = (Config.ONETIMESECRET_USERNAME or "").strip()
        self.api_key = (Config.ONETIMESECRET_API_KEY or "").strip()
        self.passphrase = (Config.ONETIMESECRET_PASSPHRASE or "").strip()
        self.link_base = _normalize_link_base(
            getattr(Config, "ONETIMESECRET_LINK_BASE", None) or "https://clientsphonenumber.com/secret/"
        )
        # Human-readable failure reason from last call (safe to show to admins/users).
        self.last_error: str = ""
    
    def _post_share(self, secret: str) -> Optional[Dict]:
        # #region agent log
        _dbg("_post_share entry", {"url": self.url, "has_user": bool(self.username), "has_key": bool(self.api_key), "key_prefix": (self.api_key or "")[:8]}, "H1")
        # #endregion
        if not self.url or not self.username or not self.api_key:
            self.last_error = "Encryption service not configured (missing ONETIMESECRET_URL/USERNAME/API_KEY)"
            # #region agent log
            _dbg("MISSING CONFIG", {"url": bool(self.url), "user": bool(self.username), "key": bool(self.api_key)}, "H1")
            # #endregion
            logger.error("OneTimeSecret: missing URL, username, or API key in config")
            return None
        secret = (secret or "").strip()
        if not secret:
            self.last_error = "Empty phone number"
            logger.error("OneTimeSecret: empty secret (phone)")
            return None
        try:
            response = requests.post(
                self.url,
                auth=(self.username, self.api_key),
                data={
                    "secret": secret,
                    "passphrase": self.passphrase,
                    "ttl": "2592000",  # 30 days (string matches form APIs)
                },
                headers={"User-Agent": "KrabsLeads-Bot/1.0"},
                timeout=15,
            )
            # #region agent log
            _dbg("HTTP response", {"status": response.status_code, "body_preview": (response.text or "")[:200]}, "H1")
            # #endregion
            if response.status_code != 200:
                preview = (response.text or "").strip().replace("\n", " ")
                preview = preview[:200] if preview else "(no body)"
                # Don't include secrets; only include status + server message preview.
                self.last_error = f"Encryption service HTTP {response.status_code}: {preview}"
                logger.warning(
                    "OneTimeSecret API HTTP %s: %s",
                    response.status_code,
                    (response.text or "")[:500],
                )
                return None
            data = response.json()
            sk = data.get("secret_key")
            mk = data.get("metadata_key")
            if not sk or not mk:
                self.last_error = "Encryption service returned malformed response (missing keys)"
                logger.warning("OneTimeSecret API 200 but missing secret_key/metadata_key: %s", data)
                return None
            self.last_error = ""
            return {
                "secret_key": sk,
                "metadata_key": mk,
                "link": f"{self.link_base}{sk}",
            }
        except requests.RequestException as e:
            self.last_error = f"Encryption service request failed: {e}"
            logger.warning("OneTimeSecret request failed: %s", e)
            return None
        except Exception as e:
            self.last_error = f"Encryption service error: {e}"
            logger.warning("OneTimeSecret error: %s", e)
            return None

    def encrypt_phone(self, phone_number: str) -> Optional[Dict[str, str]]:
        """
        Encrypt phone number using OneTimeSecret-compatible share API.

        Returns:
            Dict with 'secret_key', 'metadata_key', 'link', or None on error
        """
        return self._post_share(phone_number)

    def share_secret(self, secret: str) -> Optional[str]:
        """
        Store any secret in OneTimeSecret and return the one-time link.
        Use for redacting phone numbers (or other sensitive text) from messages.
        """
        out = self._post_share(secret.strip())
        return out["link"] if out else None
