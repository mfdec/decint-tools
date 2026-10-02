"use client";

import * as React from "react";
import Link from "next/link";
import { api } from "@/lib/api";
import { Wordmark } from "@/components/site/Wordmark";
import { NAV_LINKS } from "@/lib/site";
import type { SessionResponse } from "@/lib/types";

/**
 * Public-site header.
 *
 * The auth slot is session-aware: it asks the API whether this browser holds a
 * valid session and, if so, offers the console instead of Sign in / Sign up.
 * The check runs client-side on purpose — the marketing pages are statically
 * rendered, so their markup cannot vary per visitor at build time.
 *
 * `undefined` means "still asking". We hold an empty slot of the same width in
 * that state rather than guessing, so a signed-in visitor never sees "Sign in"
 * flash before it corrects itself. Guessing wrong in either direction looks
 * broken; a brief gap does not.
 *
 * The console link only appears behind a real session. Anyone typing /console
 * without one is still bounced by middleware.ts, and the backend re-verifies
 * the cookie signature on every /api/v1 call — this is a convenience, not the
 * gate.
 */
export function SiteNav({ current }: { current?: string }) {
  const [sess, setSess] = React.useState<SessionResponse | undefined>();

  React.useEffect(() => {
    let alive = true;
    api
      .session()
      .then((s) => alive && setSess(s))
      .catch(() => alive && setSess({ authenticated: false }));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <header
      style={{
        position: "sticky",
        top: 0,
        zIndex: 20,
        borderBottom: "1px solid var(--color-divider)",
        background: "color-mix(in srgb, var(--color-bg) 82%, transparent)",
        backdropFilter: "blur(12px)",
        WebkitBackdropFilter: "blur(12px)",
      }}
    >
      <nav className="nav" style={{ maxWidth: 1080, margin: "0 auto" }}>
        <Link href="/" className="nav-brand" aria-label="DECINT — home">
          <Wordmark />
        </Link>

        {NAV_LINKS.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            aria-current={current === l.href ? "page" : undefined}
            style={{ color: current === l.href ? "var(--color-accent)" : undefined }}
          >
            {l.label}
          </Link>
        ))}

        {sess === undefined ? (
          <span style={{ minWidth: 172 }} aria-hidden />
        ) : sess.authenticated ? (
          <>
            <Link
              href="/account"
              className="nav-user"
              title={`${sess.user?.email ?? "Your profile"} — your profile`}
              aria-current={current === "/account" ? "page" : undefined}
            >
              {sess.user?.username || sess.user?.email}
            </Link>
            <Link href="/console" className="btn btn-primary">Console</Link>
            <button
              type="button"
              className="btn btn-ghost"
              onClick={async () => {
                await api.logout().catch(() => {});
                // Full reload rather than a router push: the static marketing
                // pages would otherwise keep the stale signed-in nav in memory.
                window.location.assign("/");
              }}
            >
              Sign out
            </button>
          </>
        ) : (
          <>
            <Link href="/login" className="btn btn-ghost">Sign in</Link>
            <Link href="/signup" className="btn btn-primary">Sign up</Link>
          </>
        )}
      </nav>
    </header>
  );
}
