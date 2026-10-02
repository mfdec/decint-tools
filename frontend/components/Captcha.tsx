"use client";

import * as React from "react";

/**
 * Captcha widget — Google reCAPTCHA v2 (checkbox) or hCaptcha, whichever the
 * server says it is using (`provider`, from /auth/signup-info).
 *
 * Both vendors expose the same explicit-render API (`render` / `reset`, with
 * `callback` / `expired-callback` / `error-callback` options), so one component
 * serves both; only the script URL and the global differ.
 *
 * Loads the script once, renders into a container, and hands the solved token
 * up via onVerify. Rendering is explicit (`render=explicit`) rather than
 * automatic so React owns the element lifecycle and we don't fight the
 * library over a re-render.
 *
 * If no site key is configured the component renders nothing at all, so a
 * deployment without captcha keys simply has no captcha rather than a broken
 * box.
 */

export type CaptchaProvider = "hcaptcha" | "recaptcha";

/** Narrow the server's free-form provider string; anything unknown is hCaptcha. */
export const asCaptchaProvider = (v: string | undefined): CaptchaProvider =>
  v === "recaptcha" ? "recaptcha" : "hcaptcha";

interface CaptchaApi {
  render: (el: HTMLElement, opts: Record<string, unknown>) => string | number;
  reset: (id?: string | number) => void;
}

declare global {
  interface Window {
    hcaptcha?: CaptchaApi;
    grecaptcha?: CaptchaApi;
    __hcaptchaOnLoad?: () => void;
    __recaptchaOnLoad?: () => void;
  }
}

const VENDORS = {
  hcaptcha: {
    scriptId: "hcaptcha-api",
    onload: "__hcaptchaOnLoad",
    src: "https://js.hcaptcha.com/1/api.js?render=explicit&onload=__hcaptchaOnLoad",
    api: () => window.hcaptcha,
  },
  recaptcha: {
    scriptId: "recaptcha-api",
    onload: "__recaptchaOnLoad",
    src: "https://www.google.com/recaptcha/api.js?render=explicit&onload=__recaptchaOnLoad",
    // grecaptcha exists as a stub before it has finished loading; `render` only
    // appears once it is ready, so test for that rather than the global.
    api: () => (window.grecaptcha?.render ? window.grecaptcha : undefined),
  },
} as const;

const scriptPromises: Partial<Record<CaptchaProvider, Promise<void>>> = {};

function loadScript(provider: CaptchaProvider): Promise<void> {
  if (typeof window === "undefined") return Promise.resolve();
  const v = VENDORS[provider];
  if (v.api()) return Promise.resolve();
  const cached = scriptPromises[provider];
  if (cached) return cached;

  const p = new Promise<void>((resolve, reject) => {
    (window as unknown as Record<string, () => void>)[v.onload] = () => resolve();
    const s = document.createElement("script");
    s.id = v.scriptId;
    s.src = v.src;
    s.async = true;
    s.defer = true;
    s.onerror = () => {
      // Drop the rejected promise so a later mount can retry the load.
      delete scriptPromises[provider];
      s.remove();
      reject(new Error(`${provider} failed to load`));
    };
    document.head.appendChild(s);
  });
  scriptPromises[provider] = p;
  return p;
}

export interface CaptchaHandle {
  reset: () => void;
}

export const Captcha = React.forwardRef<
  CaptchaHandle,
  {
    siteKey: string;
    /** Defaults to hCaptcha, which is what this widget was first built for. */
    provider?: CaptchaProvider;
    onVerify: (token: string) => void;
    onExpire?: () => void;
  }
>(function Captcha({ siteKey, provider = "hcaptcha", onVerify, onExpire }, ref) {
  const box = React.useRef<HTMLDivElement>(null);
  const widgetId = React.useRef<string | number | null>(null);
  const [failed, setFailed] = React.useState(false);

  React.useImperativeHandle(ref, () => ({
    reset: () => {
      const api = VENDORS[provider].api();
      if (api && widgetId.current !== null) api.reset(widgetId.current);
    },
  }));

  React.useEffect(() => {
    if (!siteKey) return;
    let cancelled = false;

    loadScript(provider)
      .then(() => {
        const api = VENDORS[provider].api();
        if (cancelled || !box.current || !api) return;
        if (widgetId.current !== null) return; // already rendered
        widgetId.current = api.render(box.current, {
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
    // Intentionally keyed only on siteKey and provider: re-rendering the widget
    // on every parent state change would reset the user's solved challenge.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [siteKey, provider]);

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
