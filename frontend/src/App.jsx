import { useCallback, useEffect, useState } from "react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, Legend, ResponsiveContainer } from "recharts";

// Auto-refresh the dashboard this often while the tab is open, so a
// scan triggered from the CronJob (not this button) still shows up
// without the user needing to reload the page manually.
const AUTO_REFRESH_MS = 60_000;

export default function App() {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [scanning, setScanning] = useState(false);
  const [loading, setLoading] = useState(true);

  const fetchWaste = useCallback(() => {
    return fetch("/api/waste")
      .then((res) => {
        if (!res.ok) throw new Error("No scan results yet — run a scan to get started.");
        return res.json();
      })
      .then((json) => {
        setData(json);
        setError(null);
      })
      .catch((err) => setError(err.message));
  }, []);

  useEffect(() => {
    fetchWaste().finally(() => setLoading(false));
    const interval = setInterval(fetchWaste, AUTO_REFRESH_MS);
    return () => clearInterval(interval);
  }, [fetchWaste]);

  const [scanMessage, setScanMessage] = useState(null);

  const handleScan = async () => {
    setScanning(true);
    setError(null);
    setScanMessage(null);
    try {
      const res = await fetch("/api/scan", { method: "POST" });
      if (!res.ok) {
        const errJson = await res.json().catch(() => ({}));
        throw new Error(errJson.detail || `Scan failed (HTTP ${res.status})`);
      }
      const scanData = await res.json();
      setScanMessage(`Scan completed: ${scanData.namespaces_scanned ?? 0} namespace(s) analyzed.`);
      await fetchWaste();
    } catch (err) {
      setError(err.message);
    } finally {
      setScanning(false);
    }
  };

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", padding: "2rem" }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
        <div>
          <h1 style={{ marginBottom: 0 }}>ResourceGuard</h1>
          <p style={{ marginTop: 4 }}>Per-namespace resource waste — requested vs. actual usage.</p>
        </div>
        <button
          onClick={handleScan}
          disabled={scanning}
          style={{
            padding: "0.6rem 1.2rem",
            fontSize: "1rem",
            cursor: scanning ? "not-allowed" : "pointer",
            opacity: scanning ? 0.6 : 1,
            backgroundColor: "#2563eb",
            color: "white",
            border: "none",
            borderRadius: "4px",
            fontWeight: "500",
          }}
        >
          {scanning ? "Scanning…" : "Run scan now"}
        </button>
      </div>

      {loading && <p style={{ color: "#6b7280" }}>Loading scan results…</p>}
      {scanMessage && (
        <div style={{ padding: "0.75rem 1rem", marginTop: "1rem", backgroundColor: "#ecfdf5", color: "#065f46", borderRadius: "4px", border: "1px solid #a7f3d0" }}>
          {scanMessage}
        </div>
      )}
      {error && (
        <div style={{ padding: "0.75rem 1rem", marginTop: "1rem", backgroundColor: "#fef2f2", color: "#991b1b", borderRadius: "4px", border: "1px solid #fecaca" }}>
          {error}
        </div>
      )}

      {data && data.namespaces && data.namespaces.length === 0 && !loading && (
        <div style={{ marginTop: "2rem", padding: "1.5rem", textAlign: "center", backgroundColor: "#f9fafb", borderRadius: "6px", border: "1px dashed #d1d5db" }}>
          <p style={{ margin: 0, color: "#4b5563" }}>No workloads found in non-system namespaces. Deploy pods to monitor waste.</p>
        </div>
      )}

      {data && data.namespaces && data.namespaces.length > 0 && (
        <>
          <p>
            Last scanned: <strong>{new Date(data.scanned_at).toLocaleString()}</strong>
          </p>
          <ResponsiveContainer width="100%" height={400}>
            <BarChart data={data.namespaces}>
              <XAxis dataKey="namespace" />
              <YAxis unit="%" />
              <Tooltip />
              <Legend />
              <Bar dataKey="cpu_waste_pct" name="CPU waste %" fill="#8884d8" />
              <Bar dataKey="memory_waste_pct" name="Memory waste %" fill="#82ca9d" />
            </BarChart>
          </ResponsiveContainer>

          <table style={{ width: "100%", borderCollapse: "collapse", marginTop: "2rem" }}>
            <thead>
              <tr style={{ borderBottom: "2px solid #e5e7eb" }}>
                <th align="left" style={{ padding: "8px 12px" }}>Namespace</th>
                <th align="right" style={{ padding: "8px 12px" }}>Pods</th>
                <th align="right" style={{ padding: "8px 12px" }}>CPU requested (m)</th>
                <th align="right" style={{ padding: "8px 12px" }}>CPU used (m)</th>
                <th align="right" style={{ padding: "8px 12px" }}>CPU waste %</th>
                <th align="right" style={{ padding: "8px 12px" }}>Mem waste %</th>
                <th align="left" style={{ padding: "8px 12px" }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {data.namespaces.map((ns) => (
                <tr key={ns.namespace} style={{ borderBottom: "1px solid #f3f4f6" }}>
                  <td style={{ padding: "8px 12px" }}>{ns.namespace}</td>
                  <td align="right" style={{ padding: "8px 12px" }}>{ns.pod_count}</td>
                  <td align="right" style={{ padding: "8px 12px" }}>{ns.cpu_requested_millicores}</td>
                  <td align="right" style={{ padding: "8px 12px" }}>{ns.cpu_used_millicores}</td>
                  <td align="right" style={{ padding: "8px 12px" }}>{ns.cpu_waste_pct}%</td>
                  <td align="right" style={{ padding: "8px 12px" }}>{ns.memory_waste_pct}%</td>
                  <td style={{ padding: "8px 12px" }}>
                    {/* Surfaces compute_namespace_waste()'s is_complete flag so an
                        incomplete scan (missing metrics samples, or a quantity that
                        failed to parse) isn't mistaken for a confirmed waste number. */}
                    {ns.is_complete === false ? (
                      <span title={`${ns.missing_samples ?? 0} missing sample(s), ${ns.unparsable_quantities ?? 0} unparsable`}>
                        ⚠️ incomplete
                      </span>
                    ) : (
                      "✓"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
