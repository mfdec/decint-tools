"use client";

import * as React from "react";
import { api } from "@/lib/api";
import { UpgradePrompt, isQuotaError } from "@/components/console/UpgradePrompt";
import type { PhoneLineType, PhoneLookupResponse, PhoneTrust } from "@/lib/types";
import { Search, Copy } from "@/components/icons";

/**
 * Phone lookup, laid out the way VeriRoute Intel's LRN API answers: one card
 * per object in its response (cnam, enhanced_lrn, lrn, messaging, trust),
 * each labelled with that object's name, and the raw answer underneath.
 */

const LINE_LABEL: Record<PhoneLineType, string> = {
  mobile: "mobile", landline: "landline", voip: "VoIP", toll_free: "toll-free", unknown: "line type unknown",
};

const TRUST_COLOR: Record<string, string> = {
  high: "var(--color-ok)", medium: "var(--color-warn)", low: "var(--color-bad)",
};

// What carriers publish when they publish no name. Most mobiles read like this.
const GENERIC_CNAM = /^(WIRELESS CALLER|CELL ?PHONE|MOBILE|UNAVAILABLE|UNKNOWN( NAME| CALLER)?|PRIVATE( CALLER)?|NOT FOUND|NO NAME|ANONYMOUS)$/i;

/** "-0500" → "UTC−05:00". */
function offset(tz: string | null): string | null {
  const m = tz?.match(/^([+-])(\d{2}):?(\d{2})$/);
  return m ? `UTC${m[1] === "-" ? "−" : "+"}${m[2]}:${m[3]}` : tz;
}

function day(ts: string | null): string | null {
  return ts ? ts.slice(0, 10) : null;
}

function Field({ k, children }: { k: string; children: React.ReactNode }) {
  if (children === null || children === undefined || children === "") return null;
  return (
    <div style={{ display: "grid", gridTemplateColumns: "104px 1fr", gap: 10, padding: "5px 0", borderTop: "1px solid color-mix(in srgb, var(--color-divider) 55%, transparent)" }}>
      <span style={{ color: "var(--color-neutral-500)", fontSize: 11.5 }}>{k}</span>
      <span style={{ color: "#e4e7f5", fontFamily: "var(--mono)", fontSize: 12, wordBreak: "break-word" }}>{children}</span>
    </div>
  );
}

/** A card for one object of VeriRoute's answer; `obj` is its key in the JSON. */
function ApiObject({ title, obj, children }: { title: string; obj: string; children: React.ReactNode }) {
  return (
    <div style={{ background: "var(--color-surface)", border: "1px solid var(--color-divider)", borderRadius: 10, padding: "12px 14px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6 }}>
        <span className="card-kicker">{title}</span>
        <code style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--color-neutral-600)" }}>{obj}</code>
      </div>
      {children}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div style={{ color: "var(--color-neutral-500)", fontSize: 12, padding: "5px 0" }}>{text}</div>;
}

function Flag({ on, label }: { on: boolean | null; label: string }) {
  if (on === null) return null;
  return <span className={`tag ${on ? "tag-bad" : "tag-neutral"}`} style={{ fontSize: 10.5 }}>{on ? label : `not ${label}`}</span>;
}

function Reputation({ t }: { t: PhoneTrust }) {
  const score = t.reputation_score;
  const color = TRUST_COLOR[t.trust_level ?? ""] ?? "var(--color-neutral-400)";
  return (
    <>
      {score !== null && (
        <div style={{ padding: "4px 0 10px" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, marginBottom: 6 }}>
            <span style={{ fontFamily: "var(--mono)", fontSize: 22, color }}>{score}</span>
            <span style={{ fontSize: 11.5, color: "var(--color-neutral-500)" }}>/ 100</span>
            {t.trust_level && <span style={{ marginLeft: "auto", fontSize: 12, color }}>{t.trust_level} trust</span>}
          </div>
          <div
            role="meter" aria-valuemin={0} aria-valuemax={100} aria-valuenow={score} aria-label="Reputation score"
            style={{ height: 6, borderRadius: 3, background: "var(--color-neutral-800)", overflow: "hidden" }}
          >
            <div style={{ width: `${Math.max(0, Math.min(100, score))}%`, height: "100%", background: color }} />
          </div>
        </div>
      )}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", padding: "2px 0 6px" }}>
        <Flag on={t.is_spam} label="spam" />
        <Flag on={t.is_robocall} label="robocall" />
        <Flag on={t.is_scam} label="scam" />
      </div>
      <Field k="Spam type">{t.spam_type}</Field>
      <Field k="Verdict">{t.verdict_status}</Field>
      <Field k="Updated">{day(t.last_updated)}</Field>
    </>
  );
}

function ResultCard({ r }: { r: PhoneLookupResponse }) {
  const [copied, setCopied] = React.useState(false);
  const e = r.enhanced_lrn;
  const asked = (k: string) => r.requested.includes(k);
  const place = e ? [e.city, e.state].filter(Boolean).join(", ") : "";
  const genericName = r.cnam ? GENERIC_CNAM.test(r.cnam.trim()) : false;
  const lrnDiffers = r.lrn !== null && r.lrn !== r.phone_number;

  function copy() {
    navigator.clipboard?.writeText(JSON.stringify(r.raw, null, 2)).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }).catch(() => { /* clipboard blocked: nothing useful to do */ });
  }

  return (
    <div style={{ border: "1px solid var(--color-divider)", borderRadius: 12, padding: 16, marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 14 }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 18, color: "#e4e7f5" }}>{r.national}</span>
        <span className="tag tag-neutral" style={{ fontFamily: "var(--mono)" }}>{r.e164}</span>
        <span className={`tag ${r.line_type === "unknown" ? "tag-neutral" : "tag-ok"}`}>{LINE_LABEL[r.line_type]}</span>
        {r.trust?.trust_level && (
          <span className={`tag ${r.trust.trust_level === "high" ? "tag-ok" : r.trust.trust_level === "low" ? "tag-bad" : "tag-warn"}`}>
            {r.trust.trust_level} trust
          </span>
        )}
        {r.trust?.spam_type && r.trust.spam_type !== "NONE" && <span className="tag tag-bad">{r.trust.spam_type.toLowerCase()}</span>}
        {place && <span style={{ color: "#b2b6ca", fontSize: 12.5 }}>{place}</span>}
        {r.cached && <span className="tag tag-neutral" title={`Looked up ${r.looked_up_at}`}>cached</span>}
        <button type="button" onClick={copy} className="btn" style={{ marginLeft: "auto", height: 28, padding: "0 10px", fontSize: 11.5, display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Copy size={12} /> {copied ? "copied" : "copy json"}
        </button>
      </div>

      {asked("cnam") && (
        <div style={{ background: "var(--color-surface)", border: "1px solid var(--color-divider)", borderRadius: 10, padding: "12px 14px", marginBottom: 12 }}>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
            <span className="card-kicker">Caller ID name</span>
            <code style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--color-neutral-600)" }}>cnam</code>
          </div>
          <div style={{ fontFamily: "var(--mono)", fontSize: 20, color: r.cnam && !genericName ? "#e4e7f5" : "#9397ab", margin: "6px 0 2px", wordBreak: "break-word" }}>
            {r.cnam ?? "no name published"}
          </div>
          <div style={{ fontSize: 11.5, color: "var(--color-neutral-500)" }}>
            {genericName
              ? "A placeholder, not a name: the carrier publishes none for this number. Usual for mobiles."
              : r.cnam
                ? "The name carriers show on caller ID, as registered by the line's owner or carrier. It can be stale or a business's chosen label."
                : "Nothing on file in the caller ID databases."}
          </div>
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))", gap: 12 }}>
        <ApiObject title="Carrier" obj="enhanced_lrn">
          {e ? (
            <>
              <Field k="Carrier">{e.carrier}</Field>
              <Field k="Type">{e.carrier_type}</Field>
              <Field k="OCN">{e.ocn}</Field>
              <Field k="LATA">{e.lata}</Field>
            </>
          ) : (
            <Empty text="no carrier record" />
          )}
        </ApiObject>

        <ApiObject title="Home location" obj="enhanced_lrn">
          {e && (e.city || e.state || e.rate_center) ? (
            <>
              <Field k="Rate center">{e.rate_center}</Field>
              <Field k="City">{e.city}</Field>
              <Field k="County">{e.county}</Field>
              <Field k="State">{e.state}</Field>
              <Field k="ZIP">
                {e.zip_code ? (
                  <a
                    href={`https://www.openstreetmap.org/search?query=${encodeURIComponent(`${e.zip_code} ${e.country_code ?? ""}`.trim())}`}
                    target="_blank" rel="noopener noreferrer" style={{ color: "var(--color-accent)" }}
                  >
                    {e.zip_code}
                  </a>
                ) : null}
              </Field>
              <Field k="Country">{e.country_code}</Field>
              <Field k="Time zone">{offset(e.timezone)}</Field>
            </>
          ) : (
            <Empty text={r.line_type === "toll_free" ? "toll-free numbers have no home location" : "no location on file"} />
          )}
          <div style={{ fontSize: 10.5, color: "var(--color-neutral-600)", paddingTop: 6 }}>
            Where the number was issued, not where its user is.
          </div>
        </ApiObject>

        <ApiObject title="Routing" obj="lrn">
          <Field k="Number">{r.phone_number}</Field>
          <Field k="LRN">
            {r.lrn ? (
              <>
                {r.lrn}{" "}
                <span
                  className={`tag ${lrnDiffers ? "tag-warn" : "tag-neutral"}`} style={{ fontSize: 9.5, marginLeft: 4 }}
                  title={lrnDiffers
                    ? "Calls are routed through another switch's number: the number was ported, or its block pooled to another carrier"
                    : "Calls route on the number itself: still on the switch it was issued from"}
                >
                  {lrnDiffers ? "routed elsewhere" : "native"}
                </span>
              </>
            ) : null}
          </Field>
          <Field k="Last ported">
            {r.lrn_activated_at ? day(r.lrn_activated_at) : <span style={{ color: "var(--color-neutral-500)" }}>never, or not published</span>}
          </Field>
        </ApiObject>

        <ApiObject title="Messaging" obj="messaging">
          {!asked("messaging") ? (
            <Empty text="not requested" />
          ) : r.messaging ? (
            <>
              <Field k="Provider">{r.messaging.provider}</Field>
              <Field k="Texts">{r.messaging.enabled === null ? null : r.messaging.enabled ? "enabled" : "not enabled"}</Field>
              <Field k="Country">{r.messaging.country}</Field>
              <Field k="Reference">{r.messaging.reference_id}</Field>
            </>
          ) : (
            <Empty text="no messaging provider on file" />
          )}
        </ApiObject>

        <ApiObject title="Reputation" obj="trust">
          {!asked("trust") ? <Empty text="not requested" /> : r.trust ? <Reputation t={r.trust} /> : <Empty text="no reputation data" />}
        </ApiObject>
      </div>

      <details style={{ marginTop: 12 }}>
        <summary style={{ cursor: "pointer", fontFamily: "var(--mono)", fontSize: 11.5, color: "#9397ab" }}>
          raw response · POST /api/v1/lrn → 200{r.cached ? " (cached)" : ""}
        </summary>
        <pre style={{ margin: "8px 0 0", padding: 12, background: "var(--color-surface)", border: "1px solid var(--color-divider)", borderRadius: 8, fontFamily: "var(--mono)", fontSize: 11.5, color: "#b2b6ca", overflow: "auto", maxHeight: 360 }}>
          {JSON.stringify(r.raw, null, 2)}
        </pre>
      </details>
    </div>
  );
}

export function PhoneApp({
  initialQuery, onConsumed,
}: {
  initialQuery?: string;
  onConsumed: () => void;
}) {
  const [query, setQuery] = React.useState(initialQuery ?? "");
  const [loading, setLoading] = React.useState(false);
  const [data, setData] = React.useState<PhoneLookupResponse | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  // A 402: the search allowance is spent. Not an error — the next step is a plan.
  const [paywall, setPaywall] = React.useState<string | null>(null);

  const doLookup = React.useCallback(async (q: string) => {
    if (!q.trim()) return;
    setLoading(true); setError(null); setPaywall(null);
    try {
      setData(await api.lookupPhone(q.trim()));
    } catch (e) {
      if (isQuotaError(e)) { setPaywall(e.message); setData(null); return; }
      setError(e instanceof Error ? e.message : "lookup failed");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (initialQuery) {
      setQuery(initialQuery);
      doLookup(initialQuery);
      onConsumed();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialQuery]);

  // The request as VeriRoute takes it: what the header line shows.
  const digits = query.replace(/\D/g, "");
  const shown = digits.length === 10 ? "1" + digits : digits;

  return (
    <div style={{ height: "100%", overflow: "auto", padding: "20px 24px" }}>
      <div style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "#75798c", marginBottom: 16, wordBreak: "break-all" }}>
        ~/phone$ POST /api/v1/lrn {`{"phone_number": "${data?.phone_number ?? (shown || "…")}", "include_enhanced_lrn": true, …}`}
      </div>

      <form
        onSubmit={(e) => { e.preventDefault(); doLookup(query); }}
        style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 16, flexWrap: "wrap" }}
      >
        <div style={{ flex: 1, minWidth: 260, display: "flex", alignItems: "center", gap: 9, height: 40, padding: "0 12px", background: "var(--color-surface)", border: "1px solid var(--color-divider)", borderRadius: 8 }}>
          <Search size={15} style={{ color: "var(--color-neutral-500)" }} />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="US or Canadian number: (336) 408-6644, +1 336 408 6644…"
            autoFocus
            spellCheck={false}
            inputMode="tel"
            style={{ flex: 1, background: "none", border: 0, outline: "none", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 13 }}
          />
        </div>
        <button type="submit" className="btn btn-primary" style={{ height: 40, padding: "0 18px" }} disabled={loading}>
          {loading ? "Looking up…" : "Run"}
        </button>
      </form>

      {error && <div className="tag tag-bad" style={{ marginBottom: 12 }}>{error}</div>}
      {paywall && <UpgradePrompt message={paywall} />}

      {data && (
        <>
          <ResultCard r={data} />
          <div style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--color-neutral-600)", lineHeight: 1.7 }}>
            {data.attribution.map((a) => <div key={a}>{a}</div>)}
          </div>
        </>
      )}

      {!data && !loading && !error && !paywall && (
        <div className="card" style={{ maxWidth: 560, marginTop: 8 }}>
          <div className="card-kicker">Phone lookup</div>
          <div className="card-title">Carrier, caller ID and reputation of a phone number</div>
          <p className="card-body">
            Enter a US or Canadian (+1) number in any format. You get the caller ID
            name carriers publish for it, the carrier now serving it and whether it is
            mobile, landline or VoIP, the rate center, city, state and ZIP it was
            issued in, its routing number and when it last ported, the provider that
            receives its texts, and a 0–100 spam reputation with any spam, robocall
            or scam reports. One lookup is one search, and paid plans include a set
            number of phone lookups each month.
          </p>
        </div>
      )}
    </div>
  );
}
