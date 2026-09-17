"use client";

import * as React from "react";
import { api } from "@/lib/api";
import type { ServerInfo, ScriptInfo } from "@/lib/types";
import Link from "next/link";

type TabKey = "scripts" | "chat" | "users";

export function AdminApp() {
  const [activeTab, setActiveTab] = React.useState<TabKey>("scripts");
  const [sessionChecked, setSessionChecked] = React.useState<"checking" | "admin" | "denied">("checking");
  const [userEmail, setUserEmail] = React.useState<string>("");

  React.useEffect(() => {
    checkAdmin();
  }, []);

  async function checkAdmin() {
    try {
      const session = await api.session();
      if (!session?.authenticated || session.user?.role !== "admin") {
        setSessionChecked("denied");
        return;
      }
      // Check if admin@decint.tools
      if (session.user.email !== "admin@decint.tools") {
        setSessionChecked("denied");
        return;
      }
      setUserEmail(session.user.email);
      setSessionChecked("admin");
    } catch {
      setSessionChecked("denied");
    }
  }

  if (sessionChecked === "checking") {
    return (
      <div
        style={{
          position: "fixed", inset: 0, display: "grid", placeItems: "center",
          background: "#0a0f18", color: "var(--color-neutral-600)",
          fontFamily: "var(--mono)", fontSize: 12.5,
        }}
      >
        verifying admin access…
      </div>
    );
  }

  if (sessionChecked === "denied") {
    return (
      <main style={{ minHeight: "100vh", background: "#0a0f18" }}>
        <nav style={{
          position: "sticky", top: 0, zIndex: 20,
          borderBottom: "1px solid var(--color-divider)",
          background: "#0a0f18",
          padding: "12px 24px",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}>
          <Link href="/" style={{ color: "var(--color-text)", textDecoration: "none", fontFamily: "var(--mono)", fontWeight: 600 }}>
            DECINT
          </Link>
          <Link href="/login?next=/admin" className="btn btn-primary">Sign in</Link>
        </nav>
        <div
          style={{
            display: "grid", placeItems: "center", height: "calc(100vh - 60px)",
            color: "var(--color-neutral-400)", fontFamily: "var(--mono)", fontSize: 14,
          }}
        >
          Access denied. Admin panel restricted to admin@decint.tools
        </div>
      </main>
    );
  }

  return (
    <main style={{ minHeight: "100vh", background: "#0a0f18", display: "flex", flexDirection: "column" }}>
      {/* Header */}
      <header style={{
        position: "sticky", top: 0, zIndex: 20,
        borderBottom: "1px solid var(--color-divider)",
        background: "#0a0f18",
        padding: "14px 24px",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <Link href="/" style={{ color: "var(--color-text)", textDecoration: "none", fontFamily: "var(--mono)", fontWeight: 600, fontSize: 14 }}>
            DECINT
          </Link>
          <span style={{ color: "var(--color-divider)" }}>·</span>
          <h1 style={{ fontSize: 14, margin: 0, color: "var(--color-accent)", fontFamily: "var(--mono)", fontWeight: 500 }}>
            Admin Panel
          </h1>
        </div>
        <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
          <span style={{ color: "var(--color-neutral-500)", fontFamily: "var(--mono)", fontSize: 12 }}>{userEmail}</span>
          <Link href="/console" className="btn btn-ghost" style={{ fontSize: 12 }}>Console</Link>
          <button
            type="button"
            className="btn btn-primary"
            onClick={async () => {
              await api.logout().catch(() => {});
              window.location.assign("/");
            }}
            style={{ fontSize: 12 }}
          >
            Sign out
          </button>
        </div>
      </header>

      {/* Tabs */}
      <div style={{ borderBottom: "1px solid var(--color-divider)", padding: "0 24px" }}>
        <div style={{ display: "flex", gap: 0 }}>
          {(["scripts", "chat", "users"] as TabKey[]).map((tab) => (
            <button
              key={tab}
              onClick={() => setActiveTab(tab)}
              style={{
                padding: "12px 20px",
                background: "transparent",
                border: "none",
                borderBottom: activeTab === tab ? "2px solid var(--color-accent)" : "2px solid transparent",
                color: activeTab === tab ? "var(--color-accent)" : "var(--color-neutral-500)",
                fontFamily: "var(--mono)",
                fontSize: 12,
                cursor: "pointer",
                textTransform: "uppercase" as const,
                letterSpacing: "0.05em",
              }}
            >
              {tab}
            </button>
          ))}
        </div>
      </div>

      {/* Content */}
      <div style={{ flex: 1, padding: "24px", maxWidth: 1200, margin: "0 auto", width: "100%" }}>
        {activeTab === "scripts" && <ScriptsTab />}
        {activeTab === "chat" && <ChatTab />}
        {activeTab === "users" && <UsersTab />}
      </div>
    </main>
  );
}

// ── Servers Tab ──

function ServersTab() {
  const [servers, setServers] = React.useState<ServerInfo[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [showForm, setShowForm] = React.useState(false);
  const [formData, setFormData] = React.useState({
    name: "", host: "", port: 22, username: "", password: "", private_key: "", description: "", is_active: true,
  });
  const [testing, setTesting] = React.useState<number | null>(null);

  React.useEffect(() => {
    loadServers();
  }, []);

  async function loadServers() {
    setLoading(true);
    try {
      const data = await api.executorServers();
      setServers(data);
    } catch (e) {
      console.error("Failed to load servers:", e);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.executorCreateServer({
        name: formData.name,
        host: formData.host,
        port: formData.port,
        username: formData.username,
        password: formData.password || null,
        private_key: formData.private_key || null,
        description: formData.description,
        is_active: formData.is_active,
      });
      setFormData({ name: "", host: "", port: 22, username: "", password: "", private_key: "", description: "", is_active: true });
      setShowForm(false);
      loadServers();
    } catch (err) {
      alert(`Failed to create server: ${err}`);
    }
  }

  async function handleDelete(id: number) {
    if (!confirm("Delete this server?")) return;
    try {
      await api.executorDeleteServer(id);
      loadServers();
    } catch (err) {
      alert(`Failed to delete: ${err}`);
    }
  }

  async function handleTest(id: number) {
    setTesting(id);
    try {
      const result = await api.executorTestServer(id);
      alert(result.success ? `Success: ${result.message}` : `Failed: ${result.message}`);
    } catch (err) {
      alert(`Test failed: ${err}`);
    } finally {
      setTesting(null);
    }
  }

  if (loading && servers.length === 0) {
    return <div style={{ color: "var(--color-neutral-500)", fontFamily: "var(--mono)" }}>Loading servers...</div>;
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <h2 style={{ fontSize: 14, color: "var(--color-text)", fontFamily: "var(--mono)", margin: 0 }}>SSH Servers</h2>
        <button
          onClick={() => setShowForm(!showForm)}
          style={{
            padding: "8px 16px",
            background: "var(--color-accent)",
            color: "#fff",
            border: "none",
            borderRadius: 4,
            fontFamily: "var(--mono)",
            fontSize: 12,
            cursor: "pointer",
          }}
        >
          {showForm ? "Cancel" : "Add Server"}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} style={{
          marginBottom: 16, padding: 16, background: "#0f141f", borderRadius: 6,
          border: "1px solid var(--color-divider)",
        }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12 }}>
            <input placeholder="Name" value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })} required
              style={{ padding: "8px 10px", background: "#0a0f18", border: "1px solid var(--color-divider)", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 12, borderRadius: 4 }} />
            <input placeholder="Host" value={formData.host} onChange={(e) => setFormData({ ...formData, host: e.target.value })} required
              style={{ padding: "8px 10px", background: "#0a0f18", border: "1px solid var(--color-divider)", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 12, borderRadius: 4 }} />
            <input type="number" placeholder="Port" value={formData.port} onChange={(e) => setFormData({ ...formData, port: parseInt(e.target.value) })}
              style={{ padding: "8px 10px", background: "#0a0f18", border: "1px solid var(--color-divider)", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 12, borderRadius: 4 }} />
            <input placeholder="Username" value={formData.username} onChange={(e) => setFormData({ ...formData, username: e.target.value })} required
              style={{ padding: "8px 10px", background: "#0a0f18", border: "1px solid var(--color-divider)", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 12, borderRadius: 4 }} />
            <input placeholder="Password (optional)" value={formData.password} onChange={(e) => setFormData({ ...formData, password: e.target.value })}
              style={{ padding: "8px 10px", background: "#0a0f18", border: "1px solid var(--color-divider)", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 12, borderRadius: 4 }} />
            <input placeholder="Description" value={formData.description} onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              style={{ padding: "8px 10px", background: "#0a0f18", border: "1px solid var(--color-divider)", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 12, borderRadius: 4 }} />
          </div>
          <textarea placeholder="Private Key (PEM, optional)" value={formData.private_key} onChange={(e) => setFormData({ ...formData, private_key: e.target.value })}
            style={{ marginTop: 12, width: "100%", padding: "8px 10px", background: "#0a0f18", border: "1px solid var(--color-divider)", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 12, borderRadius: 4, minHeight: 100 }} />
          <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
            <label style={{ display: "flex", alignItems: "center", gap: 8, color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 12 }}>
              <input type="checkbox" checked={formData.is_active} onChange={(e) => setFormData({ ...formData, is_active: e.target.checked })} />
              Active
            </label>
          </div>
          <button type="submit" style={{
            marginTop: 12, padding: "8px 16px", background: "var(--color-accent)", color: "#fff", border: "none", borderRadius: 4,
            fontFamily: "var(--mono)", fontSize: 12, cursor: "pointer",
          }}>
            Create Server
          </button>
        </form>
      )}

      <div style={{ display: "grid", gap: 12 }}>
        {servers.map((server) => (
          <div key={server.id} style={{
            padding: 12, background: "#0f141f", borderRadius: 6, border: "1px solid var(--color-divider)",
            display: "flex", justifyContent: "space-between", alignItems: "center",
          }}>
            <div>
              <div style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--color-text)" }}>
                {server.name} — {server.host}:{server.port} ({server.username})
              </div>
              {server.description && (
                <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--color-neutral-500)", marginTop: 4 }}>
                  {server.description}
                </div>
              )}
              <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: server.is_active ? "#7fce9e" : "#e8908f", marginTop: 4 }}>
                {server.is_active ? "Active" : "Inactive"}
              </div>
            </div>
            <div style={{ display: "flex", gap: 8 }}>
              <button
                onClick={() => handleTest(server.id)}
                disabled={testing === server.id}
                style={{
                  padding: "6px 12px", background: testing === server.id ? "#3a3f4f" : "#2a3f5f", color: "#fff",
                  border: "none", borderRadius: 4, fontFamily: "var(--mono)", fontSize: 11, cursor: testing === server.id ? "not-allowed" : "pointer",
                }}
              >
                {testing === server.id ? "Testing..." : "Test"}
              </button>
              <button
                onClick={() => handleDelete(server.id)}
                style={{
                  padding: "6px 12px", background: "#5f2a2a", color: "#fff", border: "none", borderRadius: 4,
                  fontFamily: "var(--mono)", fontSize: 11, cursor: "pointer",
                }}
              >
                Delete
              </button>
            </div>
          </div>
        ))}
        {servers.length === 0 && (
          <div style={{ color: "var(--color-neutral-500)", fontFamily: "var(--mono)", fontSize: 12 }}>
            No servers configured. Click "Add Server" to create one.
          </div>
        )}
      </div>
    </div>
  );
}

// ── Scripts Tab ──

function ScriptsTab() {
  const [scripts, setScripts] = React.useState<ScriptInfo[]>([]);
  const [loading, setLoading] = React.useState(false);
  const [showForm, setShowForm] = React.useState(false);
  const [formData, setFormData] = React.useState({ name: "", description: "", category: "general", is_public: true });
  const [uploading, setUploading] = React.useState<number | null>(null);
  const [code, setCode] = React.useState("");
  const [version, setVersion] = React.useState("1.0.0");
  const [changelog, setChangelog] = React.useState("");

  React.useEffect(() => {
    loadScripts();
  }, []);

  async function loadScripts() {
    setLoading(true);
    try {
      const data = await api.executorScripts();
      setScripts(data);
    } catch (e) {
      console.error("Failed to load scripts:", e);
    } finally {
      setLoading(false);
    }
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    try {
      await api.executorCreateScript(formData);
      setFormData({ name: "", description: "", category: "general", is_public: true });
      setShowForm(false);
      loadScripts();
    } catch (err) {
      alert(`Failed to create script: ${err}`);
    }
  }

  async function handleUpload(scriptId: number) {
    if (!code.trim()) {
      alert("Please enter script code");
      return;
    }
    setUploading(scriptId);
    try {
      await api.executorUploadVersion(scriptId, { version, code, changelog });
      setCode("");
      setVersion("1.0.0");
      setChangelog("");
      loadScripts();
    } catch (err) {
      alert(`Failed to upload: ${err}`);
    } finally {
      setUploading(null);
    }
  }

  async function handleDelete(id: number) {
    if (!confirm("Delete this script and all versions?")) return;
    try {
      await api.executorDeleteScript(id);
      loadScripts();
    } catch (err) {
      alert(`Failed to delete: ${err}`);
    }
  }

  if (loading && scripts.length === 0) {
    return <div style={{ color: "var(--color-neutral-500)", fontFamily: "var(--mono)" }}>Loading scripts...</div>;
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 16 }}>
        <h2 style={{ fontSize: 14, color: "var(--color-text)", fontFamily: "var(--mono)", margin: 0 }}>Scripts (Methods)</h2>
        <button
          onClick={() => setShowForm(!showForm)}
          style={{
            padding: "8px 16px", background: "var(--color-accent)", color: "#fff", border: "none", borderRadius: 4,
            fontFamily: "var(--mono)", fontSize: 12, cursor: "pointer",
          }}
        >
          {showForm ? "Cancel" : "Create Script"}
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleCreate} style={{
          marginBottom: 16, padding: 16, background: "#0f141f", borderRadius: 6,
          border: "1px solid var(--color-divider)",
        }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(200px, 1fr))", gap: 12 }}>
            <input placeholder="Script Name" value={formData.name} onChange={(e) => setFormData({ ...formData, name: e.target.value })} required
              style={{ padding: "8px 10px", background: "#0a0f18", border: "1px solid var(--color-divider)", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 12, borderRadius: 4 }} />
            <input placeholder="Description" value={formData.description} onChange={(e) => setFormData({ ...formData, description: e.target.value })}
              style={{ padding: "8px 10px", background: "#0a0f18", border: "1px solid var(--color-divider)", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 12, borderRadius: 4 }} />
            <input placeholder="Category" value={formData.category} onChange={(e) => setFormData({ ...formData, category: e.target.value })}
              style={{ padding: "8px 10px", background: "#0a0f18", border: "1px solid var(--color-divider)", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 12, borderRadius: 4 }} />
          </div>
          <label style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 12, color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 12 }}>
            <input type="checkbox" checked={formData.is_public} onChange={(e) => setFormData({ ...formData, is_public: e.target.checked })} />
            Public (visible to all users)
          </label>
          <button type="submit" style={{
            marginTop: 12, padding: "8px 16px", background: "var(--color-accent)", color: "#fff", border: "none", borderRadius: 4,
            fontFamily: "var(--mono)", fontSize: 12, cursor: "pointer",
          }}>
            Create Script
          </button>
        </form>
      )}

      <div style={{ display: "grid", gap: 12 }}>
        {scripts.map((script) => (
          <div key={script.id} style={{
            padding: 12, background: "#0f141f", borderRadius: 6, border: "1px solid var(--color-divider)",
          }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
              <div>
                <div style={{ fontFamily: "var(--mono)", fontSize: 13, color: "var(--color-text)" }}>
                  {script.name}
                </div>
                {script.description && (
                  <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--color-neutral-500)", marginTop: 4 }}>
                    {script.description}
                  </div>
                )}
                <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--color-neutral-500)", marginTop: 4 }}>
                  Category: {script.category} · Versions: {script.versions.length} · {script.is_public ? "Public" : "Private"}
                </div>
              </div>
              <button
                onClick={() => handleDelete(script.id)}
                style={{
                  padding: "6px 12px", background: "#5f2a2a", color: "#fff", border: "none", borderRadius: 4,
                  fontFamily: "var(--mono)", fontSize: 11, cursor: "pointer",
                }}
              >
                Delete
              </button>
            </div>

            {/* Upload version form */}
            <div style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--color-divider)" }}>
              <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--color-neutral-500)", marginBottom: 8 }}>
                Upload New Version
              </div>
              <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                <input
                  placeholder="Version (e.g., 1.0.0)"
                  value={version}
                  onChange={(e) => setVersion(e.target.value)}
                  style={{ width: 100, padding: "6px 8px", background: "#0a0f18", border: "1px solid var(--color-divider)", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 11, borderRadius: 4 }}
                />
                <input
                  placeholder="Changelog"
                  value={changelog}
                  onChange={(e) => setChangelog(e.target.value)}
                  style={{ flex: 1, padding: "6px 8px", background: "#0a0f18", border: "1px solid var(--color-divider)", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 11, borderRadius: 4 }}
                />
              </div>
              <textarea
                placeholder="Python script code..."
                value={code}
                onChange={(e) => setCode(e.target.value)}
                style={{
                  marginTop: 8, width: "100%", padding: "8px 10px", background: "#0a0f18", border: "1px solid var(--color-divider)",
                  color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 11, borderRadius: 4, minHeight: 150,
                }}
              />
              <button
                onClick={() => handleUpload(script.id)}
                disabled={uploading === script.id}
                style={{
                  marginTop: 8, padding: "6px 12px", background: uploading === script.id ? "#3a3f4f" : "var(--color-accent)",
                  color: "#fff", border: "none", borderRadius: 4, fontFamily: "var(--mono)", fontSize: 11,
                  cursor: uploading === script.id ? "not-allowed" : "pointer",
                }}
              >
                {uploading === script.id ? "Uploading..." : "Upload Version"}
              </button>
            </div>
          </div>
        ))}
        {scripts.length === 0 && (
          <div style={{ color: "var(--color-neutral-500)", fontFamily: "var(--mono)", fontSize: 12 }}>
            No scripts created. Click "Create Script" to add one.
          </div>
        )}
      </div>
    </div>
  );
}

// ── Chat Tab (Claude Code Integration) ──

function ChatTab() {
  const [messages, setMessages] = React.useState<{ role: "user" | "assistant"; content: string }[]>([]);
  const [input, setInput] = React.useState("");
  const [sending, setSending] = React.useState(false);

  async function handleSend(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim() || sending) return;

    const userMessage = input.trim();
    setInput("");
    setMessages((m) => [...m, { role: "user", content: userMessage }]);
    setSending(true);

    try {
      // This would call a backend endpoint that integrates with Claude Code API
      // For now, we'll simulate a response
      await new Promise((r) => setTimeout(r, 1000));
      setMessages((m) => [...m, { role: "assistant", content: "Request received. Changes will be applied via Claude Code integration." }]);
    } catch (err) {
      setMessages((m) => [...m, { role: "assistant", content: `Error: ${err}` }]);
    } finally {
      setSending(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 200px)" }}>
      <h2 style={{ fontSize: 14, color: "var(--color-text)", fontFamily: "var(--mono)", margin: "0 0 16px 0" }}>
        Website Change Requests (Claude Code)
      </h2>
      <p style={{ color: "var(--color-neutral-500)", fontFamily: "var(--mono)", fontSize: 12, marginBottom: 16 }}>
        Describe minor website changes you'd like to make. These will be processed through Claude Code integration.
      </p>

      <div style={{
        flex: 1, overflow: "auto", padding: 16, background: "#0f141f", borderRadius: 6,
        border: "1px solid var(--color-divider)", marginBottom: 12,
      }}>
        {messages.length === 0 ? (
          <div style={{ color: "var(--color-neutral-500)", fontFamily: "var(--mono)", fontSize: 12 }}>
            No messages yet. Start by describing a change you'd like to make.
          </div>
        ) : (
          messages.map((msg, i) => (
            <div key={i} style={{
              marginBottom: 12, padding: "8px 12px", background: msg.role === "user" ? "#1a2f4f" : "#1f2a3f",
              borderRadius: 4, borderLeft: `2px solid ${msg.role === "user" ? "var(--color-accent)" : "#7fce9e"}`,
            }}>
              <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--color-neutral-500)", marginBottom: 4 }}>
                {msg.role === "user" ? "You" : "Assistant"}
              </div>
              <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--color-text)", whiteSpace: "pre-wrap" }}>
                {msg.content}
              </div>
            </div>
          ))
        )}
      </div>

      <form onSubmit={handleSend} style={{ display: "flex", gap: 8 }}>
        <input
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="Describe the website change you want to make..."
          disabled={sending}
          style={{
            flex: 1, padding: "10px 12px", background: "#0a0f18", border: "1px solid var(--color-divider)",
            color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 12, borderRadius: 4,
          }}
        />
        <button
          type="submit"
          disabled={sending || !input.trim()}
          style={{
            padding: "10px 20px", background: sending || !input.trim() ? "#3a3f4f" : "var(--color-accent)",
            color: "#fff", border: "none", borderRadius: 4, fontFamily: "var(--mono)", fontSize: 12,
            cursor: sending || !input.trim() ? "not-allowed" : "pointer",
          }}
        >
          {sending ? "Sending..." : "Send Request"}
        </button>
      </form>
    </div>
  );
}

// ── Users Tab ──

function UsersTab() {
  const [users, setUsers] = React.useState<any[]>([]);
  const [loading, setLoading] = React.useState(false);

  React.useEffect(() => {
    loadUsers();
  }, []);

  async function loadUsers() {
    setLoading(true);
    try {
      const data = await api.adminUsers();
      setUsers(data.users || []);
    } catch (e) {
      console.error("Failed to load users:", e);
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    return <div style={{ color: "var(--color-neutral-500)", fontFamily: "var(--mono)" }}>Loading users...</div>;
  }

  return (
    <div>
      <h2 style={{ fontSize: 14, color: "var(--color-text)", fontFamily: "var(--mono)", margin: "0 0 16px 0" }}>
        User Management
      </h2>
      <div style={{ display: "grid", gap: 8 }}>
        {users.map((user) => (
          <div key={user.id} style={{
            padding: 10, background: "#0f141f", borderRadius: 4, border: "1px solid var(--color-divider)",
            display: "flex", justifyContent: "space-between", alignItems: "center",
          }}>
            <div>
              <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--color-text)" }}>
                {user.email} {user.username && `(${user.username})`}
              </div>
              <div style={{ fontFamily: "var(--mono)", fontSize: 10, color: "var(--color-neutral-500)" }}>
                Role: {user.role} · Tier: {user.tier} · Status: {user.status}
              </div>
            </div>
            <Link
              href={`/admin/users/${user.id}`}
              style={{
                padding: "6px 12px", background: "#2a3f5f", color: "#fff", border: "none", borderRadius: 4,
                fontFamily: "var(--mono)", fontSize: 11, textDecoration: "none",
              }}
            >
              Manage
            </Link>
          </div>
        ))}
        {users.length === 0 && (
          <div style={{ color: "var(--color-neutral-500)", fontFamily: "var(--mono)", fontSize: 12 }}>
            No users found.
          </div>
        )}
      </div>
    </div>
  );
}
