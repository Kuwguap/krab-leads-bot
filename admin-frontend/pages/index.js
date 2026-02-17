export default function Home() {
  const backendUrl =
    process.env.NEXT_PUBLIC_ADMIN_BACKEND_URL || "https://your-render-admin-url.example.com";

  return (
    <div
      style={{
        minHeight: "100vh",
        margin: 0,
        fontFamily:
          "-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif",
        background: "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
        padding: "20px",
        boxSizing: "border-box",
      }}
    >
      <div
        style={{
          maxWidth: "1200px",
          margin: "0 auto",
          background: "white",
          borderRadius: "12px",
          boxShadow: "0 20px 60px rgba(0,0,0,0.3)",
          padding: "24px",
        }}
      >
        <h1 style={{ textAlign: "center", marginBottom: "16px" }}>
          🚀 KrabsLeads Admin Panel
        </h1>
        <p style={{ textAlign: "center", marginBottom: "12px", color: "#555" }}>
          Frontend hosted on Vercel, backend admin server running on Render.
        </p>
        <p style={{ textAlign: "center", marginBottom: "24px", color: "#666" }}>
          Backend URL:
          <br />
          <code style={{ fontSize: "0.9rem" }}>{backendUrl}</code>
        </p>

        <div
          style={{
            borderRadius: "8px",
            overflow: "hidden",
            border: "1px solid #ddd",
            background: "#f8f9fa",
            minHeight: "70vh",
          }}
        >
          <iframe
            src={backendUrl}
            title="KrabsLeads Admin Dashboard"
            style={{
              width: "100%",
              height: "100%",
              border: "none",
            }}
          />
        </div>

        <p
          style={{
            marginTop: "16px",
            fontSize: "0.85rem",
            color: "#888",
            textAlign: "center",
          }}
        >
          If the dashboard does not load, confirm that your Render admin service
          is running and that NEXT_PUBLIC_ADMIN_BACKEND_URL is set correctly in
          your Vercel project settings.
        </p>
      </div>
    </div>
  );
}

