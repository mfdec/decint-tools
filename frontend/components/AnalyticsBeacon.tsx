"use client";

import * as React from "react";
import { usePathname, useSearchParams } from "next/navigation";

/**
 * Visitor beacon. Reports a pageview on every route change and a click event
 * for links and buttons, together with the environment signals a browser is
 * willing to expose (screen, GPU, cores, memory, timezone, connection).
 *
 * IP, geography, network operator and User-Agent breakdown are resolved
 * server-side from the request — they are not sent from here.
 *
 * Uses sendBeacon where available so a click that navigates away still gets
 * recorded; the fetch fallback is keepalive for the same reason.
 */

const ENDPOINT = "/api/v1/analytics/collect";

function sessionId(): string {
  try {
    const k = "decint_sid";
    let v = sessionStorage.getItem(k);
    if (!v) {
      v = Math.random().toString(36).slice(2) + Date.now().toString(36);
      sessionStorage.setItem(k, v);
    }
    return v;
  } catch {
    return "";
  }
}

/** WebGL renderer string — the closest thing the browser gives us to "what
 *  hardware is this". Wrapped because it throws in hardened/private modes. */
function gpu(): string | undefined {
  try {
    const c = document.createElement("canvas");
    const gl = (c.getContext("webgl") ||
      c.getContext("experimental-webgl")) as WebGLRenderingContext | null;
    if (!gl) return undefined;
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    const r = ext
      ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL)
      : gl.getParameter(gl.RENDERER);
    return typeof r === "string" ? r : undefined;
  } catch {
    return undefined;
  }
}

function environment() {
  const nav = navigator as Navigator & {
    deviceMemory?: number;
    connection?: { effectiveType?: string };
  };
  return {
    session_id: sessionId(),
    screen_w: screen.width,
    screen_h: screen.height,
    viewport_w: window.innerWidth,
    viewport_h: window.innerHeight,
    pixel_ratio: window.devicePixelRatio,
    color_depth: screen.colorDepth,
    cpu_cores: nav.hardwareConcurrency,
    device_memory: nav.deviceMemory,
    gpu: gpu(),
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    language: navigator.language,
    languages: (navigator.languages || []).slice(0, 5).join(","),
    touch_points: navigator.maxTouchPoints,
    connection: nav.connection?.effectiveType,
  };
}

function send(body: Record<string, unknown>) {
  try {
    const json = JSON.stringify(body);
    if (navigator.sendBeacon) {
      navigator.sendBeacon(ENDPOINT, new Blob([json], { type: "application/json" }));
      return;
    }
    fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: json,
      keepalive: true,
    }).catch(() => {});
  } catch {
    /* analytics must never break the page */
  }
}

export function AnalyticsBeacon() {
  const pathname = usePathname();
  const search = useSearchParams();

  // Pageview on mount and on every client-side navigation.
  React.useEffect(() => {
    send({
      event: "pageview",
      path: pathname,
      query: search?.toString() || "",
      referrer: document.referrer,
      title: document.title,
      ...environment(),
    });
  }, [pathname, search]);

  // Clicks on interactive elements.
  React.useEffect(() => {
    function onClick(e: MouseEvent) {
      const el = (e.target as HTMLElement | null)?.closest(
        "a, button, [role=button]"
      ) as HTMLElement | null;
      if (!el) return;

      const href = el.getAttribute("href") || undefined;
      let outbound = false;
      if (href && /^https?:\/\//i.test(href)) {
        try {
          outbound = new URL(href).host !== window.location.host;
        } catch {
          /* ignore */
        }
      }

      send({
        event: outbound ? "outbound" : "click",
        path: pathname,
        title: document.title,
        session_id: sessionId(),
        meta: {
          tag: el.tagName.toLowerCase(),
          text: (el.innerText || "").trim().slice(0, 80),
          href,
          id: el.id || undefined,
          cls: el.className?.toString().slice(0, 80) || undefined,
        },
      });
    }
    document.addEventListener("click", onClick, true);
    return () => document.removeEventListener("click", onClick, true);
  }, [pathname]);

  return null;
}
