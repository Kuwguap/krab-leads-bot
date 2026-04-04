"""Supabase wrapper for Paper Investigator — shares krableads DB."""
import logging
from datetime import datetime, timezone
from supabase import create_client, Client
from config import Config
from typing import Optional

logger = logging.getLogger(__name__)


class PaperDB:
    def __init__(self):
        self.client: Client = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)

    # ── Drivers (reads from krableads shared table) ──────────────────────

    def get_all_drivers(self) -> list:
        try:
            r = self.client.table("drivers").select("*").execute()
            return r.data or []
        except Exception as e:
            logger.error("get_all_drivers: %s", e)
            return []

    def get_driver_by_id(self, driver_id: str) -> Optional[dict]:
        try:
            r = self.client.table("drivers").select("*").eq("id", driver_id).limit(1).execute()
            return r.data[0] if r.data else None
        except Exception as e:
            logger.error("get_driver_by_id: %s", e)
            return None

    # ── Driver addresses ─────────────────────────────────────────────────

    def get_driver_address(self, driver_id: str) -> Optional[dict]:
        try:
            r = self.client.table("driver_addresses").select("*").eq("driver_id", driver_id).limit(1).execute()
            return r.data[0] if r.data else None
        except Exception as e:
            logger.error("get_driver_address: %s", e)
            return None

    def set_driver_address(self, driver_id: str, address_line: str, city: str = "", state: str = "", zip_code: str = "") -> bool:
        try:
            existing = self.get_driver_address(driver_id)
            data = {"address_line": address_line, "city": city, "state": state, "zip_code": zip_code}
            if existing:
                self.client.table("driver_addresses").update(data).eq("driver_id", driver_id).execute()
            else:
                data["driver_id"] = driver_id
                self.client.table("driver_addresses").insert(data).execute()
            return True
        except Exception as e:
            logger.error("set_driver_address: %s", e)
            return False

    def get_all_driver_addresses(self) -> list:
        try:
            r = self.client.table("driver_addresses").select("*").execute()
            return r.data or []
        except Exception as e:
            logger.error("get_all_driver_addresses: %s", e)
            return []

    # ── Paper inventory ──────────────────────────────────────────────────

    def get_paper_count(self, driver_id: str) -> int:
        try:
            r = self.client.table("paper_inventory").select("current_count").eq("driver_id", driver_id).limit(1).execute()
            return r.data[0]["current_count"] if r.data else 0
        except Exception:
            return 0

    def _ensure_inventory(self, driver_id: str) -> None:
        existing = self.client.table("paper_inventory").select("id").eq("driver_id", driver_id).limit(1).execute()
        if not existing.data:
            self.client.table("paper_inventory").insert({"driver_id": driver_id, "current_count": 0}).execute()

    def add_paper(self, driver_id: str, amount: int, created_by: int, note: str = "") -> int:
        """Add papers to a driver. Returns new balance."""
        try:
            self._ensure_inventory(driver_id)
            current = self.get_paper_count(driver_id)
            new_balance = current + amount
            self.client.table("paper_inventory").update({
                "current_count": new_balance,
                "low_alert_sent": False,
            }).eq("driver_id", driver_id).execute()
            self.client.table("paper_transactions").insert({
                "driver_id": driver_id,
                "type": "add",
                "amount": amount,
                "balance_after": new_balance,
                "note": note,
                "created_by": created_by,
            }).execute()
            return new_balance
        except Exception as e:
            logger.error("add_paper: %s", e)
            return -1

    def subtract_paper(self, driver_id: str, amount: int, reference_id: str = "", note: str = "", created_by: int = 0) -> int:
        """Subtract papers from a driver (order accepted). Returns new balance, or -1 on error."""
        try:
            self._ensure_inventory(driver_id)
            current = self.get_paper_count(driver_id)
            new_balance = max(0, current - amount)
            self.client.table("paper_inventory").update({
                "current_count": new_balance,
            }).eq("driver_id", driver_id).execute()
            self.client.table("paper_transactions").insert({
                "driver_id": driver_id,
                "type": "subtract_order",
                "amount": -amount,
                "balance_after": new_balance,
                "reference_id": reference_id or None,
                "note": note,
                "created_by": created_by or None,
            }).execute()
            return new_balance
        except Exception as e:
            logger.error("subtract_paper: %s", e)
            return -1

    def get_all_inventory(self) -> list:
        """All drivers with their paper counts. Returns list of dicts."""
        try:
            drivers = self.get_all_drivers()
            out = []
            for d in drivers:
                if not d.get("is_active", True):
                    continue
                did = d["id"]
                count = self.get_paper_count(did)
                addr = self.get_driver_address(did)
                out.append({
                    "driver_id": did,
                    "driver_name": d.get("driver_name", "?"),
                    "driver_telegram_id": d.get("driver_telegram_id"),
                    "current_count": count,
                    "address": addr,
                })
            return out
        except Exception as e:
            logger.error("get_all_inventory: %s", e)
            return []

    def is_low_paper(self, driver_id: str) -> bool:
        return self.get_paper_count(driver_id) < Config.LOW_PAPER_THRESHOLD

    def was_low_alert_sent(self, driver_id: str) -> bool:
        try:
            r = self.client.table("paper_inventory").select("low_alert_sent").eq("driver_id", driver_id).limit(1).execute()
            return bool(r.data and r.data[0].get("low_alert_sent"))
        except Exception:
            return False

    def mark_low_alert_sent(self, driver_id: str) -> None:
        try:
            self.client.table("paper_inventory").update({"low_alert_sent": True}).eq("driver_id", driver_id).execute()
        except Exception:
            pass

    # ── Paper transactions (history) ─────────────────────────────────────

    def get_driver_history(self, driver_id: str, limit: int = 50) -> list:
        try:
            r = (self.client.table("paper_transactions")
                 .select("*")
                 .eq("driver_id", driver_id)
                 .order("created_at", desc=True)
                 .limit(limit)
                 .execute())
            return r.data or []
        except Exception as e:
            logger.error("get_driver_history: %s", e)
            return []

    def get_recent_transactions(self, limit: int = 50) -> list:
        try:
            r = (self.client.table("paper_transactions")
                 .select("*, driver:drivers(driver_name)")
                 .order("created_at", desc=True)
                 .limit(limit)
                 .execute())
            return r.data or []
        except Exception as e:
            logger.error("get_recent_transactions: %s", e)
            return []

    # ── Delivery orders ──────────────────────────────────────────────────

    def create_delivery_order(self, driver_id: str, quantity: int) -> Optional[dict]:
        try:
            r = self.client.table("paper_delivery_orders").insert({
                "driver_id": driver_id,
                "quantity": quantity,
                "status": "pending_approval",
            }).execute()
            return r.data[0] if r.data else None
        except Exception as e:
            logger.error("create_delivery_order: %s", e)
            return None

    def approve_delivery_order(self, order_id: str) -> bool:
        try:
            self.client.table("paper_delivery_orders").update({
                "status": "approved",
                "approved_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", order_id).execute()
            return True
        except Exception as e:
            logger.error("approve_delivery_order: %s", e)
            return False

    def decline_delivery_order(self, order_id: str) -> bool:
        try:
            self.client.table("paper_delivery_orders").update({
                "status": "declined",
            }).eq("id", order_id).execute()
            return True
        except Exception as e:
            logger.error("decline_delivery_order: %s", e)
            return False

    def mark_order_delivered(self, order_id: str, receipt_url: str = "") -> bool:
        try:
            data = {
                "status": "delivered",
                "delivered_at": datetime.now(timezone.utc).isoformat(),
            }
            if receipt_url:
                data["receipt_image_url"] = receipt_url
            self.client.table("paper_delivery_orders").update(data).eq("id", order_id).execute()
            return True
        except Exception as e:
            logger.error("mark_order_delivered: %s", e)
            return False

    def get_pending_delivery_orders(self) -> list:
        try:
            r = (self.client.table("paper_delivery_orders")
                 .select("*, driver:drivers(driver_name, driver_telegram_id)")
                 .in_("status", ["approved"])
                 .order("created_at")
                 .execute())
            return r.data or []
        except Exception as e:
            logger.error("get_pending_delivery_orders: %s", e)
            return []

    def get_orders_needing_reminder(self) -> list:
        """Approved orders where delivery not yet confirmed and last reminder was > N hours ago."""
        try:
            from datetime import timedelta
            cutoff = (datetime.now(timezone.utc) - timedelta(hours=Config.RECEIPT_REMINDER_HOURS)).isoformat()
            r = (self.client.table("paper_delivery_orders")
                 .select("*, driver:drivers(driver_name)")
                 .eq("status", "approved")
                 .execute())
            out = []
            for row in (r.data or []):
                last = row.get("last_reminder_sent_at") or ""
                if not last or last < cutoff:
                    out.append(row)
            return out
        except Exception as e:
            logger.error("get_orders_needing_reminder: %s", e)
            return []

    def mark_reminder_sent(self, order_id: str) -> None:
        try:
            self.client.table("paper_delivery_orders").update({
                "last_reminder_sent_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", order_id).execute()
        except Exception:
            pass

    def update_order_receipt_verification(self, order_id: str, verified: bool, notes: str = "") -> None:
        try:
            self.client.table("paper_delivery_orders").update({
                "receipt_verified": verified,
                "receipt_verification_notes": notes,
            }).eq("id", order_id).execute()
        except Exception:
            pass

    # ── Auto-tracking: process new accepted krableads assignments ────────

    def get_unprocessed_accepted_assignments(self) -> list:
        """Find lead_assignments with status='accepted' not yet counted for paper."""
        try:
            r = (self.client.table("lead_assignments")
                 .select("id, driver_id, lead_id, accepted_at, lead:leads(reference_id)")
                 .eq("status", "accepted")
                 .execute())
            if not r.data:
                return []
            processed = set()
            try:
                pr = self.client.table("paper_processed_assignments").select("assignment_id").execute()
                processed = {row["assignment_id"] for row in (pr.data or [])}
            except Exception:
                pass
            return [a for a in r.data if a.get("id") not in processed]
        except Exception as e:
            logger.error("get_unprocessed_accepted_assignments: %s", e)
            return []

    def mark_assignment_processed(self, assignment_id: str, driver_id: str) -> None:
        try:
            self.client.table("paper_processed_assignments").insert({
                "assignment_id": assignment_id,
                "driver_id": driver_id,
            }).execute()
        except Exception:
            pass

    # ── Settings ─────────────────────────────────────────────────────────

    def get_setting(self, key: str) -> Optional[str]:
        try:
            r = self.client.table("paper_settings").select("value").eq("key", key).limit(1).execute()
            return r.data[0]["value"] if r.data else None
        except Exception:
            return None

    def set_setting(self, key: str, value: str) -> None:
        try:
            existing = self.get_setting(key)
            if existing is not None:
                self.client.table("paper_settings").update({"value": value}).eq("key", key).execute()
            else:
                self.client.table("paper_settings").insert({"key": key, "value": value}).execute()
        except Exception as e:
            logger.error("set_setting: %s", e)

    # ── Stats ────────────────────────────────────────────────────────────

    def get_usage_stats(self) -> dict:
        """Summary stats for supervisor dashboard."""
        try:
            inventory = self.get_all_inventory()
            total_papers = sum(d["current_count"] for d in inventory)
            total_drivers = len(inventory)
            low_drivers = [d for d in inventory if d["current_count"] < Config.LOW_PAPER_THRESHOLD]
            txns = self.get_recent_transactions(200)
            total_added = sum(t["amount"] for t in txns if t.get("type") == "add")
            total_used = sum(abs(t["amount"]) for t in txns if t.get("type") == "subtract_order")
            return {
                "total_papers_in_field": total_papers,
                "total_drivers": total_drivers,
                "low_paper_drivers": len(low_drivers),
                "total_added_recent": total_added,
                "total_used_recent": total_used,
                "drivers": inventory,
            }
        except Exception as e:
            logger.error("get_usage_stats: %s", e)
            return {}
