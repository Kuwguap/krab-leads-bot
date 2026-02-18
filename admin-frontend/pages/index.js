import { useState, useEffect, useCallback } from 'react';

const API = process.env.NEXT_PUBLIC_ADMIN_BACKEND_URL || '';

export default function AdminPanel() {
  const [groups, setGroups] = useState([]);
  const [drivers, setDrivers] = useState([]);
  const [assistantsByGroup, setAssistantsByGroup] = useState({});
  const [settings, setSettings] = useState({ assistants_choose_group: false });
  const [stats, setStats] = useState({ total_leads: 0, drivers: [] });
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

  const fetchAssistantsForGroups = useCallback(async (groupList) => {
    const next = {};
    for (const g of groupList || []) {
      try {
        const res = await fetch(`${API}/api/groups/${g.id}/assistants`);
        next[g.id] = res.ok ? await res.json() : [];
      } catch {
        next[g.id] = [];
      }
    }
    setAssistantsByGroup(next);
  }, []);

  const fetchSettings = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/settings`);
      if (res.ok) setSettings(await res.json());
    } catch {}
  }, []);

  const fetchStats = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/stats`);
      if (res.ok) setStats(await res.json());
    } catch {}
  }, []);

  useEffect(() => {
    if (!API) {
      setLoading(false);
      showMessage('Set NEXT_PUBLIC_ADMIN_BACKEND_URL in Vercel to your Render admin URL.', 'error');
      return;
    }
    (async () => {
      setLoading(true);
      await Promise.all([fetchGroups(), fetchDrivers(), fetchSettings(), fetchStats()]);
      setLoading(false);
    })();
  }, [fetchGroups, fetchDrivers, fetchSettings, fetchStats]);

  useEffect(() => {
    if (!API || groups.length === 0) return;
    fetchAssistantsForGroups(groups);
  }, [API, groups, fetchAssistantsForGroups]);

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
        const msg = data.error || (res.status === 500 ? 'Server error. Check Render logs for details.' : 'Failed to add driver');
        showMessage(msg, 'error');
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

  async function handleAddAssistant(groupId, telegramId) {
    const tid = String(telegramId).trim();
    if (!tid) {
      showMessage('Enter assistant Telegram ID.', 'error');
      return;
    }
    try {
      const res = await fetch(`${API}/api/groups/${groupId}/assistants`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ telegram_id: tid }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.success) {
        showMessage(data.message || 'Assistant added!');
        fetchAssistantsForGroups(groups);
      } else {
        showMessage(data.error || 'Failed to add assistant', 'error');
      }
    } catch {
      showMessage('Network error.', 'error');
    }
  }

  async function handleRemoveAssistant(groupId, telegramId) {
    try {
      const res = await fetch(`${API}/api/groups/${groupId}/assistants/${encodeURIComponent(String(telegramId))}`, { method: 'DELETE' });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.success) {
        showMessage(data.message || 'Assistant removed!');
        setAssistantsByGroup((prev) => ({ ...prev, [groupId]: (prev[groupId] || []).filter((t) => t !== String(telegramId)) }));
      } else {
        showMessage(data.error || 'Failed to remove assistant', 'error');
      }
    } catch {
      showMessage('Network error.', 'error');
    }
  }

  async function toggleAssistantsChooseGroup() {
    const next = !settings.assistants_choose_group;
    try {
      const res = await fetch(`${API}/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ assistants_choose_group: next }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.success) {
        setSettings({ assistants_choose_group: next });
        showMessage(data.message || 'Setting updated!');
      } else {
        showMessage(data.error || 'Failed to update setting', 'error');
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
    <div className="admin-page" style={styles.page}>
      <style dangerouslySetInnerHTML={{ __html: `
        .admin-page { padding: 20px; }
        .admin-container { max-width: 1200px; margin: 0 auto; background: white; border-radius: 12px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); padding: 30px; }
        .admin-table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; margin-top: 15px; }
        .admin-table-wrap table { min-width: 320px; }
        .admin-section { margin-bottom: 40px; padding: 20px; background: #f8f9fa; border-radius: 8px; }
        .admin-assistant-form { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
        .admin-assistant-form input { flex: 1; min-width: 0; }
        @media (max-width: 768px) {
          .admin-page { padding: 10px; }
          .admin-container { padding: 16px; }
          .admin-section { padding: 14px; margin-bottom: 24px; }
          .admin-section h2 { font-size: 1rem; margin-bottom: 14px; }
          .admin-table-wrap th, .admin-table-wrap td { padding: 10px 6px; font-size: 0.8125rem; }
          .admin-mobile-full button { width: 100%; min-height: 48px; }
          .admin-assistant-form { flex-direction: column; align-items: stretch; }
          .admin-assistant-form button { width: 100%; }
          .admin-assistant-form input { width: 100%; }
        }
        @media (max-width: 480px) {
          .admin-section h2 { font-size: 0.9375rem; }
        }
      ` }} />
      <div className="admin-container" style={styles.container}>
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

        <section className="admin-section" style={styles.section}>
          <h2 style={styles.sectionTitle}>Lead flow</h2>
          <p style={{ marginBottom: 10, color: '#555' }}>
            When <strong>Allow assistants to choose group</strong> is ON, anyone can send leads and will choose a group (then a driver). When OFF, assistants use their assigned group.
          </p>
          <p style={{ marginBottom: 12 }}><strong>Current:</strong> {settings.assistants_choose_group ? 'Allow assistants to choose group' : 'Use assigned groups only'}</p>
          <button type="button" onClick={toggleAssistantsChooseGroup} className="admin-mobile-full" style={styles.button}>
            {settings.assistants_choose_group ? 'Use assigned groups only' : 'Allow assistants to choose group'}
          </button>
        </section>

        <section className="admin-section" style={styles.section}>
          <h2 style={styles.sectionTitle}>Lead stats</h2>
          <p style={{ marginBottom: 12 }}><strong>Total leads sent:</strong> {stats.total_leads ?? 0}</p>
          <div className="admin-table-wrap">
          <table style={styles.table}>
            <thead>
              <tr>
                <th>Driver</th>
                <th>Leads accepted</th>
                <th>Receipts submitted</th>
              </tr>
            </thead>
            <tbody>
              {(stats.drivers || []).length === 0 ? (
                <tr><td colSpan={3} style={{ textAlign: 'center', color: '#888' }}>No drivers</td></tr>
              ) : (
                (stats.drivers || []).map((d) => (
                  <tr key={d.driver_id}>
                    <td>{d.driver_name}</td>
                    <td>{d.leads_accepted}</td>
                    <td>{d.receipts_submitted}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
          </div>
        </section>

        <section className="admin-section" style={styles.section}>
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
            <button type="submit" className="admin-mobile-full" style={styles.button}>Add Group</button>
          </form>
        </section>

        <section className="admin-section" style={styles.section}>
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
            <button type="submit" className="admin-mobile-full" style={styles.button}>Add Driver</button>
          </form>
        </section>

        <section className="admin-section" style={styles.section}>
          <h2 style={styles.sectionTitle}>Groups</h2>
          <div className="admin-table-wrap">
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
                        className="admin-mobile-full"
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
          </div>
        </section>

        <section className="admin-section" style={styles.section}>
          <h2 style={styles.sectionTitle}>Group Assistants</h2>
          <p style={{ marginBottom: 16, color: '#555' }}>Assistants use the bot like normal; their leads go to the group they are assigned to.</p>
          {groups.map((g) => (
            <div key={g.id} style={{ marginBottom: 20, padding: 12, background: '#fff', borderRadius: 6, border: '1px solid #ddd' }}>
              <strong>{g.group_name}</strong> — Assistants (Telegram IDs):
              <ul style={{ margin: '8px 0', paddingLeft: 20 }}>
                {(assistantsByGroup[g.id] || []).length === 0 ? (
                  <li style={{ color: '#888' }}>None yet</li>
                ) : (
                  (assistantsByGroup[g.id] || []).map((tid) => (
                    <li key={tid} style={{ marginBottom: 6 }}>
                      <code>{tid}</code>{' '}
                      <button
                        type="button"
                        onClick={() => handleRemoveAssistant(g.id, tid)}
                        className="admin-mobile-full"
                        style={{ ...styles.button, ...styles.buttonSmall, ...styles.buttonDanger, marginLeft: 8, marginTop: 4 }}
                      >
                        Remove
                      </button>
                    </li>
                  ))
                )}
              </ul>
              <form
                className="admin-assistant-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  const input = e.target.querySelector('input[name="telegram_id"]');
                  if (input) {
                    handleAddAssistant(g.id, input.value);
                    input.value = '';
                  }
                }}
              >
                <input
                  type="text"
                  name="telegram_id"
                  placeholder="Assistant Telegram ID (e.g. 123456789)"
                  style={styles.input}
                />
                <button type="submit" className="admin-mobile-full" style={styles.button}>Add Assistant</button>
              </form>
            </div>
          ))}
        </section>

        <section className="admin-section" style={styles.section}>
          <h2 style={styles.sectionTitle}>Drivers</h2>
          <div className="admin-table-wrap">
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
                        className="admin-mobile-full"
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
          </div>
        </section>
      </div>
    </div>
  );
}

const styles = {
  page: {
    minHeight: '100vh',
    margin: 0,
    width: '100%',
    fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif",
    background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)',
    padding: 20,
    boxSizing: 'border-box',
  },
  container: {
    maxWidth: 1200,
    width: '100%',
    margin: '0 auto',
    background: 'white',
    borderRadius: 12,
    boxShadow: '0 20px 60px rgba(0,0,0,0.3)',
    padding: 30,
    boxSizing: 'border-box',
  },
  h1: {
    color: '#333',
    marginBottom: 30,
    textAlign: 'center',
    fontSize: 'clamp(1.25rem, 4vw, 1.75rem)',
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
    padding: 12,
    border: '2px solid #ddd',
    borderRadius: 6,
    fontSize: 16,
    boxSizing: 'border-box',
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
    minHeight: 44,
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
