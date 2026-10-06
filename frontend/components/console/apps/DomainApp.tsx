"use client";

import * as React from "react";
import type { Dispatch } from "../Console";
import { api } from "@/lib/api";
import { UpgradePrompt, isQuotaError } from "@/components/console/UpgradePrompt";
import type { DomainLookupResponse, DomainWebsite, MailProtection } from "@/lib/types";
import { Search, Copy } from "@/components/icons";

const ERROR_LABEL: Record<string, string> = {
  dns: "DNS", rdap: "registry", certificates: "certificate logs", website: "website", archive: "archive",
};

const PROTECTION: Record<MailProtection, { cls: string; text: string }> = {
  strong: { cls: "tag-ok", text: "mail: spoofing blocked" },
  partial: { cls: "tag-warn", text: "mail: partly protected" },
  weak: { cls: "tag-bad", text: "mail: weakly protected" },
  none: { cls: "tag-bad", text: "mail: unprotected" },
};

const HEADER_LABEL: Record<string, string> = {
  "strict-transport-security": "HSTS",
  "content-security-policy": "CSP",
  "x-frame-options": "X-Frame-Options",
  "x-content-type-options": "X-Content-Type-Options",
  "referrer-policy": "Referrer-Policy",
  "permissions-policy": "Permissions-Policy",
};

const SUBDOMAINS_SHOWN = 40;

function flag(cc: string | null): string {
  if (!cc || !/^[A-Z]{2}$/.test(cc)) return "";
  return String.fromCodePoint(...[...cc].map((c) => 0x1f1e6 + c.charCodeAt(0) - 65));
}

function day(ts: string | null): string | null {
  return ts ? ts.slice(0, 10) : null;
}

function daysUntil(ts: string | null): number | null {
  if (!ts) return null;
  const t = Date.parse(ts);
  return Number.isNaN(t) ? null : Math.floor((t - Date.now()) / 86400000);
}

/** Attribution text with the sources' domains as links. */
function Attribution({ text }: { text: string }) {
  const re = /(db-ip\.com|maxmind\.com|rdap\.org|sslmate\.com|archive\.org)/;
  return (
    <>
      {text.split(re).map((p, i) =>
        re.test(p) ? (
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
    <div style={{ display: "grid", gridTemplateColumns: "112px 1fr", gap: 10, padding: "5px 0", borderTop: "1px solid color-mix(in srgb, var(--color-divider) 55%, transparent)" }}>
      <span style={{ color: "var(--color-neutral-500)", fontSize: 11.5 }}>{k}</span>
      <span style={{ color: "#e4e7f5", fontFamily: "var(--mono)", fontSize: 12, wordBreak: "break-word" }}>{children}</span>
    </div>
  );
}

function Section({ title, note, wide, children }: { title: string; note?: string; wide?: boolean; children: React.ReactNode }) {
  return (
    <div style={{ background: "var(--color-surface)", border: "1px solid var(--color-divider)", borderRadius: 10, padding: "12px 14px", gridColumn: wide ? "1 / -1" : undefined }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 6, gap: 10 }}>
        <span className="card-kicker">{title}</span>
        {note && <span style={{ fontSize: 10.5, color: "var(--color-neutral-600)", textAlign: "right" }}>{note}</span>}
      </div>
      {children}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <div style={{ color: "var(--color-neutral-500)", fontSize: 12, padding: "5px 0" }}>{text}</div>;
}

function Tag({ cls, children, title }: { cls: string; children: React.ReactNode; title?: string }) {
  return <span className={`tag ${cls}`} style={{ fontSize: 10.5 }} title={title}>{children}</span>;
}

function Link({ href, children }: { href: string; children: React.ReactNode }) {
  return <a href={href} target="_blank" rel="noopener noreferrer nofollow" style={{ color: "var(--color-accent)" }}>{children}</a>;
}

function WebsiteSection({ w }: { w: DomainWebsite | null }) {
  if (!w) return <Section title="Website"><Empty text="unavailable" /></Section>;
  const tls = w.tls;
  const missing = Object.entries(w.security_headers).filter(([, v]) => v === null).map(([k]) => HEADER_LABEL[k] ?? k);
  const present = Object.entries(w.security_headers).filter(([, v]) => v !== null);
  return (
    <Section title="Website" note={w.ip ? `fetched from ${w.ip}` : undefined}>
      {w.status === null && <Empty text={w.note ?? "not fetched"} />}
      <Field k="Status">{w.status !== null ? `HTTP ${w.status}${w.response_ms !== null ? ` · ${w.response_ms} ms` : ""}` : null}</Field>
      <Field k="Final URL">{w.final_url ? <Link href={w.final_url}>{w.final_url}</Link> : null}</Field>
      <Field k="Redirects">
        {w.redirects.length ? w.redirects.map((h, i) => (
          <div key={i}>{h.status} {h.url} → {h.location}</div>
        )) : null}
      </Field>
      <Field k="HTTP → HTTPS">
        {w.https_redirect === null ? null : w.https_redirect
          ? <Tag cls="tag-ok">redirects to HTTPS</Tag>
          : <Tag cls="tag-warn" title="Plain-HTTP visitors are not sent on to HTTPS">no redirect</Tag>}
      </Field>
      <Field k="Title">{w.title}</Field>
      <Field k="Server">{w.server}</Field>
      <Field k="Powered by">{w.powered_by}</Field>
      <Field k="Generator">{w.generator}</Field>
      {w.status !== null && (
        <Field k="Security headers">
          <span style={{ display: "inline-flex", gap: 5, flexWrap: "wrap" }}>
            {present.map(([k, v]) => <Tag key={k} cls="tag-ok" title={v ?? ""}>{HEADER_LABEL[k] ?? k}</Tag>)}
            {missing.map((k) => <Tag key={k} cls="tag-neutral" title="Not sent">{k} ✕</Tag>)}
          </span>
        </Field>
      )}
      {tls && (
        <>
          <Field k="TLS">
            {tls.valid
              ? <Tag cls="tag-ok">valid certificate</Tag>
              : <Tag cls="tag-bad" title={tls.error ?? ""}>invalid: {tls.error}</Tag>}
            {tls.version ? <span style={{ marginLeft: 8, color: "#9397ab" }}>{tls.version}</span> : null}
          </Field>
          <Field k="Issuer">{tls.issuer}</Field>
          <Field k="Expires">
            {tls.not_after ? (
              <>
                {day(tls.not_after)}{" "}
                {tls.days_left !== null && (
                  <Tag cls={tls.days_left < 0 ? "tag-bad" : tls.days_left < 14 ? "tag-warn" : "tag-neutral"}>
                    {tls.days_left < 0 ? `expired ${-tls.days_left} d ago` : `${tls.days_left} d left`}
                  </Tag>
                )}
              </>
            ) : null}
          </Field>
          <Field k="Covers">{tls.names.length ? tls.names.slice(0, 8).join(", ") + (tls.names.length > 8 ? ` +${tls.names.length - 8}` : "") : null}</Field>
        </>
      )}
      {w.status !== null && w.note && <div style={{ color: "#e3c07b", fontSize: 11.5, marginTop: 6 }}>{w.note}</div>}
    </Section>
  );
}

function Result({ d, onDomain, onIp }: { d: DomainLookupResponse; onDomain: (q: string) => void; onIp: (ip: string) => void }) {
  const [copied, setCopied] = React.useState(false);
  const [allSubs, setAllSubs] = React.useState(false);
  const reg = d.registration;
  const mail = d.email;
  const certs = d.certificates;
  const expiresIn = daysUntil(reg?.expires ?? null);

  function copy() {
    navigator.clipboard?.writeText(JSON.stringify(d, null, 2)).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }).catch(() => { /* clipboard blocked: nothing useful to do */ });
  }

  const subs = certs ? (allSubs ? certs.subdomains : certs.subdomains.slice(0, SUBDOMAINS_SHOWN)) : [];

  return (
    <div style={{ border: "1px solid var(--color-divider)", borderRadius: 12, padding: 16, marginBottom: 16 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 12 }}>
        <span style={{ fontFamily: "var(--mono)", fontSize: 16, color: "#e4e7f5", wordBreak: "break-all" }}>{d.domain}</span>
        {d.exists === false && <span className="tag tag-bad" title="The DNS says this name does not exist (NXDOMAIN)">does not exist</span>}
        {d.registered_domain && d.registered_domain !== d.domain && (
          <button type="button" className="tag tag-neutral" style={{ cursor: "pointer", border: 0 }} onClick={() => onDomain(d.registered_domain!)}
            title="Look up the registered domain">part of {d.registered_domain}</button>
        )}
        {d.dnssec_validated && <span className="tag tag-ok" title="The resolver validated these answers with DNSSEC">DNSSEC</span>}
        {mail && <span className={`tag ${PROTECTION[mail.protection].cls}`}>{PROTECTION[mail.protection].text}</span>}
        {expiresIn !== null && expiresIn < 30 && (
          <span className="tag tag-warn">{expiresIn < 0 ? "registration expired" : `expires in ${expiresIn} d`}</span>
        )}
        <button type="button" onClick={copy} className="btn" style={{ marginLeft: "auto", height: 28, padding: "0 10px", fontSize: 11.5, display: "inline-flex", alignItems: "center", gap: 6 }}>
          <Copy size={12} /> {copied ? "copied" : "copy json"}
        </button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 12 }}>
        <Section title="Registration" note={reg?.registry ? `via ${reg.registry}` : undefined}>
          {reg ? (
            <>
              <Field k="Domain">{reg.domain !== d.domain ? reg.domain : null}</Field>
              <Field k="Registrar">{reg.registrar ? `${reg.registrar}${reg.registrar_iana_id ? ` (IANA ${reg.registrar_iana_id})` : ""}` : null}</Field>
              <Field k="Registrant">{reg.registrant ?? (reg.registrant_redacted ? <span style={{ color: "#9397ab" }}>redacted for privacy</span> : null)}</Field>
              <Field k="Created">{day(reg.created)}</Field>
              <Field k="Expires">{reg.expires ? `${day(reg.expires)}${expiresIn !== null ? ` · ${expiresIn < 0 ? "expired" : `${expiresIn} d left`}` : ""}` : null}</Field>
              <Field k="Updated">{day(reg.updated)}</Field>
              <Field k="Name servers">{reg.nameservers.length ? reg.nameservers.join(", ") : null}</Field>
              <Field k="DNSSEC">{reg.dnssec === null ? null : reg.dnssec ? "signed" : "not signed"}</Field>
              <Field k="Status">{reg.status.length ? reg.status.join(", ") : null}</Field>
              <Field k="Abuse">
                {reg.registrar_abuse_email ? <a href={`mailto:${reg.registrar_abuse_email}`} style={{ color: "var(--color-accent)" }}>{reg.registrar_abuse_email}</a> : null}
              </Field>
            </>
          ) : (
            <Empty text={d.errors.rdap ? "unavailable" : "no registration record: the domain may not be registered"} />
          )}
        </Section>

        <WebsiteSection w={d.website} />

        <Section title="Mail security" note={mail?.dmarc_inherited_from ? `DMARC from ${mail.dmarc_inherited_from}` : undefined}>
          {mail ? (
            <>
              <Field k="MX">{mail.null_mx ? "none (null MX: takes no mail)" : mail.mx.length ? mail.mx.map((m) => <div key={m}>{m}</div>) : "none"}</Field>
              <Field k="SPF">{mail.spf ?? <span style={{ color: "#e8908f" }}>none</span>}</Field>
              <Field k="DMARC">
                {mail.dmarc ? (
                  <>
                    <Tag cls={mail.dmarc_policy === "reject" || mail.dmarc_policy === "quarantine" ? "tag-ok" : "tag-warn"}>
                      p={mail.dmarc_policy ?? "?"}
                    </Tag>{" "}
                    {mail.dmarc_pct !== null && mail.dmarc_pct < 100 ? `pct=${mail.dmarc_pct} ` : ""}
                    <div style={{ color: "#9397ab", marginTop: 3 }}>{mail.dmarc}</div>
                  </>
                ) : <span style={{ color: "#e8908f" }}>none</span>}
              </Field>
              <Field k="MTA-STS">{mail.mta_sts ? "published" : "none"}</Field>
              {mail.notes.length > 0 && (
                <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 4 }}>
                  {mail.notes.map((n) => <div key={n} style={{ fontSize: 11.5, color: "#e3c07b" }}>• {n}</div>)}
                </div>
              )}
            </>
          ) : (
            <Empty text={d.exists === false ? "the name does not exist" : "unavailable"} />
          )}
        </Section>

        <Section title="Hosting" note="from our own IP databases">
          {d.addresses.length ? d.addresses.map((a) => (
            <Field key={a.ip} k={`IPv${a.version}`}>
              <button type="button" onClick={() => onIp(a.ip)} title="Open in the IP lookup"
                style={{ background: "none", border: 0, padding: 0, color: "var(--color-accent)", fontFamily: "var(--mono)", fontSize: 12, cursor: "pointer" }}>
                {a.ip}
              </button>
              {a.scope !== "public"
                ? <span style={{ color: "#e3c07b" }}> · {a.scope.replace("_", "-")}</span>
                : <span style={{ color: "#9397ab" }}>
                    {a.country_code ? ` · ${flag(a.country_code)} ${a.city ? a.city + ", " : ""}${a.country ?? a.country_code}` : ""}
                    {a.as_org ? ` · ${a.as_org}` : ""}{a.asn ? ` (AS${a.asn})` : ""}
                  </span>}
            </Field>
          )) : <Empty text="no A or AAAA records" />}
        </Section>

        <Section title="Archive" note="Wayback Machine">
          {d.archive ? (
            <>
              <Field k="First capture">{d.archive.first ? <Link href={d.archive.first_url!}>{day(d.archive.first)}</Link> : null}</Field>
              {!d.archive.first && <Empty text="never captured" />}
              <div style={{ marginTop: 6, fontSize: 11.5 }}>
                <Link href={`https://web.archive.org/web/*/${d.domain}`}>all captures →</Link>
              </div>
            </>
          ) : (
            <>
              <Empty text={d.errors.archive ? "unavailable right now" : "not checked"} />
              <div style={{ marginTop: 6, fontSize: 11.5 }}>
                <Link href={`https://web.archive.org/web/*/${d.domain}`}>look on archive.org →</Link>
              </div>
            </>
          )}
        </Section>

        <Section
          title="Subdomains"
          wide
          note={certs ? `${certs.total_names} name(s) in ${certs.certificates} certificate(s) via ${certs.source}${certs.partial ? " · first page only" : ""}` : undefined}
        >
          {certs ? (
            certs.subdomains.length ? (
              <>
                <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 4 }}>
                  {subs.map((n) => (
                    <button key={n} type="button" className="tag tag-neutral" onClick={() => onDomain(n)}
                      title="Look this name up" style={{ cursor: "pointer", border: 0, fontFamily: "var(--mono)" }}>{n}</button>
                  ))}
                </div>
                {certs.subdomains.length > SUBDOMAINS_SHOWN && (
                  <button type="button" className="btn" onClick={() => setAllSubs((v) => !v)} style={{ marginTop: 8, height: 26, padding: "0 10px", fontSize: 11 }}>
                    {allSubs ? "show fewer" : `show all ${certs.subdomains.length}`}
                  </button>
                )}
                {certs.total_names > certs.subdomains.length && (
                  <div style={{ color: "var(--color-neutral-500)", fontSize: 11, marginTop: 6 }}>
                    showing the first {certs.subdomains.length} of {certs.total_names}
                  </div>
                )}
              </>
            ) : <Empty text="no other names in the certificate logs" />
          ) : <Empty text="unavailable" />}
        </Section>

        <Section title="DNS records" wide note={d.dnssec_validated ? "DNSSEC-validated" : undefined}>
          {d.dns.length ? (
            <div style={{ overflowX: "auto" }}>
              <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "var(--mono)", fontSize: 11.5 }}>
                <tbody>
                  {d.dns.map((r, i) => (
                    <tr key={i} style={{ borderTop: "1px solid color-mix(in srgb, var(--color-divider) 55%, transparent)" }}>
                      <td style={{ padding: "4px 10px 4px 0", color: "var(--color-accent)", whiteSpace: "nowrap", verticalAlign: "top" }}>{r.type}</td>
                      <td style={{ padding: "4px 10px 4px 0", color: r.name === d.domain ? "#9397ab" : "#b2b6ca", whiteSpace: "nowrap", verticalAlign: "top" }}>{r.name}</td>
                      <td style={{ padding: "4px 0", color: "#e4e7f5", wordBreak: "break-all" }}>{r.value}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <Empty text={d.exists === false ? "the name does not exist (NXDOMAIN)" : d.errors.dns ? "unavailable" : "no records"} />}
        </Section>
      </div>

      {Object.keys(d.errors).length > 0 && (
        <div style={{ marginTop: 10, display: "flex", gap: 8, flexWrap: "wrap" }}>
          {Object.entries(d.errors).map(([k, v]) => (
            <span key={k} className="tag tag-bad" style={{ fontSize: 10.5 }}>{ERROR_LABEL[k] || k}: {v}</span>
          ))}
        </div>
      )}
    </div>
  );
}

export function DomainApp({
  initialQuery, onConsumed, dispatch,
}: {
  initialQuery?: string;
  onConsumed: () => void;
  dispatch: Dispatch;
}) {
  const [query, setQuery] = React.useState(initialQuery ?? "");
  const [loading, setLoading] = React.useState(false);
  const [data, setData] = React.useState<DomainLookupResponse | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  // A 402: the search allowance is spent. Not an error — the next step is a plan.
  const [paywall, setPaywall] = React.useState<string | null>(null);

  const doLookup = React.useCallback(async (q: string) => {
    if (!q.trim()) return;
    setLoading(true); setError(null); setPaywall(null);
    try {
      setData(await api.lookupDomain(q.trim()));
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

  function lookupName(name: string) {
    setQuery(name);
    doLookup(name);
  }

  return (
    <div style={{ height: "100%", overflow: "auto", padding: "20px 24px" }}>
      <div style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "#75798c", marginBottom: 16 }}>
        ~/domain$ lookup {query ? query : "--help"}
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
            placeholder="domain, URL or email address…"
            autoFocus
            spellCheck={false}
            style={{ flex: 1, background: "none", border: 0, outline: "none", color: "var(--color-text)", fontFamily: "var(--mono)", fontSize: 13 }}
          />
        </div>
        <button type="submit" className="btn btn-primary" style={{ height: 40, padding: "0 18px" }} disabled={loading}>
          {loading ? "Looking up…" : "Run"}
        </button>
      </form>

      {loading && (
        <div style={{ fontFamily: "var(--mono)", fontSize: 11.5, color: "#75798c", marginBottom: 12 }}>
          asking DNS, the registry, the certificate logs, the archive and the site itself — a few seconds, up to ~20 for big domains
        </div>
      )}
      {error && <div className="tag tag-bad" style={{ marginBottom: 12 }}>{error}</div>}
      {paywall && <UpgradePrompt message={paywall} />}

      {data && (
        <>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 14 }}>
            {data.sources.map((s) => (
              <span key={s.key} className={`tag ${s.ok ? "tag-ok" : "tag-bad"}`} title={s.status}>{s.label}</span>
            ))}
          </div>

          <Result d={data} onDomain={lookupName} onIp={(ip) => dispatch.open("ip", ip)} />

          <div style={{ fontFamily: "var(--mono)", fontSize: 10.5, color: "var(--color-neutral-600)", lineHeight: 1.7 }}>
            {data.attribution.map((a) => <div key={a}><Attribution text={a} /></div>)}
          </div>
        </>
      )}

      {!data && !loading && !error && !paywall && (
        <div className="card" style={{ maxWidth: 600, marginTop: 8 }}>
          <div className="card-kicker">Domain lookup</div>
          <div className="card-title">Everything a domain says about itself</div>
          <p className="card-body">
            Enter a domain, a URL or an email address. You get its DNS records; how its
            mail is protected against spoofing (SPF, DMARC, MTA-STS); who it is registered
            with and when it expires; every subdomain that has appeared in a public TLS
            certificate; what its website answers with, including redirects, security
            headers and the certificate; where it is hosted; and when the Wayback
            Machine first saw it. One lookup is one search.
          </p>
        </div>
      )}
    </div>
  );
}
