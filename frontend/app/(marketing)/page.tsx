import Link from "next/link";
import { SiteNav } from "@/components/site/SiteNav";
import { SiteFooter } from "@/components/site/SiteFooter";
import { Shield, ArrowRight } from "@/components/icons";
import { TOOLS } from "@/lib/tools";

// Deliberately not a metrics band. Counting free sources or absent API keys
// undersells the product, and inventing record counts would be a lie — so this
// states the properties that actually differentiate it, all of which are true
// of the running system.
const CLAIMS = [
  { k: "Nothing stored", d: "Queries and results are never written to disk." },
  { k: "Tor-native", d: "Onion search over a live circuit, not a cached mirror." },
  { k: "Self-hosted", d: "Runs entirely on infrastructure you control." },
  { k: "One prompt", d: "Every tool behind a single command line." },
];

const STEPS = [
  ["Pick a tool", "One dropdown, or ⌘K. No tabs, no menus to hunt."],
  ["Run a query", "Type it straight into the shell, or fill the field."],
  ["Read the signal", "Ranked, deduped, source-tagged. Nothing stored."],
];

export default function Landing() {
  return (
    <main style={{ minHeight: "100vh" }}>
      <SiteNav />

      {/* ── hero ───────────────────────────────────────────────────────────
          The mark is used once, very large and very quiet, as the ground the
          headline sits on. It is the only decorative element on the page —
          spending the boldness in one place and keeping everything else flat. */}
      <section style={{ position: "relative", overflow: "hidden" }}>
        <div
          aria-hidden
          style={{
            position: "absolute",
            inset: 0,
            background:
              "radial-gradient(760px 380px at 12% -20%, color-mix(in srgb, var(--color-accent) 16%, transparent), transparent 70%)",
            pointerEvents: "none",
          }}
        />
        <Shield
          size={560}
          aria-hidden
          style={{
            position: "absolute",
            right: -130,
            top: -110,
            color: "var(--color-accent)",
            opacity: 0.055,
            pointerEvents: "none",
          }}
        />

        <div style={{ position: "relative", maxWidth: 1080, margin: "0 auto", padding: "104px 24px 72px" }}>
          <p className="eyebrow">OSINT &amp; network intelligence</p>

          <h1 style={{ fontSize: "clamp(42px, 7.5vw, 68px)", lineHeight: 1.0, letterSpacing: "-0.035em", margin: 0 }}>
            <span style={{ display: "block", color: "var(--color-text)" }}>Every signal.</span>
            <span className="grad-text" style={{ display: "block", width: "fit-content" }}>One console.</span>
          </h1>

          <p style={{ fontSize: 18, color: "var(--color-neutral-400)", maxWidth: "46ch", marginTop: 22, lineHeight: 1.6 }}>
            Focused investigation tools behind a single command line. Self-hosted,
            nothing written to disk, no result you can&apos;t trace back to its source.
          </p>

          <div style={{ display: "flex", gap: 12, marginTop: 32, flexWrap: "wrap" }}>
            <Link href="/signup" className="btn btn-solid" style={{ height: 46, padding: "0 24px" }}>
              Create account
            </Link>
            <Link href="/tools" className="btn btn-secondary" style={{ height: 46, padding: "0 22px" }}>
              See the toolkit <ArrowRight size={14} />
            </Link>
          </div>
        </div>
      </section>

      {/* ── claims ─────────────────────────────────────────────────────── */}
      <section style={{ borderTop: "1px solid var(--color-divider)", borderBottom: "1px solid var(--color-divider)" }}>
        <div
          style={{
            maxWidth: 1080,
            margin: "0 auto",
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(215px, 1fr))",
          }}
        >
          {CLAIMS.map((c, i) => (
            <div
              key={c.k}
              style={{
                padding: "28px 24px",
                // Dividers BETWEEN cells only. A border-left on the first cell
                // lands on the container edge and reads as a stray mark.
                borderLeft: i === 0 ? undefined : "1px solid var(--color-divider)",
              }}
            >
              <div style={{ fontSize: 14, fontWeight: 600, color: "var(--color-accent-300)" }}>{c.k}</div>
              <div style={{ fontSize: 13, color: "var(--color-neutral-500)", marginTop: 6, lineHeight: 1.5 }}>{c.d}</div>
            </div>
          ))}
        </div>
      </section>

      {/* ── the toolkit ────────────────────────────────────────────────────
          TOOLS is imported from lib/tools — the same source /tools renders
          from, so the two pages can never disagree about what the product does.
          The 01–03 numbering is the tools' own; it is not decoration. */}
      <section style={{ maxWidth: 880, margin: "0 auto", padding: "88px 24px" }}>
        <p className="eyebrow">The toolkit</p>
        <h2 style={{ fontSize: "clamp(26px, 4vw, 34px)", margin: "0 0 8px" }}>The whole toolkit.</h2>
        <p style={{ fontSize: 15.5, color: "var(--color-neutral-500)", margin: "0 0 36px", maxWidth: "52ch" }}>
          Everything DECINT does, in full. Every plan sees every tool — the tiers
          differ by depth and volume, not by locking tools away.
        </p>

        <ol style={{ listStyle: "none", margin: 0, padding: 0, borderTop: "1px solid var(--color-divider)" }}>
          {TOOLS.map((t) => (
            <li
              key={t.no}
              style={{
                display: "grid",
                gridTemplateColumns: "38px 1fr",
                gap: 18,
                padding: "24px 0",
                borderBottom: "1px solid var(--color-divider)",
                alignItems: "start",
              }}
            >
              <span
                style={{
                  fontFamily: "var(--mono)",
                  fontSize: 12,
                  color: "var(--color-accent)",
                  paddingTop: 4,
                  fontVariantNumeric: "tabular-nums",
                }}
              >
                {t.no}
              </span>
              <div>
                <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
                  <h3 style={{ fontSize: 18, margin: 0 }}>{t.name}</h3>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--color-neutral-600)" }}>{t.input}</span>
                  {t.local && <span className="tag tag-outline" style={{ fontSize: 10 }}>local only</span>}
                </div>
                <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: "7px 0 0", maxWidth: "62ch", lineHeight: 1.6 }}>
                  {t.summary}
                </p>
              </div>
            </li>
          ))}
        </ol>

        <div style={{ marginTop: 30 }}>
          <Link href="/tools" className="btn btn-primary">
            Full breakdown of every tool <ArrowRight size={14} />
          </Link>
        </div>
      </section>

      {/* ── how it works ─────────────────────────────────────────────────── */}
      <section style={{ borderTop: "1px solid var(--color-divider)" }}>
        <div style={{ maxWidth: 1080, margin: "0 auto", padding: "64px 24px" }}>
          <p className="eyebrow">How it runs</p>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(230px, 1fr))",
              gap: 28,
              marginTop: 24,
            }}
          >
            {STEPS.map(([h, d], i) => (
              <div key={h} style={{ borderTop: "2px solid var(--color-accent-800)", paddingTop: 14 }}>
                <div style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--color-accent)", letterSpacing: "0.1em" }}>
                  {`0${i + 1}`}
                </div>
                <div style={{ fontSize: 16, fontWeight: 600, margin: "8px 0 4px" }}>{h}</div>
                <div style={{ fontSize: 14, color: "var(--color-neutral-500)", lineHeight: 1.55 }}>{d}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── closing CTA ──────────────────────────────────────────────────── */}
      <section style={{ borderTop: "1px solid var(--color-divider)" }}>
        <div
          style={{
            maxWidth: 1080,
            margin: "0 auto",
            padding: "72px 24px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            gap: 28,
            flexWrap: "wrap",
          }}
        >
          <div>
            <h2 style={{ fontSize: 26, margin: "0 0 6px" }}>Start with one query.</h2>
            <p style={{ fontSize: 15, color: "var(--color-neutral-500)", margin: 0, maxWidth: "48ch" }}>
              Create an account and run a leak search in the console. Nothing you
              type is written to disk.
            </p>
          </div>
          <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
            <Link href="/signup" className="btn btn-solid" style={{ height: 44, padding: "0 22px" }}>Create account</Link>
            <Link href="/pricing" className="btn btn-secondary" style={{ height: 44, padding: "0 20px" }}>Pricing</Link>
          </div>
        </div>
      </section>

      <SiteFooter />
    </main>
  );
}
