"use client";

import * as React from "react";

/**
 * hCaptcha widget — the same provider Discord uses.
 *
 * Loads the script once, renders into a container, and hands the solved token
 * up via onVerify. Rendering is explicit (`render=explicit`) rather than
 * automatic so React owns the element lifecycle and we don't fight the
 * library over a re-render.
 *
 * If no site key is configured the component renders nothing at all, so a
 * deployment without hCaptcha keys simply has no captcha rather than a broken
 * box.
 */

declare global {
  interface Window {
    hcaptcha?: {
      render: (el: HTMLElement, opts: Record<string, unknown>) => string;
      reset: (id?: string) => void;
      remove: (id: string) => void;
    };
    __hcaptchaOnLoad?: () => void;
  }
}

const SCRIPT_ID = "hcaptcha-api";
let scriptPromise: Promise<void> | null = null;

function loadScript(): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  if (window.hcaptcha) return Promise.resolve();
  if (scriptPromise) return scriptPromise;

  scriptPromise = new Promise<void>((resolve, reject) => {
    window.__hcaptchaOnLoad = () => resolve();
    const s = document.createElement("script");
    s.id = SCRIPT_ID;
    s.src = "https://js.hcaptcha.com/1/api.js?render=explicit&onload=__hcaptchaOnLoad";
    s.async = true;
    s.defer = true;
    s.onerror = () => reject(new Error("hcaptcha failed to load"));
    document.head.appendChild(s);
  });
  return scriptPromise;
}

export interface CaptchaHandle {
  reset: () => void;
}

export const Captcha = React.forwardRef<
  CaptchaHandle,
  { siteKey: string; onVerify: (token: string) => void; onExpire?: () => void }
>(function Captcha({ siteKey, onVerify, onExpire }, ref) {
  const box = React.useRef<HTMLDivElement>(null);
  const widgetId = React.useRef<string | null>(null);
  const [failed, setFailed] = React.useState(false);

  React.useImperativeHandle(ref, () => ({
    reset: () => {
      if (window.hcaptcha && widgetId.current) window.hcaptcha.reset(widgetId.current);
    },
  }));

  React.useEffect(() => {
    if (!siteKey) return;
    let cancelled = false;

    loadScript()
      .then(() => {
        if (cancelled || !box.current || !window.hcaptcha) return;
        if (widgetId.current) return; // already rendered
        widgetId.current = window.hcaptcha.render(box.current, {
          sitekey: siteKey,
          theme: "dark",
          callback: (token: string) => onVerify(token),
          "expired-callback": () => onExpire?.(),
          "error-callback": () => setFailed(true),
        });
      })
      .catch(() => setFailed(true));

    return () => {
      cancelled = true;
    };
    // Intentionally keyed only on siteKey: re-rendering the widget on every
    // parent state change would reset the user's solved challenge.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [siteKey]);

  if (!siteKey) return null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div ref={box} />
      {failed && (
        <span style={{ fontSize: 11.5, color: "#e8908f" }}>
          The captcha could not load. Disable any script blocker and refresh.
        </span>
      )}
    </div>
  );
});
