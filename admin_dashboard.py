"""Simple Flask admin dashboard for managing groups, drivers, and supervisory IDs."""
from flask import Flask, render_template_string, request, jsonify, redirect, url_for
from flask_cors import CORS
from utils.database import Database
import os

app = Flask(__name__)
CORS(app)
db = Database()

# Simple HTML template for the dashboard
DASHBOARD_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>KrabsLeads Admin Dashboard</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
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
            padding: 10px;
            border: 2px solid #ddd;
            border-radius: 6px;
            font-size: 14px;
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
        table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 15px;
        }
        th, td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
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
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 KrabsLeads Admin Dashboard</h1>
        
        {% if message %}
        <div class="message message-{{ message_type }}">{{ message }}</div>
        {% endif %}
        
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
        
        <!-- Drivers List -->
        <div class="section">
            <h2>🚗 Drivers</h2>
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
</body>
</html>
"""


@app.route('/')
def dashboard():
    """Main dashboard page."""
    try:
        groups = db.get_all_groups()
        drivers = db.get_all_drivers()
        
        return render_template_string(
            DASHBOARD_HTML,
            groups=groups or [],
            drivers=drivers or [],
            assignments=[],  # No longer used
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




if __name__ == '__main__':
    port = int(os.getenv('ADMIN_PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)

