import Link from "next/link";
import { SiteNav } from "@/components/site/SiteNav";
import { SiteFooter } from "@/components/site/SiteFooter";
import { TOOLS, STANDALONE_TOTAL_CENTS, type ToolSpec } from "@/lib/tools";
import { CONTACT_EMAIL } from "@/lib/site";
import { FALLBACK_PLANS } from "@/lib/pricing";
import { Database, Onion, Chat, ArrowRight } from "@/components/icons";

export const metadata = {
  title: "Tools — DECINT",
  description:
    "The full DECINT toolkit: leak database search, dark-web search and Discord OSINT — what each one costs, takes, and gives back.",
};

const GLYPH: Record<string, (p: { size?: number; style?: React.CSSProperties }) => JSX.Element> = {
  leaks: Database,
  darkweb: Onion,
  discord: Chat,
};

const usd = (cents: number) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: cents % 100 === 0 ? 0 : 2,
  }).format(cents / 100);

// The bundle argument, computed rather than written down, so it cannot go
// stale when a tool price changes in lib/tools.ts or the entry plan changes
// in lib/pricing.ts.
const STARTER_CENTS = FALLBACK_PLANS.find((p) => p.key === "starter")!.monthly_cents;
const SAVING = STANDALONE_TOTAL_CENTS - STARTER_CENTS;

function Column({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <h4
        style={{
          fontFamily: "var(--mono)",
          fontSize: 10.5,
          letterSpacing: "0.14em",
          textTransform: "uppercase",
          color: "var(--color-neutral-500)",
          margin: "0 0 10px",
          fontWeight: 500,
        }}
      >
        {title}
      </h4>
      <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
        {items.map((it) => (
          <li key={it} style={{ display: "flex", gap: 9, fontSize: 13.5, color: "var(--color-neutral-300)", lineHeight: 1.5 }}>
            <span style={{ color: "var(--color-accent)", lineHeight: 1.4, flex: "none" }}>·</span>
            <span>{it}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function PriceTag({ tool }: { tool: ToolSpec }) {
  return (
    <div
      style={{
        marginLeft: "auto",
        textAlign: "right",
        flex: "none",
        display: "flex",
        flexDirection: "column",
        alignItems: "flex-end",
        gap: 3,
      }}
    >
      <div style={{ display: "flex", alignItems: "baseline", gap: 3 }}>
        <span
          style={{
            fontSize: 26,
            fontWeight: 600,
            letterSpacing: "-0.03em",
            color: "var(--color-text)",
            fontVariantNumeric: "tabular-nums",
          }}
        >
          {usd(tool.price_cents)}
        </span>
        <span style={{ fontSize: 12.5, color: "var(--color-neutral-500)" }}>/mo</span>
      </div>
      <span className="tag tag-accent" style={{ fontSize: 9.5 }}>
        In {tool.included_in}
      </span>
    </div>
  );
}

export default function ToolsPage() {
  return (
    <main style={{ minHeight: "100vh" }}>
      <SiteNav current="/tools" />

      <section style={{ maxWidth: 940, margin: "0 auto", padding: "76px 24px 8px" }}>
        <p className="eyebrow">The toolkit</p>
        <h1 style={{ fontSize: "clamp(32px, 5.5vw, 46px)", margin: "0 0 14px" }}>
          Every tool, and what it&apos;s worth.
        </h1>
        <p style={{ fontSize: 16.5, color: "var(--color-neutral-400)", maxWidth: "60ch", margin: 0, lineHeight: 1.6 }}>
          Everything DECINT does, stated in full — what each tool takes, what it
          gives back, and where it stops. The price beside each one is what it is
          worth on its own; they are sold together, which is the point.
        </p>
      </section>

      {/* ── the bundle argument, stated once, in numbers ── */}
      <section style={{ maxWidth: 940, margin: "0 auto", padding: "32px 24px 0" }}>
        <div
          className="card"
          style={{
            padding: "20px 24px",
            gap: 18,
            flexDirection: "row",
            alignItems: "center",
            flexWrap: "wrap",
            borderColor: "var(--color-accent)",
            background:
              "linear-gradient(180deg, color-mix(in srgb, var(--color-accent) 8%, var(--color-surface)), var(--color-surface))",
          }}
        >
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
            <span style={{ fontSize: 14, color: "var(--color-neutral-400)" }}>All three separately</span>
            <span
              style={{
                fontSize: 20,
                fontWeight: 600,
                color: "var(--color-neutral-500)",
                textDecoration: "line-through",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {usd(STANDALONE_TOTAL_CENTS)}
            </span>
            <ArrowRight size={16} style={{ color: "var(--color-accent)" }} />
            <span style={{ fontSize: 14, color: "var(--color-neutral-400)" }}>Starter</span>
            <span
              style={{
                fontSize: 26,
                fontWeight: 600,
                letterSpacing: "-0.03em",
                color: "var(--color-text)",
                fontVariantNumeric: "tabular-nums",
              }}
            >
              {usd(STARTER_CENTS)}
            </span>
            <span style={{ fontSize: 13.5, color: "var(--color-neutral-500)" }}>/month</span>
            <span className="tag tag-accent" style={{ fontSize: 10 }}>
              Save {usd(SAVING)}
            </span>
          </div>
          <Link href="/pricing" className="btn btn-primary" style={{ marginLeft: "auto" }}>
            See the plans
          </Link>
        </div>
      </section>

      <section style={{ maxWidth: 940, margin: "0 auto", padding: "28px 24px 8px" }}>
        {TOOLS.map((t) => {
          const Glyph = GLYPH[t.key];
          return (
            <article
              key={t.key}
              id={t.key}
              className="card"
              style={{ padding: "28px 26px", gap: 0, marginBottom: 18, scrollMarginTop: 84 }}
            >
              <div style={{ display: "flex", alignItems: "flex-start", gap: 14, flexWrap: "wrap" }}>
                {Glyph && (
                  <span
                    style={{
                      display: "grid",
                      placeItems: "center",
                      width: 44,
                      height: 44,
                      flex: "none",
                      borderRadius: 11,
                      border: "1px solid var(--color-divider)",
                      background: "var(--color-surface-2)",
                      color: "var(--color-accent)",
                    }}
                  >
                    <Glyph size={24} />
                  </span>
                )}
                <div style={{ display: "flex", flexDirection: "column", gap: 5, minWidth: 0 }}>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 11, flexWrap: "wrap" }}>
                    <span
                      style={{
                        fontFamily: "var(--mono)",
                        fontSize: 12,
                        color: "var(--color-accent)",
                        fontVariantNumeric: "tabular-nums",
                      }}
                    >
                      {t.no}
                    </span>
                    <h2 style={{ fontSize: 22, margin: 0 }}>{t.name}</h2>
                  </div>
                  <span style={{ fontFamily: "var(--mono)", fontSize: 11, color: "var(--color-neutral-600)" }}>
                    {t.input}
                  </span>
                </div>
                <PriceTag tool={t} />
              </div>

              <p style={{ fontSize: 14.5, color: "var(--color-neutral-300)", margin: "16px 0 0", maxWidth: "68ch", lineHeight: 1.65 }}>
                {t.how}
              </p>

              {t.deeper && (
                <p
                  style={{
                    fontSize: 13,
                    color: "var(--color-neutral-400)",
                    margin: "14px 0 0",
                    padding: "10px 14px",
                    borderRadius: 9,
                    border: "1px solid var(--color-divider)",
                    background: "var(--color-surface-2)",
                    maxWidth: "68ch",
                    lineHeight: 1.55,
                  }}
                >
                  <strong style={{ color: "var(--color-accent)", fontWeight: 600 }}>
                    Goes further on {t.deeper.plan} —{" "}
                  </strong>
                  {t.deeper.what}.
                </p>
              )}

              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
                  gap: 24,
                  marginTop: 22,
                  paddingTop: 20,
                  borderTop: "1px solid var(--color-divider)",
                }}
              >
                <Column title="Accepts" items={t.accepts} />
                <Column title="Returns" items={t.returns} />
                {t.sources && <Column title="Sources" items={t.sources} />}
              </div>

              <p
                style={{
                  fontSize: 13,
                  color: "var(--color-neutral-500)",
                  margin: "20px 0 0",
                  paddingTop: 16,
                  borderTop: "1px solid var(--color-divider)",
                  maxWidth: "72ch",
                  lineHeight: 1.6,
                }}
              >
                <strong style={{ color: "var(--color-neutral-400)", fontWeight: 600 }}>Limits — </strong>
                {t.limits}
              </p>
            </article>
          );
        })}
      </section>

      {/* custom work */}
      <section style={{ maxWidth: 940, margin: "0 auto", padding: "28px 24px 76px" }}>
        <div className="card" style={{ padding: "28px 26px", gap: 8 }}>
          <div className="card-kicker">Something missing?</div>
          <h3 style={{ fontSize: 20, margin: "2px 0 0" }}>Custom tools can be added to your account.</h3>
          <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: "6px 0 0", maxWidth: "62ch", lineHeight: 1.6 }}>
            If your work needs a source, a lookup, or a workflow that isn&apos;t here,
            email the operator and describe it. Bespoke tools are built and
            enabled on your account directly, and come as standard on Enterprise.
          </p>
          <p style={{ fontSize: 13.5, color: "var(--color-neutral-500)", margin: "10px 0 0", maxWidth: "62ch" }}>
            Pricing for custom scripts and tools varies with scope, and is quoted
            per account before any work starts.
          </p>
          <div style={{ marginTop: 16 }}>
            <a href={`mailto:${CONTACT_EMAIL}?subject=DECINT%20custom%20tool%20request`} className="btn btn-primary">
              {CONTACT_EMAIL}
            </a>
          </div>
        </div>
        <p style={{ fontSize: 13, color: "var(--color-neutral-600)", marginTop: 20 }}>
          See <Link href="/pricing">pricing</Link> for plans and payment options.
        </p>
      </section>

      <SiteFooter />
    </main>
  );
}
