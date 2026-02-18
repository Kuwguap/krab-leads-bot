"""Simple Flask admin dashboard for managing groups, drivers, and supervisory IDs."""
from flask import Flask, render_template_string, request, jsonify, redirect, url_for
from flask_cors import CORS
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load .env file explicitly (admin dashboard doesn't need Telegram bot token)
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

# Import Supabase directly - admin dashboard only needs Supabase, NOT Telegram
from supabase import create_client, Client

app = Flask(__name__)
# CORS: allow frontend from any origin (Vercel, localhost) so preflight and responses always have headers
CORS(app, resources={r"/api/*": {"origins": "*", "allow_headers": ["Content-Type", "Authorization"], "methods": ["GET", "POST", "PUT", "DELETE", "OPTIONS"]}})


@app.after_request
def add_cors_headers(response):
    """Ensure every response (including errors) has CORS headers so browser never blocks."""
    if request.path.startswith("/api/"):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
        response.headers["Access-Control-Max-Age"] = "86400"
    return response


@app.route("/api/<path:path>", methods=["OPTIONS"])
def api_options(path):
    """Handle CORS preflight for all /api/* so browser always gets CORS headers."""
    return "", 204


@app.errorhandler(Exception)
def handle_exception(e):
    """Catch unhandled exceptions and return JSON (so frontend always gets error message)."""
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return e
    return jsonify({"success": False, "error": str(e)}), 500

# Create Supabase client directly (bypass Config to avoid Telegram dependencies)
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in environment variables")

# Create a minimal database wrapper for admin dashboard
class AdminDatabase:
    """Minimal database wrapper for admin dashboard (no Telegram dependencies)."""
    def __init__(self):
        self.client: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self._tables_checked = False
        self._tables_exist = False
    
    def _check_tables_exist(self) -> bool:
        """Check if required tables exist."""
        if self._tables_checked:
            return self._tables_exist
        try:
            self.client.table("groups").select("id").limit(1).execute()
            self._tables_checked = True
            self._tables_exist = True
            return True
        except Exception:
            self._tables_checked = True
            self._tables_exist = False
            return False
    
    def get_all_groups(self) -> list:
        """Get all groups."""
        if not self._check_tables_exist():
            return []
        try:
            response = self.client.table("groups").select("*").order("group_name").execute()
            return response.data or []
        except Exception:
            return []
    
    def get_all_drivers(self) -> list:
        """Get all drivers."""
        if not self._check_tables_exist():
            return []
        try:
            response = self.client.table("drivers").select("*").order("driver_name").execute()
            return response.data or []
        except Exception:
            return []
    
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
        except Exception:
            return False
    
    def create_driver(self, driver_name: str, driver_telegram_id: str, phone_number: str = None) -> bool:
        """Create a new driver. Raises on failure (table missing or insert error)."""
        if not self._check_tables_exist():
            raise ValueError("Database tables not found. Run the schema migrations in Supabase SQL Editor.")
        payload = {
            "driver_name": driver_name,
            "driver_telegram_id": str(driver_telegram_id).strip(),
        }
        if phone_number is not None and str(phone_number).strip():
            payload["phone_number"] = str(phone_number).strip()
        self.client.table("drivers").insert(payload).execute()
        return True
    
    def get_group_by_id(self, group_id: str):
        """Get a group by ID."""
        if not self._check_tables_exist():
            return None
        try:
            response = self.client.table("groups").select("*").eq("id", group_id).execute()
            return response.data[0] if response.data else None
        except Exception:
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
        except Exception:
            return False
    
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
        except Exception:
            return False

    # Group assistants (Telegram IDs that send leads into this group)
    def get_group_assistants(self, group_id: str) -> list:
        """Return list of telegram_id strings for assistants assigned to this group."""
        if not self._check_tables_exist():
            return []
        try:
            r = self.client.table("group_assistants").select("telegram_id").eq("group_id", group_id).execute()
            return [x["telegram_id"] for x in (r.data or [])]
        except Exception:
            return []

    def add_group_assistant(self, group_id: str, telegram_id: str) -> bool:
        """Assign an assistant (Telegram user ID) to a group."""
        if not self._check_tables_exist():
            return False
        try:
            self.client.table("group_assistants").insert({"group_id": group_id, "telegram_id": str(telegram_id).strip()}).execute()
            return True
        except Exception:
            return False

    def remove_group_assistant(self, group_id: str, telegram_id: str) -> bool:
        """Remove an assistant from a group."""
        if not self._check_tables_exist():
            return False
        try:
            self.client.table("group_assistants").delete().eq("group_id", group_id).eq("telegram_id", str(telegram_id).strip()).execute()
            return True
        except Exception:
            return False

    # Settings
    def get_setting(self, key: str) -> str:
        """Get setting value; returns '' if missing or table missing."""
        if not self._check_tables_exist():
            return ""
        try:
            r = self.client.table("settings").select("value").eq("key", key).limit(1).execute()
            if r.data and len(r.data) > 0:
                return (r.data[0].get("value") or "").strip()
            return ""
        except Exception:
            return ""

    def set_setting(self, key: str, value: str) -> bool:
        """Set setting (upsert)."""
        if not self._check_tables_exist():
            return False
        try:
            self.client.table("settings").upsert({"key": key, "value": str(value)}, on_conflict="key").execute()
            return True
        except Exception:
            return False

    # Lead stats: total leads, per-driver accepted and receipts
    def get_lead_stats(self) -> dict:
        """Return { total_leads: int, drivers: [ { driver_id, driver_name, leads_accepted, receipts_submitted } ] }."""
        out = {"total_leads": 0, "drivers": []}
        if not self._check_tables_exist():
            return out
        try:
            r = self.client.table("leads").select("id").execute()
            out["total_leads"] = len(r.data or [])
        except Exception:
            pass
        try:
            drivers = self.client.table("drivers").select("id, driver_name").execute()
            assignments = self.client.table("lead_assignments").select("driver_id, lead_id").eq("status", "accepted").execute()
            lead_ids_with_receipt = set()
            try:
                leads = self.client.table("leads").select("id, receipt_image_url").execute()
                lead_ids_with_receipt = {l["id"] for l in (leads.data or []) if l.get("receipt_image_url")}
            except Exception:
                pass
            by_driver = {}
            for a in (assignments.data or []):
                did = a.get("driver_id")
                lid = a.get("lead_id")
                if did not in by_driver:
                    by_driver[did] = {"accepted": 0, "receipts": 0}
                by_driver[did]["accepted"] += 1
                if lid and lid in lead_ids_with_receipt:
                    by_driver[did]["receipts"] += 1
            for d in (drivers.data or []):
                did = d.get("id")
                out["drivers"].append({
                    "driver_id": did,
                    "driver_name": d.get("driver_name", "N/A"),
                    "leads_accepted": by_driver.get(did, {}).get("accepted", 0),
                    "receipts_submitted": by_driver.get(did, {}).get("receipts", 0),
                })
        except Exception:
            pass
        return out

db = AdminDatabase()

# Simple HTML template for the dashboard
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
    <title>KrabsLeads Admin Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
            -webkit-text-size-adjust: 100%;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 30px;
        }
        h1 {
            color: #333;
            margin-bottom: 30px;
            text-align: center;
            font-size: clamp(1.25rem, 4vw, 1.75rem);
        }
        .section {
            margin-bottom: 40px;
            padding: 20px;
            background: #f8f9fa;
            border-radius: 8px;
        }
        .section h2 {
            color: #667eea;
            margin-bottom: 20px;
            border-bottom: 2px solid #667eea;
            padding-bottom: 10px;
            font-size: clamp(1rem, 3vw, 1.25rem);
        }
        .form-group {
            margin-bottom: 15px;
        }
        label {
            display: block;
            margin-bottom: 5px;
            color: #555;
            font-weight: 500;
        }
        input, select {
            width: 100%;
            padding: 12px 10px;
            border: 2px solid #ddd;
            border-radius: 6px;
            font-size: 16px;
            transition: border-color 0.3s;
        }
        input:focus, select:focus {
            outline: none;
            border-color: #667eea;
        }
        button {
            background: #667eea;
            color: white;
            padding: 12px 24px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 14px;
            font-weight: 500;
            transition: background 0.3s;
            min-height: 44px;
        }
        button:hover {
            background: #5568d3;
        }
        .btn-danger {
            background: #dc3545;
        }
        .btn-danger:hover {
            background: #c82333;
        }
        .table-wrapper {
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            margin-top: 15px;
        }
        .table-wrapper table {
            min-width: 320px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
        }
        th, td {
            padding: 12px 8px;
            text-align: left;
            border-bottom: 1px solid #ddd;
            font-size: clamp(0.8125rem, 2.5vw, 0.9375rem);
        }
        th {
            background: #667eea;
            color: white;
            font-weight: 600;
        }
        tr:hover {
            background: #f5f5f5;
        }
        .status-active {
            color: #28a745;
            font-weight: 600;
        }
        .status-inactive {
            color: #dc3545;
            font-weight: 600;
        }
        .message {
            padding: 12px;
            border-radius: 6px;
            margin-bottom: 20px;
        }
        .message-success {
            background: #d4edda;
            color: #155724;
            border: 1px solid #c3e6cb;
        }
        .message-error {
            background: #f8d7da;
            color: #721c24;
            border: 1px solid #f5c6cb;
        }
        .assistant-form-row {
            display: flex;
            gap: 8px;
            align-items: center;
            flex-wrap: wrap;
        }
        .assistant-form-row input {
            flex: 1;
            min-width: 0;
        }
        .assistant-form-row button {
            flex-shrink: 0;
        }
        @media (max-width: 768px) {
            body { padding: 10px; }
            .container { padding: 16px; border-radius: 8px; }
            .section { padding: 14px; margin-bottom: 24px; }
            .section h2 { margin-bottom: 14px; }
            th, td { padding: 10px 6px; }
            button { width: 100%; min-height: 48px; }
            .assistant-form-row { flex-direction: column; align-items: stretch; }
            .assistant-form-row button { width: 100%; }
        }
        @media (max-width: 480px) {
            h1 { margin-bottom: 20px; }
            .section h2 { font-size: 1rem; }
        }
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 KrabsLeads Admin Dashboard</h1>
        
        {% if message %}
        <div class="message message-{{ message_type }}">{{ message }}</div>
        {% endif %}
        
        <!-- Settings: Allow assistants to choose group -->
        <div class="section">
            <h2>⚙️ Lead flow</h2>
            <p style="margin-bottom: 10px; color: #555;">When <strong>Allow assistants to choose group</strong> is ON, anyone can send leads and will choose a group (and then a driver). When OFF, assistants use their assigned group.</p>
            <p style="margin-bottom: 12px;"><strong>Current:</strong> {{ 'Allow assistants to choose group' if assistants_choose_group else 'Use assigned groups only' }}</p>
            <form method="POST" action="/set_assistants_choose_group" style="display: inline;">
                <input type="hidden" name="value" value="{{ '0' if assistants_choose_group else '1' }}">
                <button type="submit" style="padding: 8px 16px;">{{ 'Use assigned groups only' if assistants_choose_group else 'Allow assistants to choose group' }}</button>
            </form>
        </div>
        
        <!-- Lead stats -->
        <div class="section">
            <h2>📊 Lead stats</h2>
            <p style="margin-bottom: 12px;"><strong>Total leads sent:</strong> {{ lead_stats.get('total_leads', 0) }}</p>
            <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Driver</th>
                        <th>Leads accepted</th>
                        <th>Receipts submitted</th>
                    </tr>
                </thead>
                <tbody>
                    {% for d in (lead_stats.get('drivers') or []) %}
                    <tr>
                        <td>{{ d.driver_name }}</td>
                        <td>{{ d.leads_accepted }}</td>
                        <td>{{ d.receipts_submitted }}</td>
                    </tr>
                    {% endfor %}
                    {% if not (lead_stats.get('drivers') or []) %}
                    <tr><td colspan="3" style="text-align: center; color: #888;">No drivers</td></tr>
                    {% endif %}
                </tbody>
            </table>
            </div>
        </div>
        
        <!-- Add Group Section -->
        <div class="section">
            <h2>➕ Add New Group</h2>
            <form method="POST" action="/add_group">
                <div class="form-group">
                    <label>Group Name:</label>
                    <input type="text" name="group_name" required placeholder="e.g., Group A">
                </div>
                <div class="form-group">
                    <label>Group Telegram ID:</label>
                    <input type="text" name="group_telegram_id" required placeholder="e.g., -1001234567890">
                </div>
                <div class="form-group">
                    <label>Supervisory Telegram ID:</label>
                    <input type="text" name="supervisory_telegram_id" required placeholder="e.g., 123456789">
                </div>
                <button type="submit">Add Group</button>
            </form>
        </div>
        
        <!-- Add Driver Section -->
        <div class="section">
            <h2>👤 Add New Driver</h2>
            <form method="POST" action="/add_driver">
                <div class="form-group">
                    <label>Driver Name:</label>
                    <input type="text" name="driver_name" required placeholder="e.g., John Doe">
                </div>
                <div class="form-group">
                    <label>Driver Telegram ID:</label>
                    <input type="text" name="driver_telegram_id" required placeholder="e.g., 123456789">
                </div>
                <div class="form-group">
                    <label>Phone Number (optional):</label>
                    <input type="text" name="phone_number" placeholder="e.g., +1234567890">
                </div>
                <button type="submit">Add Driver</button>
            </form>
        </div>
        
        <!-- Groups List -->
        <div class="section">
            <h2>📋 Groups</h2>
            <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Name</th>
                        <th>Group ID</th>
                        <th>Supervisory ID</th>
                        <th>Status</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {% for group in groups %}
                    <tr>
                        <td>{{ group.group_name }}</td>
                        <td><code>{{ group.group_telegram_id }}</code></td>
                        <td><code>{{ group.supervisory_telegram_id }}</code></td>
                        <td>
                            <span class="status-{{ 'active' if group.is_active else 'inactive' }}">
                                {{ 'Active' if group.is_active else 'Inactive' }}
                            </span>
                        </td>
                        <td>
                            <a href="/toggle_group/{{ group.id }}">
                                <button class="btn-danger" style="padding: 6px 12px; font-size: 12px;">
                                    {{ 'Deactivate' if group.is_active else 'Activate' }}
                                </button>
                            </a>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            </div>
        </div>
        
        <!-- Drivers List -->
        <div class="section">
            <h2>🚗 Drivers</h2>
            <div class="table-wrapper">
            <table>
                <thead>
                    <tr>
                        <th>Name</th>
                        <th>Telegram ID</th>
                        <th>Phone</th>
                        <th>Status</th>
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>
                    {% for driver in drivers %}
                    <tr>
                        <td>{{ driver.driver_name }}</td>
                        <td><code>{{ driver.driver_telegram_id }}</code></td>
                        <td>{{ driver.phone_number or 'N/A' }}</td>
                        <td>
                            <span class="status-{{ 'active' if driver.is_active else 'inactive' }}">
                                {{ 'Active' if driver.is_active else 'Inactive' }}
                            </span>
                        </td>
                        <td>
                            <a href="/toggle_driver/{{ driver.id }}">
                                <button class="btn-danger" style="padding: 6px 12px; font-size: 12px;">
                                    {{ 'Deactivate' if driver.is_active else 'Activate' }}
                                </button>
                            </a>
                        </td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
            </div>
        </div>
        
        <!-- Group Assistants: Telegram IDs that send leads into each group -->
        <div class="section">
            <h2>👥 Group Assistants</h2>
            <p style="margin-bottom: 15px; color: #555;">Assistants use the bot like normal; their leads go to the group they are assigned to.</p>
            {% for group in groups %}
            <div style="margin-bottom: 24px; padding: 12px; background: #fff; border-radius: 6px; border: 1px solid #ddd;">
                <strong>{{ group.group_name }}</strong> — Assistants (Telegram IDs):
                <ul style="margin: 8px 0; padding-left: 20px;">
                    {% for tid in (groups_assistants.get(group.id) or []) %}
                    <li><code>{{ tid }}</code> <a href="/remove_group_assistant/{{ group.id }}/{{ tid }}" style="margin-left: 8px; color: #dc3545; font-size: 12px;">Remove</a></li>
                    {% endfor %}
                    {% if not (groups_assistants.get(group.id) or []) %}
                    <li style="color: #888;">None yet</li>
                    {% endif %}
                </ul>
                <form method="POST" action="/add_group_assistant" class="assistant-form-row">
                    <input type="hidden" name="group_id" value="{{ group.id }}">
                    <input type="text" name="telegram_id" placeholder="Assistant Telegram ID (e.g. 123456789)">
                    <button type="submit" style="padding: 8px 16px;">Add Assistant</button>
                </form>
            </div>
            {% endfor %}
        </div>
        
    </div>
</body>
</html>
"""


@app.route('/')
def dashboard():
    """Main dashboard page."""
    try:
        groups = db.get_all_groups()
        drivers = db.get_all_drivers()
        groups_assistants = {}
        for g in (groups or []):
            groups_assistants[g['id']] = db.get_group_assistants(g['id'])
        assistants_choose_group = (db.get_setting("assistants_choose_group") or "").lower() in ("true", "1", "yes")
        lead_stats = db.get_lead_stats()
        return render_template_string(
            DASHBOARD_HTML,
            groups=groups or [],
            drivers=drivers or [],
            groups_assistants=groups_assistants,
            assistants_choose_group=assistants_choose_group,
            lead_stats=lead_stats,
            assignments=[],
            message=request.args.get('message'),
            message_type=request.args.get('type', 'success')
        )
    except Exception as e:
        return f"Error loading dashboard: {str(e)}", 500


@app.route('/add_group', methods=['POST'])
def add_group():
    """Add a new group."""
    try:
        group_name = request.form.get('group_name')
        group_telegram_id = request.form.get('group_telegram_id')
        supervisory_telegram_id = request.form.get('supervisory_telegram_id')
        
        if db.create_group(group_name, group_telegram_id, supervisory_telegram_id):
            return redirect(url_for('dashboard', message='Group added successfully!', type='success'))
        else:
            return redirect(url_for('dashboard', message='Error adding group', type='error'))
    except Exception as e:
        return redirect(url_for('dashboard', message=f'Error: {str(e)}', type='error'))


@app.route('/add_driver', methods=['POST'])
def add_driver():
    """Add a new driver."""
    try:
        driver_name = request.form.get('driver_name')
        driver_telegram_id = request.form.get('driver_telegram_id')
        phone_number = request.form.get('phone_number') or None
        
        if db.create_driver(driver_name, driver_telegram_id, phone_number):
            return redirect(url_for('dashboard', message='Driver added successfully!', type='success'))
        else:
            return redirect(url_for('dashboard', message='Error adding driver', type='error'))
    except Exception as e:
        return redirect(url_for('dashboard', message=f'Error: {str(e)}', type='error'))


@app.route('/toggle_group/<group_id>')
def toggle_group(group_id):
    """Toggle group active status."""
    try:
        if db.toggle_group_status(group_id):
            return redirect(url_for('dashboard', message='Group status updated!', type='success'))
        else:
            return redirect(url_for('dashboard', message='Error updating group', type='error'))
    except Exception as e:
        return redirect(url_for('dashboard', message=f'Error: {str(e)}', type='error'))


@app.route('/toggle_driver/<driver_id>')
def toggle_driver(driver_id):
    """Toggle driver active status."""
    try:
        if db.toggle_driver_status(driver_id):
            return redirect(url_for('dashboard', message='Driver status updated!', type='success'))
        else:
            return redirect(url_for('dashboard', message='Error updating driver', type='error'))
    except Exception as e:
        return redirect(url_for('dashboard', message=f'Error: {str(e)}', type='error'))


@app.route('/set_assistants_choose_group', methods=['POST'])
def set_assistants_choose_group():
    """Toggle setting: value=1 means allow assistants to choose group, 0 means use assigned only."""
    try:
        val = (request.form.get("value") or "0").strip()
        db.set_setting("assistants_choose_group", "true" if val == "1" else "false")
        return redirect(url_for('dashboard', message='Setting updated!', type='success'))
    except Exception as e:
        return redirect(url_for('dashboard', message=f'Error: {str(e)}', type='error'))


@app.route('/add_group_assistant', methods=['POST'])
def add_group_assistant():
    """Add an assistant (Telegram ID) to a group (legacy form)."""
    try:
        group_id = request.form.get('group_id')
        telegram_id = (request.form.get('telegram_id') or '').strip()
        if not group_id or not telegram_id:
            return redirect(url_for('dashboard', message='Missing group or Telegram ID', type='error'))
        if db.add_group_assistant(group_id, telegram_id):
            return redirect(url_for('dashboard', message='Assistant added!', type='success'))
        return redirect(url_for('dashboard', message='Error adding assistant', type='error'))
    except Exception as e:
        return redirect(url_for('dashboard', message=f'Error: {str(e)}', type='error'))


@app.route('/remove_group_assistant/<group_id>/<telegram_id>')
def remove_group_assistant(group_id, telegram_id):
    """Remove an assistant from a group (legacy link)."""
    try:
        if db.remove_group_assistant(group_id, telegram_id):
            return redirect(url_for('dashboard', message='Assistant removed!', type='success'))
        return redirect(url_for('dashboard', message='Error removing assistant', type='error'))
    except Exception as e:
        return redirect(url_for('dashboard', message=f'Error: {str(e)}', type='error'))


# --- JSON API for Vercel frontend (no iframe) ---

def _get_json_or_form():
    """Get request data from JSON body or form (for API and legacy)."""
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form


@app.route('/api/groups', methods=['GET', 'POST'])
def api_groups():
    if request.method == 'GET':
        try:
            groups = db.get_all_groups()
            return jsonify(groups or [])
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    # POST: create group
    try:
        data = _get_json_or_form()
        group_name = data.get('group_name') or request.form.get('group_name')
        group_telegram_id = data.get('group_telegram_id') or request.form.get('group_telegram_id')
        supervisory_telegram_id = data.get('supervisory_telegram_id') or request.form.get('supervisory_telegram_id')
        if not all([group_name, group_telegram_id, supervisory_telegram_id]):
            return jsonify({"success": False, "error": "Missing group_name, group_telegram_id, or supervisory_telegram_id"}), 400
        if db.create_group(group_name, group_telegram_id, supervisory_telegram_id):
            return jsonify({"success": True, "message": "Group added successfully!"})
        return jsonify({"success": False, "error": "Error adding group"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/drivers', methods=['GET', 'POST'])
def api_drivers():
    if request.method == 'GET':
        try:
            drivers = db.get_all_drivers()
            return jsonify(drivers or [])
        except Exception as e:
            return jsonify({"error": str(e)}), 500
    # POST: create driver
    try:
        data = _get_json_or_form()
        driver_name = (data.get('driver_name') or request.form.get('driver_name') or '').strip()
        driver_telegram_id = (data.get('driver_telegram_id') or request.form.get('driver_telegram_id') or '').strip()
        phone_number = data.get('phone_number') or request.form.get('phone_number')
        if phone_number is not None:
            phone_number = str(phone_number).strip() or None
        if not driver_name or not driver_telegram_id:
            return jsonify({"success": False, "error": "Missing driver_name or driver_telegram_id"}), 400
        try:
            if db.create_driver(driver_name, driver_telegram_id, phone_number):
                return jsonify({"success": True, "message": "Driver added successfully!"})
        except Exception as db_err:
            err_msg = str(db_err).lower()
            logger.exception("POST /api/drivers create_driver failed")
            if any(x in err_msg for x in ("unique", "duplicate", "already exists", "23505", "unique constraint", "violates")):
                return jsonify({"success": False, "error": "A driver with this Telegram ID already exists."}), 409
            return jsonify({"success": False, "error": str(db_err)}), 500
    except Exception as e:
        logger.exception("POST /api/drivers error")
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/groups/<group_id>/assistants', methods=['GET'])
def api_group_assistants(group_id):
    """List assistants (Telegram IDs) for a group."""
    try:
        ids = db.get_group_assistants(group_id)
        return jsonify(ids)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/groups/<group_id>/assistants', methods=['POST'])
def api_add_group_assistant(group_id):
    """Add an assistant (Telegram user ID) to a group. Body: {"telegram_id": "123456789"}."""
    try:
        data = _get_json_or_form()
        telegram_id = (data.get('telegram_id') or request.form.get('telegram_id') or '').strip()
        if not telegram_id:
            return jsonify({"success": False, "error": "Missing telegram_id"}), 400
        if db.add_group_assistant(group_id, telegram_id):
            return jsonify({"success": True, "message": "Assistant added!"})
        return jsonify({"success": False, "error": "Error adding assistant"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/groups/<group_id>/assistants/<telegram_id>', methods=['DELETE'])
def api_remove_group_assistant(group_id, telegram_id):
    """Remove an assistant from a group."""
    try:
        if db.remove_group_assistant(group_id, telegram_id):
            return jsonify({"success": True, "message": "Assistant removed!"})
        return jsonify({"success": False, "error": "Error removing assistant"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/settings', methods=['GET'])
def api_get_settings():
    """Get settings (e.g. assistants_choose_group)."""
    try:
        val = db.get_setting("assistants_choose_group")
        return jsonify({"assistants_choose_group": (val or "").lower() in ("true", "1", "yes")})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/settings', methods=['POST'])
def api_set_settings():
    """Update settings. Body: { \"assistants_choose_group\": true/false }."""
    try:
        data = _get_json_or_form()
        v = data.get("assistants_choose_group")
        if v is None:
            return jsonify({"success": False, "error": "Missing assistants_choose_group"}), 400
        db.set_setting("assistants_choose_group", "true" if v in (True, "true", "1", "yes") else "false")
        return jsonify({"success": True, "message": "Settings updated!"})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/stats', methods=['GET'])
def api_stats():
    """Get lead stats: total_leads, drivers with leads_accepted and receipts_submitted."""
    try:
        return jsonify(db.get_lead_stats())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/groups/<group_id>/toggle', methods=['POST'])
def api_toggle_group(group_id):
    """Toggle group active status."""
    try:
        if db.toggle_group_status(group_id):
            return jsonify({"success": True, "message": "Group status updated!"})
        return jsonify({"success": False, "error": "Error updating group"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route('/api/drivers/<driver_id>/toggle', methods=['POST'])
def api_toggle_driver(driver_id):
    """Toggle driver active status."""
    try:
        if db.toggle_driver_status(driver_id):
            return jsonify({"success": True, "message": "Driver status updated!"})
        return jsonify({"success": False, "error": "Error updating driver"}), 500
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


if __name__ == '__main__':
    # Render sets PORT for web services; fallback to ADMIN_PORT or 5000
    port = int(os.getenv('PORT', os.getenv('ADMIN_PORT', 5000)))
    app.run(host='0.0.0.0', port=port, debug=os.getenv('FLASK_DEBUG', 'false').lower() == 'true')

