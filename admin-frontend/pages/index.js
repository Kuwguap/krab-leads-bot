import { useState, useEffect, useCallback } from 'react';

const API = process.env.NEXT_PUBLIC_ADMIN_BACKEND_URL || '';

export default function AdminPanel() {
  const [groups, setGroups] = useState([]);
  const [drivers, setDrivers] = useState([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState(null);
  const [messageType, setMessageType] = useState('success');

  const showMessage = (msg, type = 'success') => {
    setMessage(msg);
    setMessageType(type);
    setTimeout(() => setMessage(null), 5000);
  };

  const fetchGroups = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/groups`);
      if (res.ok) setGroups(await res.json());
      else setGroups([]);
    } catch {
      setGroups([]);
    }
  }, []);

  const fetchDrivers = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/drivers`);
      if (res.ok) setDrivers(await res.json());
      else setDrivers([]);
    } catch {
      setDrivers([]);
    }
  }, []);

  useEffect(() => {
    if (!API) {
      setLoading(false);
      showMessage('Set NEXT_PUBLIC_ADMIN_BACKEND_URL in Vercel to your Render admin URL.', 'error');
      return;
    }
    (async () => {
      setLoading(true);
      await Promise.all([fetchGroups(), fetchDrivers()]);
      setLoading(false);
    })();
  }, [fetchGroups, fetchDrivers]);

  async function handleAddGroup(e) {
    e.preventDefault();
    const form = e.target;
    const payload = {
      group_name: form.group_name.value.trim(),
      group_telegram_id: form.group_telegram_id.value.trim(),
      supervisory_telegram_id: form.supervisory_telegram_id.value.trim(),
    };
    if (!payload.group_name || !payload.group_telegram_id || !payload.supervisory_telegram_id) {
      showMessage('Fill all group fields.', 'error');
      return;
    }
    try {
      const res = await fetch(`${API}/api/groups`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.success) {
        showMessage(data.message || 'Group added!');
        form.reset();
        fetchGroups();
      } else {
        showMessage(data.error || 'Failed to add group', 'error');
      }
    } catch (err) {
      showMessage('Network error. Is the backend running?', 'error');
    }
  }

  async function handleAddDriver(e) {
    e.preventDefault();
    const form = e.target;
    const payload = {
      driver_name: form.driver_name.value.trim(),
      driver_telegram_id: form.driver_telegram_id.value.trim(),
      phone_number: (form.phone_number?.value || '').trim() || null,
    };
    if (!payload.driver_name || !payload.driver_telegram_id) {
      showMessage('Fill driver name and Telegram ID.', 'error');
      return;
    }
    try {
      const res = await fetch(`${API}/api/drivers`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.success) {
        showMessage(data.message || 'Driver added!');
        form.reset();
        fetchDrivers();
      } else {
        showMessage(data.error || 'Failed to add driver', 'error');
      }
    } catch (err) {
      showMessage('Network error. Is the backend running?', 'error');
    }
  }

  async function toggleGroup(groupId) {
    try {
      const res = await fetch(`${API}/api/groups/${groupId}/toggle`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.success) {
        showMessage(data.message || 'Group updated!');
        fetchGroups();
      } else {
        showMessage(data.error || 'Failed to update group', 'error');
      }
    } catch {
      showMessage('Network error.', 'error');
    }
  }

  async function toggleDriver(driverId) {
    try {
      const res = await fetch(`${API}/api/drivers/${driverId}/toggle`, { method: 'POST' });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.success) {
        showMessage(data.message || 'Driver updated!');
        fetchDrivers();
      } else {
        showMessage(data.error || 'Failed to update driver', 'error');
      }
    } catch {
      showMessage('Network error.', 'error');
    }
  }

  if (loading) {
    return (
      <div style={styles.page}>
        <div style={styles.container}>
          <p style={{ textAlign: 'center', color: '#666' }}>Loading...</p>
        </div>
      </div>
    );
  }

  return (
    <div style={styles.page}>
      <div style={styles.container}>
        <h1 style={styles.h1}>KrabsLeads Admin</h1>

        {message && (
          <div
            style={{
              ...styles.message,
              ...(messageType === 'error' ? styles.messageError : styles.messageSuccess),
            }}
          >
            {message}
          </div>
        )}

        <section style={styles.section}>
          <h2 style={styles.sectionTitle}>Add New Group</h2>
          <form onSubmit={handleAddGroup} style={styles.form}>
            <div style={styles.formGroup}>
              <label>Group Name</label>
              <input type="text" name="group_name" required placeholder="e.g. Group A" style={styles.input} />
            </div>
            <div style={styles.formGroup}>
              <label>Group Telegram ID</label>
              <input type="text" name="group_telegram_id" required placeholder="e.g. -1001234567890" style={styles.input} />
            </div>
            <div style={styles.formGroup}>
              <label>Supervisory Telegram ID</label>
              <input type="text" name="supervisory_telegram_id" required placeholder="e.g. 123456789" style={styles.input} />
            </div>
            <button type="submit" style={styles.button}>Add Group</button>
          </form>
        </section>

        <section style={styles.section}>
          <h2 style={styles.sectionTitle}>Add New Driver</h2>
          <form onSubmit={handleAddDriver} style={styles.form}>
            <div style={styles.formGroup}>
              <label>Driver Name</label>
              <input type="text" name="driver_name" required placeholder="e.g. John Doe" style={styles.input} />
            </div>
            <div style={styles.formGroup}>
              <label>Driver Telegram ID</label>
              <input type="text" name="driver_telegram_id" required placeholder="e.g. 123456789" style={styles.input} />
            </div>
            <div style={styles.formGroup}>
              <label>Phone (optional)</label>
              <input type="text" name="phone_number" placeholder="e.g. +1234567890" style={styles.input} />
            </div>
            <button type="submit" style={styles.button}>Add Driver</button>
          </form>
        </section>

        <section style={styles.section}>
          <h2 style={styles.sectionTitle}>Groups</h2>
          <table style={styles.table}>
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
              {groups.length === 0 ? (
                <tr><td colSpan={5} style={{ textAlign: 'center', color: '#888' }}>No groups yet</td></tr>
              ) : (
                groups.map((g) => (
                  <tr key={g.id}>
                    <td>{g.group_name}</td>
                    <td><code>{g.group_telegram_id}</code></td>
                    <td><code>{g.supervisory_telegram_id}</code></td>
                    <td>
                      <span style={g.is_active ? styles.statusActive : styles.statusInactive}>
                        {g.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td>
                      <button
                        type="button"
                        onClick={() => toggleGroup(g.id)}
                        style={{ ...styles.button, ...styles.buttonSmall, ...styles.buttonDanger }}
                      >
                        {g.is_active ? 'Deactivate' : 'Activate'}
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </section>

        <section style={styles.section}>
          <h2 style={styles.sectionTitle}>Drivers</h2>
          <table style={styles.table}>
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
              {drivers.length === 0 ? (
                <tr><td colSpan={5} style={{ textAlign: 'center', color: '#888' }}>No drivers yet</td></tr>
              ) : (
                drivers.map((d) => (
                  <tr key={d.id}>
                    <td>{d.driver_name}</td>
                    <td><code>{d.driver_telegram_id}</code></td>
                    <td>{d.phone_number || '—'}</td>
                    <td>
                      <span style={d.is_active ? styles.statusActive : styles.statusInactive}>
                        {d.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td>
                      <button
                        type="button"
                        onClick={() => toggleDriver(d.id)}
                        style={{ ...styles.button, ...styles.buttonSmall, ...styles.buttonDanger }}
                      >
                        {d.is_active ? 'Deactivate' : 'Activate'}
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  );
}

const styles = {
  page: {
    minHeight: '100vh',
    margin: 0,
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif",
    background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
    padding: 20,
    boxSizing: 'border-box',
  },
  container: {
    maxWidth: 1200,
    margin: '0 auto',
    background: 'white',
    borderRadius: 12,
    boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
    padding: 30,
  },
  h1: {
    color: '#333',
    marginBottom: 30,
    textAlign: 'center',
  },
  message: {
    padding: 12,
    borderRadius: 6,
    marginBottom: 20,
  },
  messageSuccess: {
    background: '#d4edda',
    color: '#155724',
    border: '1px solid #c3e6cb',
  },
  messageError: {
    background: '#f8d7da',
    color: '#721c24',
    border: '1px solid #f5c6cb',
  },
  section: {
    marginBottom: 40,
    padding: 20,
    background: '#f8f9fa',
    borderRadius: 8,
  },
  sectionTitle: {
    color: '#667eea',
    marginBottom: 20,
    borderBottom: '2px solid #667eea',
    paddingBottom: 10,
  },
  form: { marginTop: 8 },
  formGroup: { marginBottom: 15 },
  input: {
    width: '100%',
    padding: 10,
    border: '2px solid #ddd',
    borderRadius: 6,
    fontSize: 14,
  },
  button: {
    background: '#667eea',
    color: 'white',
    padding: '12px 24px',
    border: 'none',
    borderRadius: 6,
    cursor: 'pointer',
    fontSize: 14,
    fontWeight: 500,
  },
  buttonSmall: { padding: '6px 12px', fontSize: 12 },
  buttonDanger: { background: '#dc3545' },
  table: {
    width: '100%',
    borderCollapse: 'collapse',
    marginTop: 15,
  },
  statusActive: { color: '#28a745', fontWeight: 600 },
  statusInactive: { color: '#dc3545', fontWeight: 600 },
};
