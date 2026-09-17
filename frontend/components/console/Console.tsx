"use client";

import * as React from "react";
import { api } from "@/lib/api";
import { AppKey, appByKey, appsForRole } from "@/lib/apps";
import type { CurrentUser, DarkwebMode, DiscordMode, HealthResponse, LeakKind } from "@/lib/types";
import { StatusBar } from "./StatusBar";
import { CommandPalette } from "./CommandPalette";
import { ReconApp } from "./apps/ReconApp";
import { LeaksApp } from "./apps/LeaksApp";
import { DarkwebApp } from "./apps/DarkwebApp";
import { DiscordApp } from "./apps/DiscordApp";
import { PacketsApp } from "./apps/PacketsApp";
import { VisitorsApp } from "./apps/VisitorsApp";
import { UsersApp } from "./apps/UsersApp";
import { FleetApp } from "./apps/FleetApp";

/** Per-app switches the recon shell can set from the command line. */
export interface OpenOptions {
  kind?: LeakKind;
  mode?: DarkwebMode;
  as?: DiscordMode;
}

export interface Dispatch {
  open: (key: AppKey, query?: string, opts?: OpenOptions) => void;
}

type Pending = { query: string; opts?: OpenOptions };

export function Console() {
  const [active, setActive] = React.useState<AppKey>("recon");
  const [menuOpen, setMenuOpen] = React.useState(false);
  const [paletteOpen, setPaletteOpen] = React.useState(false);
  const [health, setHealth] = React.useState<HealthResponse | null>(null);
  // The middleware only checks that a cookie exists; this asks the backend
  // whether it's actually valid. Nothing renders until it says yes.
  const [gate, setGate] = React.useState<"checking" | "in">("checking");
  // per-app "run this query on entry", consumed by the target app.
  const [pending, setPending] = React.useState<Partial<Record<AppKey, Pending>>>({});

  const [me, setMe] = React.useState<CurrentUser | null>(null);

  React.useEffect(() => {
    (async () => {
      const session = await api.session().catch(() => null);
      if (!session?.authenticated) {
        window.location.replace("/login?next=/console");
        return;
      }
      setMe(session.user ?? null);
      setGate("in");
      api.health().then(setHealth).catch(() => setHealth(null));
    })();
  }, []);

  // Role decides which apps exist at all. The server enforces this too — this
  // only keeps the UI honest.
  const apps = React.useMemo(
    () => appsForRole(me?.role, health?.sniffer_enabled),
    [me, health]
  );

  const open = React.useCallback(
    (key: AppKey, query?: string, opts?: OpenOptions) => {
      if (query !== undefined) setPending((p) => ({ ...p, [key]: { query, opts } }));
      setActive(key);
      setMenuOpen(false);
      setPaletteOpen(false);
    },
    []
  );

  const consumePending = React.useCallback((key: AppKey) => {
    setPending((p) => {
      if (!(key in p)) return p;
      const next = { ...p };
      delete next[key];
      return next;
    });
  }, []);

  React.useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && (e.key === "k" || e.key === "K")) {
        e.preventDefault();
        setPaletteOpen((v) => !v);
        setMenuOpen(false);
        return;
      }
      if (e.key === "Escape") {
        setPaletteOpen(false);
        setMenuOpen(false);
        return;
      }
      if (e.ctrlKey && "1234567".includes(e.key)) {
        const target = apps[Number(e.key) - 1];
        if (target) {
          e.preventDefault();
          open(target.key);
        }
      }
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [apps, open]);

  const dispatch: Dispatch = { open };
  const initial = (k: AppKey) => pending[k]?.query;
  const initialOpts = (k: AppKey) => pending[k]?.opts;

  if (gate === "checking") {
    return (
      <div
        style={{
          position: "fixed", inset: 0, display: "grid", placeItems: "center",
          background: "#0a0f18", color: "var(--color-neutral-600)",
          fontFamily: "var(--mono)", fontSize: 12.5,
        }}
      >
        verifying session…
      </div>
    );
  }

  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        background:
          "radial-gradient(900px 460px at 88% -12%, color-mix(in srgb, var(--color-accent-900) 55%, transparent), transparent 62%), #0a0f18",
        color: "var(--color-text)",
      }}
    >
      <StatusBar
        apps={apps}
        active={active}
        menuOpen={menuOpen}
        health={health}
        onToggleMenu={() => setMenuOpen((v) => !v)}
        onCloseMenu={() => setMenuOpen(false)}
        onSelect={(k) => open(k)}
        onOpenPalette={() => setPaletteOpen(true)}
      />

      <div style={{ position: "relative", zIndex: 1, flex: 1, overflow: "hidden" }}>
        {active === "recon" && (
          <ReconApp dispatch={dispatch} health={health} apps={apps} />
        )}
        {active === "leaks" && (
          <LeaksApp initialQuery={initial("leaks")} initialKind={initialOpts("leaks")?.kind} onConsumed={() => consumePending("leaks")} />
        )}
        {active === "darkweb" && (
          <DarkwebApp initialQuery={initial("darkweb")} initialMode={initialOpts("darkweb")?.mode} onConsumed={() => consumePending("darkweb")} health={health} />
        )}
        {active === "discord" && (
          <DiscordApp initialQuery={initial("discord")} initialMode={initialOpts("discord")?.as} onConsumed={() => consumePending("discord")} health={health} />
        )}
        {active === "packets" && <PacketsApp health={health} />}
        {active === "visitors" && <VisitorsApp />}
        {active === "users" && <UsersApp />}
        {active === "fleet" && <FleetApp />}
      </div>

      {paletteOpen && (
        <CommandPalette
          apps={apps}
          active={active}
          onSelect={(k) => open(k)}
          onClose={() => setPaletteOpen(false)}
        />
      )}
    </div>
  );
}

export { appByKey };
