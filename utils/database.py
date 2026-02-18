"""Database utilities for Supabase integration."""
import logging
from supabase import create_client, Client
from config import Config
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)


class Database:
    """Supabase database client wrapper."""
    
    def __init__(self):
        self.client: Client = create_client(Config.SUPABASE_URL, Config.SUPABASE_KEY)
        self._tables_checked = False
        self._tables_exist = False
        self._error_logged = False
    
    def _check_tables_exist(self) -> bool:
        """Check if required tables exist in the database."""
        if self._tables_checked:
            return self._tables_exist
        
        try:
            # Try a simple query to check if tables exist
            self.client.table("states").select("user_id").limit(1).execute()
            self._tables_checked = True
            self._tables_exist = True
            return True
        except Exception as e:
            error_msg = str(e)
            if ("Could not find the table" in error_msg or "PGRST205" in error_msg) and not self._error_logged:
                logger.error(
                    "\n" + "="*60 + "\n"
                    "DATABASE ERROR: Tables not found!\n\n"
                    "Please run the SQL schema from 'database/schema.sql' in your Supabase SQL Editor.\n"
                    "Go to: https://supabase.com/dashboard -> Your Project -> SQL Editor\n"
                    "="*60
                )
                self._error_logged = True
            self._tables_checked = True
            self._tables_exist = False
            return False
    
    def get_user_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get the current state for a user."""
        if not self._check_tables_exist():
            return None
        
        try:
            response = self.client.table("states").select("*").eq("user_id", user_id).execute()
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            error_msg = str(e)
            if "Could not find the table" not in error_msg and "PGRST205" not in error_msg:
                logger.error(f"Error getting user state: {e}")
            return None
    
    def set_user_state(self, user_id: int, state: str, data: Optional[Dict[str, Any]] = None) -> bool:
        """Set or update the state for a user."""
        if not self._check_tables_exist():
            return False
        
        try:
            state_data = {
                "user_id": user_id,
                "state": state,
                "data": data or {}
            }
            
            # Try to update first
            existing = self.get_user_state(user_id)
            if existing:
                self.client.table("states").update(state_data).eq("user_id", user_id).execute()
            else:
                self.client.table("states").insert(state_data).execute()
            
            return True
        except Exception as e:
            error_msg = str(e)
            if "Could not find the table" not in error_msg and "PGRST205" not in error_msg:
                logger.error(f"Error setting user state: {e}")
            return False
    
    def clear_user_state(self, user_id: int) -> bool:
        """Clear the state for a user."""
        if not self._check_tables_exist():
            return False
        
        try:
            self.client.table("states").delete().eq("user_id", user_id).execute()
            return True
        except Exception as e:
            error_msg = str(e)
            if "Could not find the table" not in error_msg and "PGRST205" not in error_msg:
                logger.error(f"Error clearing user state: {e}")
            return False
    
    def create_lead(self, lead_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Create a new lead record."""
        if not self._check_tables_exist():
            return None
        
        try:
            response = self.client.table("leads").insert(lead_data).execute()
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            error_msg = str(e)
            if "Could not find the table" not in error_msg and "PGRST205" not in error_msg:
                logger.error(f"Error creating lead: {e}")
            return None
    
    def update_lead(self, lead_id: str, updates: Dict[str, Any]) -> bool:
        """Update a lead record."""
        if not self._check_tables_exist():
            return False
        
        try:
            self.client.table("leads").update(updates).eq("id", lead_id).execute()
            return True
        except Exception as e:
            error_msg = str(e)
            if "Could not find the table" not in error_msg and "PGRST205" not in error_msg:
                logger.error(f"Error updating lead: {e}")
            return False
    
    def get_lead_by_id(self, lead_id: str) -> Optional[Dict[str, Any]]:
        """Get a lead by ID."""
        if not self._check_tables_exist():
            return None
        
        try:
            response = self.client.table("leads").select("*").eq("id", lead_id).execute()
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            error_msg = str(e)
            if "Could not find the table" not in error_msg and "PGRST205" not in error_msg:
                logger.error(f"Error getting lead by ID: {e}")
            return None
    
    def get_lead_by_monday_id(self, monday_item_id: int) -> Optional[Dict[str, Any]]:
        """Get a lead by Monday.com item ID."""
        if not self._check_tables_exist():
            return None
        
        try:
            response = self.client.table("leads").select("*").eq("monday_item_id", monday_item_id).execute()
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            error_msg = str(e)
            if "Could not find the table" not in error_msg and "PGRST205" not in error_msg:
                logger.error(f"Error getting lead by Monday ID: {e}")
            return None
    
    def get_lead_by_reference_id(self, reference_id: str) -> Optional[Dict[str, Any]]:
        """Get a lead by reference ID."""
        if not self._check_tables_exist():
            return None
        
        try:
            response = self.client.table("leads").select("*").eq("reference_id", reference_id).execute()
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            error_msg = str(e)
            if "Could not find the table" not in error_msg and "PGRST205" not in error_msg:
                logger.error(f"Error getting lead by reference ID: {e}")
            return None
    
    def update_lead_receipt(self, lead_id: str, receipt_image_url: str) -> bool:
        """Update lead with receipt image URL and set status to Paid."""
        if not self._check_tables_exist():
            return False
        
        try:
            self.client.table("leads").update({
                "receipt_image_url": receipt_image_url,
                "monday_status": "Paid"
            }).eq("id", lead_id).execute()
            return True
        except Exception as e:
            error_msg = str(e)
            if "Could not find the table" not in error_msg and "PGRST205" not in error_msg:
                logger.error(f"Error updating lead receipt: {e}")
            return False
    
    # Group management methods
    def create_group(self, group_name: str, group_telegram_id: str, supervisory_telegram_id: str) -> bool:
        """Create a new group."""
        if not self._check_tables_exist():
            return False
        
        try:
            self.client.table("groups").insert({
                "group_name": group_name,
                "group_telegram_id": group_telegram_id,
                "supervisory_telegram_id": supervisory_telegram_id
            }).execute()
            return True
        except Exception as e:
            logger.error(f"Error creating group: {e}")
            return False
    
    def get_all_groups(self) -> list:
        """Get all groups."""
        if not self._check_tables_exist():
            return []
        
        try:
            response = self.client.table("groups").select("*").order("group_name").execute()
            return response.data or []
        except Exception as e:
            logger.error(f"Error getting groups: {e}")
            return []
    
    def get_group_by_id(self, group_id: str) -> Optional[Dict[str, Any]]:
        """Get a group by ID."""
        if not self._check_tables_exist():
            return None
        
        try:
            response = self.client.table("groups").select("*").eq("id", group_id).execute()
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            logger.error(f"Error getting group: {e}")
            return None
    
    def toggle_group_status(self, group_id: str) -> bool:
        """Toggle group active status."""
        if not self._check_tables_exist():
            return False
        
        try:
            group = self.get_group_by_id(group_id)
            if group:
                new_status = not group.get('is_active', True)
                self.client.table("groups").update({"is_active": new_status}).eq("id", group_id).execute()
                return True
            return False
        except Exception as e:
            logger.error(f"Error toggling group status: {e}")
            return False
    
    # Group assistants: Telegram IDs that send leads into this group
    def get_group_by_assistant_telegram_id(self, telegram_id: str) -> Optional[Dict[str, Any]]:
        """Return the group that has this telegram_id as an assistant (first if multiple)."""
        if not self._check_tables_exist():
            return None
        try:
            r = self.client.table("group_assistants").select("group_id").eq("telegram_id", str(telegram_id)).limit(1).execute()
            if not r.data:
                return None
            return self.get_group_by_id(r.data[0]["group_id"])
        except Exception as e:
            logger.error(f"Error getting group by assistant: {e}")
            return None

    def get_group_assistants(self, group_id: str) -> list:
        """Return list of {telegram_id} for assistants assigned to this group."""
        if not self._check_tables_exist():
            return []
        try:
            r = self.client.table("group_assistants").select("telegram_id").eq("group_id", group_id).execute()
            return [x["telegram_id"] for x in (r.data or [])]
        except Exception as e:
            logger.error(f"Error getting group assistants: {e}")
            return []

    def add_group_assistant(self, group_id: str, telegram_id: str) -> bool:
        """Assign an assistant (Telegram user ID) to a group."""
        if not self._check_tables_exist():
            return False
        try:
            self.client.table("group_assistants").insert({"group_id": group_id, "telegram_id": str(telegram_id).strip()}).execute()
            return True
        except Exception as e:
            logger.error(f"Error adding group assistant: {e}")
            return False

    def remove_group_assistant(self, group_id: str, telegram_id: str) -> bool:
        """Remove an assistant from a group."""
        if not self._check_tables_exist():
            return False
        try:
            self.client.table("group_assistants").delete().eq("group_id", group_id).eq("telegram_id", str(telegram_id).strip()).execute()
            return True
        except Exception as e:
            logger.error(f"Error removing group assistant: {e}")
            return False

    # Settings (e.g. assistants_choose_group)
    def get_setting(self, key: str) -> Optional[str]:
        """Get a setting value by key. Returns None if not found or table missing."""
        if not self._check_tables_exist():
            return None
        try:
            r = self.client.table("settings").select("value").eq("key", key).limit(1).execute()
            if r.data and len(r.data) > 0:
                return (r.data[0].get("value") or "").strip()
            return None
        except Exception as e:
            logger.error(f"Error getting setting {key}: {e}")
            return None

    def set_setting(self, key: str, value: str) -> bool:
        """Set a setting value. Upserts."""
        if not self._check_tables_exist():
            return False
        try:
            self.client.table("settings").upsert({"key": key, "value": str(value)}, on_conflict="key").execute()
            return True
        except Exception as e:
            logger.error(f"Error setting {key}: {e}")
            return False

    # Driver management methods
    def create_driver(self, driver_name: str, driver_telegram_id: str, phone_number: Optional[str] = None) -> bool:
        """Create a new driver."""
        if not self._check_tables_exist():
            return False
        
        try:
            self.client.table("drivers").insert({
                "driver_name": driver_name,
                "driver_telegram_id": driver_telegram_id,
                "phone_number": phone_number
            }).execute()
            return True
        except Exception as e:
            logger.error(f"Error creating driver: {e}")
            return False
    
    def get_all_drivers(self) -> list:
        """Get all drivers."""
        if not self._check_tables_exist():
            return []
        
        try:
            response = self.client.table("drivers").select("*").order("driver_name").execute()
            return response.data or []
        except Exception as e:
            logger.error(f"Error getting drivers: {e}")
            return []
    
    def get_active_drivers_for_group(self, group_id: str) -> list:
        """Get all active drivers for a specific group."""
        if not self._check_tables_exist():
            return []
        
        try:
            response = self.client.table("group_drivers").select(
                "driver:drivers(*)"
            ).eq("group_id", group_id).execute()
            
            drivers = []
            for item in response.data or []:
                driver = item.get('driver')
                if driver and driver.get('is_active', True):
                    drivers.append(driver)
            return drivers
        except Exception as e:
            logger.error(f"Error getting drivers for group: {e}")
            return []
    
    def toggle_driver_status(self, driver_id: str) -> bool:
        """Toggle driver active status."""
        if not self._check_tables_exist():
            return False
        
        try:
            driver = self.client.table("drivers").select("*").eq("id", driver_id).execute()
            if driver.data:
                current_status = driver.data[0].get('is_active', True)
                new_status = not current_status
                self.client.table("drivers").update({"is_active": new_status}).eq("id", driver_id).execute()
                return True
            return False
        except Exception as e:
            logger.error(f"Error toggling driver status: {e}")
            return False
    
    # Assignment methods
    def assign_driver_to_group(self, group_id: str, driver_id: str) -> bool:
        """Assign a driver to a group."""
        if not self._check_tables_exist():
            return False
        
        try:
            self.client.table("group_drivers").insert({
                "group_id": group_id,
                "driver_id": driver_id
            }).execute()
            return True
        except Exception as e:
            logger.error(f"Error assigning driver to group: {e}")
            return False
    
    def get_all_assignments(self) -> list:
        """Get all group-driver assignments with names."""
        if not self._check_tables_exist():
            return []
        
        try:
            response = self.client.table("group_drivers").select(
                "id, group:groups(group_name), driver:drivers(driver_name)"
            ).execute()
            
            assignments = []
            for item in response.data or []:
                assignments.append({
                    "id": item.get("id"),
                    "group_name": item.get("group", {}).get("group_name", "N/A"),
                    "driver_name": item.get("driver", {}).get("driver_name", "N/A")
                })
            return assignments
        except Exception as e:
            logger.error(f"Error getting assignments: {e}")
            return []
    
    def remove_driver_from_group(self, assignment_id: str) -> bool:
        """Remove a driver from a group."""
        if not self._check_tables_exist():
            return False
        
        try:
            self.client.table("group_drivers").delete().eq("id", assignment_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error removing driver from group: {e}")
            return False
    
    # Lead assignment methods
    def create_lead_assignment(self, lead_id: str, driver_id: str, group_id: str) -> bool:
        """Create a lead assignment (when sent to driver)."""
        if not self._check_tables_exist():
            return False
        
        try:
            self.client.table("lead_assignments").insert({
                "lead_id": lead_id,
                "driver_id": driver_id,
                "group_id": group_id,
                "status": "pending"
            }).execute()
            return True
        except Exception as e:
            logger.error(f"Error creating lead assignment: {e}")
            return False
    
    def accept_lead_assignment(self, lead_id: str, driver_id: str) -> bool:
        """Accept a lead assignment (first driver to accept)."""
        if not self._check_tables_exist():
            return False
        
        try:
            # Check if lead is already accepted
            existing = self.client.table("lead_assignments").select("*").eq(
                "lead_id", lead_id
            ).eq("status", "accepted").execute()
            
            if existing.data:
                return False  # Already accepted by someone else
            
            # Update this driver's assignment to accepted
            self.client.table("lead_assignments").update({
                "status": "accepted",
                "accepted_at": "now()"
            }).eq("lead_id", lead_id).eq("driver_id", driver_id).execute()
            
            # Decline all other pending assignments for this lead
            self.client.table("lead_assignments").update({
                "status": "declined"
            }).eq("lead_id", lead_id).eq("status", "pending").neq("driver_id", driver_id).execute()
            
            return True
        except Exception as e:
            logger.error(f"Error accepting lead assignment: {e}")
            return False
    
    def decline_lead_assignment(self, lead_id: str, driver_id: str) -> bool:
        """Decline a lead assignment."""
        if not self._check_tables_exist():
            return False
        
        try:
            self.client.table("lead_assignments").update({
                "status": "declined"
            }).eq("lead_id", lead_id).eq("driver_id", driver_id).execute()
            return True
        except Exception as e:
            logger.error(f"Error declining lead assignment: {e}")
            return False
    
    def get_lead_assignment_status(self, lead_id: str) -> Optional[Dict[str, Any]]:
        """Get the accepted assignment for a lead."""
        if not self._check_tables_exist():
            return None
        
        try:
            response = self.client.table("lead_assignments").select(
                "*, driver:drivers(*)"
            ).eq("lead_id", lead_id).eq("status", "accepted").execute()
            
            if response.data:
                return response.data[0]
            return None
        except Exception as e:
            logger.error(f"Error getting lead assignment status: {e}")
            return None

