import { SiteNav } from "@/components/site/SiteNav";
import { SiteFooter } from "@/components/site/SiteFooter";

/**
 * Long-form text pages (privacy policy, account deletion): the marketing
 * chrome around one readable column. Server-rendered — nothing here is
 * interactive, and these are the pages app stores and crawlers fetch.
 */
export function DocPage({
  eyebrow, title, updated, children,
}: {
  eyebrow: string;
  title: string;
  /** ISO date the text last changed, shown under the title. */
  updated?: string;
  children: React.ReactNode;
}) {
  return (
    <main style={{ minHeight: "100vh" }}>
      <SiteNav />
      <article style={{ maxWidth: 760, margin: "0 auto", padding: "76px 24px 8px" }}>
        <p className="eyebrow">{eyebrow}</p>
        <h1 style={{ fontSize: "clamp(28px, 5vw, 40px)", margin: "0 0 10px" }}>{title}</h1>
        {updated && (
          <p style={{ fontFamily: "var(--mono)", fontSize: 12, color: "var(--color-neutral-600)", margin: "0 0 8px" }}>
            Last updated {updated}
          </p>
        )}
        <div className="doc">{children}</div>
      </article>
      <div style={{ height: 76 }} />
      <SiteFooter />
    </main>
  );
}

export function DocSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section style={{ marginTop: 36 }}>
      <h2 style={{ fontSize: 18, margin: "0 0 10px" }}>{title}</h2>
      {children}
    </section>
  );
}
