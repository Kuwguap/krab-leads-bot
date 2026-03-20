"use client";

import { useMemo, useState } from "react";

export default function SecretPage({ params }) {
  const secretKey = useMemo(() => (params?.secret_key || "").toString(), [params]);
  const [passphrase, setPassphrase] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [secret, setSecret] = useState("");
  const [unlocked, setUnlocked] = useState(false);

  async function unlock() {
    setError("");
    setLoading(true);
    try {
      const res = await fetch("/api/v1/unlock", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ secret_key: secretKey, passphrase }),
      });

      if (!res.ok) {
        if (res.status === 401) throw new Error("Incorrect passphrase.");
        if (res.status === 410) throw new Error("This note has expired.");
        if (res.status === 404) throw new Error("Not found (or already deleted).");
        throw new Error(`Unlock failed (HTTP ${res.status}).`);
      }

      const data = await res.json();
      setSecret(data?.secret || "");
      setUnlocked(true);
    } catch (e) {
      setSecret("");
      setUnlocked(false);
      setError(e?.message || "Unlock failed.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main style={{ maxWidth: 720, margin: "40px auto", padding: 16, fontFamily: "system-ui" }}>
      <h1 style={{ marginBottom: 12 }}>Unlock note</h1>
      {!unlocked ? (
        <>
          <p style={{ marginBottom: 18, color: "#555" }}>
            Enter the passphrase set by the admin to reveal this phone number.
          </p>
          <div style={{ display: "grid", gap: 10 }}>
            <label style={{ fontWeight: 600 }}>
              Passphrase
              <input
                value={passphrase}
                onChange={(e) => setPassphrase(e.target.value)}
                style={{ display: "block", width: "100%", padding: 12, marginTop: 6 }}
                type="password"
                autoComplete="off"
              />
            </label>
            <button
              type="button"
              onClick={unlock}
              disabled={loading || !secretKey}
              style={{
                padding: "12px 16px",
                border: "none",
                borderRadius: 8,
                background: "#667eea",
                color: "white",
                cursor: "pointer",
                fontWeight: 700,
                opacity: loading ? 0.7 : 1,
              }}
            >
              {loading ? "Unlocking..." : "Unlock"}
            </button>
          </div>
          {error ? <p style={{ marginTop: 12, color: "#dc3545" }}>{error}</p> : null}
        </>
      ) : (
        <>
          <p style={{ marginBottom: 10, color: "#155724", fontWeight: 700 }}>Unlocked</p>
          <div
            style={{
              background: "#f6f6f6",
              borderRadius: 12,
              padding: 16,
              wordBreak: "break-word",
              fontSize: 20,
            }}
          >
            {secret}
          </div>
        </>
      )}
      <p style={{ marginTop: 24, color: "#888", fontSize: 12 }}>
        Secret key: {secretKey?.slice(0, 10)}...
      </p>
    </main>
  );
}

