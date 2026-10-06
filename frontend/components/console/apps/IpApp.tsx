"use client";

import * as React from "react";
import { api } from "@/lib/api";
import { UpgradePrompt, isQuotaError } from "@/components/console/UpgradePrompt";
import type { IpLookupResponse, IpResult, IpScope } from "@/lib/types";
import { Search, Copy } from "@/components/icons";

/** Why there is nothing to look up for an address that isn't on the internet. */
const SCOPE_NOTE: Record<Exclude<IpScope, "public">, string> = {
  private: "a private address (RFC 1918 / unique local). It only means something inside one network",
  loopback: "the loopback address: the machine itself",
  link_local: "a link-local address, only valid on one network segment",
  multicast: "a multicast group address, not a host",
  reserved: "a reserved address that is not routed on the internet",
  unspecified: "the unspecified address, which names no host",
  shared: "carrier-grade NAT space (RFC 6598), shared behind an ISP's NAT",
  documentation: "reserved for documentation and examples",
};

const ERROR_LABEL: Record<string, string> = {
  location: "location", network: "network", rdap: "registry", ptr: "reverse DNS", reputation: "reputation",
};

/** AbuseIPDB's confidence score, read the way its own site colours it. */
function scoreTag(score: number): { cls: string; text: string } {
  if (score >= 75) return { cls: "tag-bad", text: `abuse score ${score}%` };
  if (score >= 25) return { cls: "tag-warn", text: `abuse score ${score}%` };
  if (score > 0) return { cls: "tag-neutral", text: `abuse score ${score}%` };
  return { cls: "tag-ok", text: "no abuse reported" };
}

function flag(cc: string | null): string {
  if (!cc || !/^[A-Z]{2}$/.test(cc)) return "";
  return String.fromCodePoint(...[...cc].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65));
}

/** The date part of an RDAP timestamp; registries disagree on the rest. */
function day(ts: string | null): string | null {
  return ts ? ts.slice(0, 10) : null;
}

/** Attribution text with the sources' domains as links, as their licences ask. */
function Attribution({ text }: { text: string }) {
  const parts = text.split(/(db-ip\.com|maxmind\.com|rdap\.org|abuseipdb\.com)/);
  return (
    <>
      {parts.map((p, i) =>
        /^(db-ip\.com|maxmind\.com|rdap\.org|abuseipdb\.com)$/.test(p) ? (
          <a key={i} href={`https://${p}`} target="_blank" rel="noopener noreferrer" style={{ color: "var(--color-neutral-400)" }}>{p}</a>
        ) : (
          <React.Fragment key={i}>{p}</React.Fragment>
        )
      )}
    </>
  );
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

function Section({ title, note, children }: { title: string; note?: string; children: React.ReactNode }) {
  return (
    <div style={{ background: "var(--color-surface)", border: "1px solid var(--color-divider)", borderRadius: 10, padding: "12px 14px" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6 }}>
        <span className="card-kicker">{title}</span>
        {note && <span style={{ fontSize: 10.5, color: "var(--color-neutral-600)" }}>{note}</span>}
      </div>
      {children}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div style={{ color: "var(--color-neutral-500)", fontSize: 12, padding: "5px 0" }}>{text}</div>;
}

function ResultCard({ r }: { r: IpResult }) {
  const [copied, setCopied] = React.useState(false);
  const loc = r.location;
  const net = r.network;
  const reg = r.registration;
  const rep = r.reputation;
  const place = loc ? [loc.city, loc.region, loc.country].filter(Boolean).join(", ") : "";

  function copy() {
    navigator.clipboard?.writeText(JSON.stringify(r, null, 2)).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }).catch(() => { /* clipboard blocked: nothing useful to do */ });
  }

  return (
    <div style={{ border: "1px solid var(--color-divider)", borderRadius: 12, padding: 16, marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 16, color: "#e4e7f5", wordBreak: "break-all" }}>{r.ip}</span>
        <span className="tag tag-neutral">IPv{r.version}</span>
        {r.scope !== "public" && <span className="tag tag-warn">{r.scope.replace("_", "-")}</span>}
        {r.tor_exit === true && <span className="tag tag-bad" title="Listed in the Tor Project's current exit list">Tor exit node</span>}
        {r.tor_exit === false && <span className="tag tag-neutral" title="Not in the Tor Project's current exit list">not a Tor exit</span>}
        {rep && rep.abuse_score > 0 && (
          <span className={`tag ${scoreTag(rep.abuse_score).cls}`} title="AbuseIPDB confidence of abuse">{scoreTag(rep.abuse_score).text}</span>
        )}
        {place && <span style={{ color: "#b2b6ca", fontSize: 12.5 }}>{flag(loc?.country_code ?? null)} {place}</span>}
        <button type="button" onClick={copy} className="btn" style={{ marginLeft: "auto", height: 28, padding: "0 10px", fontSize: 11.5, display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Copy size={12} /> {copied ? "copied" : "copy json"}
        </button>
      </div>

      {r.scope !== "public" ? (
        <div style={{ color: "#b2b6ca", fontSize: 13 }}>
          This is {SCOPE_NOTE[r.scope]}. No registry or location database has anything to say
          about it, so it was not sent anywhere.
        </div>
      ) : (
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(270px, 1fr))", gap: 12 }}>
          <Section title="Location" note="approximate · city level at best">
            {loc ? (
              <>
                <Field k="City">{loc.city}</Field>
                <Field k="Region">{loc.region}</Field>
                <Field k="Country">{loc.country ? `${flag(loc.country_code)} ${loc.country}${loc.country_code ? ` (${loc.country_code})` : ""}` : null}</Field>
                <Field k="Continent">{loc.continent ? `${loc.continent}${loc.in_eu ? " · EU" : ""}` : null}</Field>
                <Field k="Coordinates">
                  {loc.latitude !== null && loc.longitude !== null ? (
                    <a
                      href={`https://www.openstreetmap.org/?mlat=${loc.latitude}&mlon=${loc.longitude}#map=10/${loc.latitude}/${loc.longitude}`}
                      target="_blank" rel="noopener noreferrer" style={{ color: "var(--color-accent)" }}
                    >
                      {loc.latitude.toFixed(4)}, {loc.longitude.toFixed(4)}
                    </a>
                  ) : null}
                </Field>
                <Field k="Accuracy">{loc.accuracy_km !== null ? `± ${loc.accuracy_km} km` : null}</Field>
                <Field k="Time zone">{loc.timezone}</Field>
              </>
            ) : (
              <Empty text={r.errors.location ? "unavailable" : "not in the location database"} />
            )}
          </Section>

          <Section title="Network / ISP">
            {net ? (
              <>
                <Field k="Operator">{net.as_org}</Field>
                <Field k="ASN">
                  {net.asn !== null ? (
                    <a href={`https://bgp.he.net/AS${net.asn}`} target="_blank" rel="noopener noreferrer" style={{ color: "var(--color-accent)" }}>AS{net.asn}</a>
                  ) : null}
                </Field>
                <Field k="Routed block">{net.prefix}</Field>
              </>
            ) : (
              <Empty text={r.errors.network ? "unavailable" : "not announced in the ASN database"} />
            )}
            <Field k="Reverse DNS">
              {r.ptr ? (
                <>
                  {r.ptr}{" "}
                  <span
                    className={`tag ${r.ptr_confirmed ? "tag-ok" : "tag-warn"}`}
                    style={{ fontSize: 9.5, marginLeft: 4 }}
                    title={r.ptr_confirmed
                      ? "The name resolves back to this address"
                      : "The name does not resolve back to this address: whoever runs the reverse zone can put any name there"}
                  >
                    {r.ptr_confirmed ? "confirmed" : "unconfirmed"}
                  </span>
                </>
              ) : r.errors.ptr ? null : (
                <span style={{ color: "var(--color-neutral-500)" }}>no PTR record</span>
              )}
            </Field>
          </Section>

          <Section title="Registration" note={reg?.registry ? `via ${reg.registry}` : undefined}>
            {reg ? (
              <>
                <Field k="Holder">{reg.org}</Field>
                <Field k="Address">{reg.org_address}</Field>
                <Field k="Net name">{reg.name}</Field>
                <Field k="Range">{reg.cidrs.length ? reg.cidrs.join(", ") : reg.range}</Field>
                <Field k="Type">{reg.type}</Field>
                <Field k="Country">{reg.country}</Field>
                <Field k="Abuse">
                  {reg.abuse_email ? (
                    <a href={`mailto:${reg.abuse_email}`} style={{ color: "var(--color-accent)" }}>{reg.abuse_email}</a>
                  ) : null}
                </Field>
                <Field k="Registered">{day(reg.registered)}</Field>
                <Field k="Last changed">{day(reg.last_changed)}</Field>
                <Field k="Handle">{reg.handle}</Field>
              </>
            ) : (
              <Empty text="unavailable" />
            )}
          </Section>

          {(rep || r.errors.reputation) && (
            <Section title="Reputation" note={rep ? `AbuseIPDB · last ${rep.max_age_days} days` : "AbuseIPDB"}>
              {rep ? (
                <>
                  <Field k="Abuse score">
                    <span className={`tag ${scoreTag(rep.abuse_score).cls}`} style={{ fontSize: 10.5 }}>{scoreTag(rep.abuse_score).text}</span>
                  </Field>
                  <Field k="Reports">
                    {rep.total_reports
                      ? `${rep.total_reports.toLocaleString()} from ${rep.distinct_reporters.toLocaleString()} reporter${rep.distinct_reporters === 1 ? "" : "s"}`
                      : "none"}
                  </Field>
                  <Field k="Last reported">{day(rep.last_reported)}</Field>
                  <Field k="Usage">{rep.usage_type}</Field>
                  <Field k="ISP">{rep.isp}</Field>
                  <Field k="Domain">{rep.domain}</Field>
                  <Field k="Allow-listed">{rep.whitelisted ? "yes: AbuseIPDB treats it as a known-good service" : null}</Field>
                  <Field k="Details">
                    <a href={`https://www.abuseipdb.com/check/${encodeURIComponent(r.ip)}`} target="_blank" rel="noopener noreferrer" style={{ color: "var(--color-accent)" }}>
                      the reports on abuseipdb.com →
                    </a>
                  </Field>
                </>
              ) : (
                <Empty text="unavailable" />
              )}
            </Section>
          )}
        </div>
      )}

      {Object.keys(r.errors).length > 0 && (
        <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
          {Object.entries(r.errors).map(([k, v]) => (
            <span key={k} className="tag tag-bad" style={{ fontSize: 10.5 }}>{ERROR_LABEL[k] || k}: {v}</span>
          ))}
        </div>
      )}
    </div>
  );
}

export function IpApp({
  initialQuery, onConsumed,
}: {
  initialQuery?: string;
  onConsumed: () => void;
}) {
  const [query, setQuery] = React.useState(initialQuery ?? "");
  const [loading, setLoading] = React.useState(false);
  const [data, setData] = React.useState<IpLookupResponse | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  // A 402: the search allowance is spent. Not an error — the next step is a plan.
  const [paywall, setPaywall] = React.useState<string | null>(null);

  const doLookup = React.useCallback(async (q: string) => {
    if (!q.trim()) return;
    setLoading(true); setError(null); setPaywall(null);
    try {
      setData(await api.lookupIp(q.trim()));
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

  return (
    <div style={{ height: "100%", overflow: "auto", padding: "20px 24px" }}>
      <div style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "#75798c", marginBottom: 16 }}>
        ~/ip$ lookup {query ? query : "--help"}
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
            placeholder="IP address (v4 or v6), hostname or URL…"
            autoFocus
            spellCheck={false}
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
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
            {data.sources.map((s) => (
              <span key={s.key} className={`tag ${s.ok ? "tag-ok" : "tag-bad"}`} title={s.status}>{s.label}</span>
            ))}
          </div>

          {data.kind === "hostname" && (
            <div style={{ fontFamily: "var(--mono)", fontSize: 12, color: "#9397ab", marginBottom: 12 }}>
              {data.hostname} resolves to {data.results.length + data.more_addresses.length} address
              {data.results.length + data.more_addresses.length === 1 ? "" : "es"}
              {data.more_addresses.length > 0 && (
                <> · showing the first {data.results.length}; also {data.more_addresses.join(", ")}</>
              )}
            </div>
          )}

          {data.results.map((r) => <ResultCard key={r.ip} r={r} />)}

          <div style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--color-neutral-600)", lineHeight: 1.7 }}>
            {data.attribution.map((a) => <div key={a}><Attribution text={a} /></div>)}
          </div>
        </>
      )}

      {!data && !loading && !error && !paywall && (
        <div className="card" style={{ maxWidth: 560, marginTop: 8 }}>
          <div className="card-kicker">IP lookup</div>
          <div className="card-title">Location, network and owner of an IP address</div>
          <p className="card-body">
            Enter an IPv4 or IPv6 address, a hostname or a URL. You get an approximate
            location (city, region, country, coordinates), the network operator and
            ASN, the registry record (holder, registered range, abuse contact),
            reverse DNS checked against forward DNS, and whether the address is a
            current Tor exit node, with its abuse-report reputation where the server
            has AbuseIPDB switched on. A hostname is resolved and each of its addresses
            looked up. One lookup is one search.
          </p>
        </div>
      )}
    </div>
  );
}
