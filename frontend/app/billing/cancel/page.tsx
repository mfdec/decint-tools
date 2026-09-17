import Link from "next/link";
import { SiteNav } from "@/components/site/SiteNav";
import { SiteFooter } from "@/components/site/SiteFooter";
import { CONTACT_EMAIL } from "@/lib/site";

export const metadata = { title: "Checkout cancelled — DECINT" };

/** Where a processor returns someone who backed out. Nothing was charged, and
 *  the order row stays `pending` until it expires — so this page has nothing
 *  to undo and says so plainly rather than implying something went wrong. */
export default function BillingCancelPage() {
  return (
    <main style={{ minHeight: "100vh" }}>
      <SiteNav />
      <section style={{ maxWidth: 640, margin: "0 auto", padding: "96px 24px 8px" }}>
        <p className="eyebrow">Checkout cancelled</p>
        <h1 style={{ fontSize: "clamp(28px, 5vw, 40px)", margin: "0 0 16px" }}>
          Nothing was charged.
        </h1>
        <p style={{ fontSize: 15.5, color: "var(--color-neutral-400)", lineHeight: 1.6 }}>
          You left the checkout before it completed, so no payment was taken and
          your plan is unchanged. Pick up where you left off whenever you like.
        </p>
        <p style={{ fontSize: 14, color: "var(--color-neutral-500)", lineHeight: 1.6, marginTop: 14 }}>
          If something went wrong instead — a card declined, a coin you couldn&apos;t
          send — <a href={`mailto:${CONTACT_EMAIL}?subject=DECINT%20checkout%20problem`}>tell the operator</a> and
          it&apos;ll get sorted.
        </p>
        <div style={{ display: "flex", gap: 12, marginTop: 28, flexWrap: "wrap" }}>
          <Link href="/pricing" className="btn btn-primary">Back to pricing</Link>
          <Link href="/console" className="btn btn-ghost">Open the console</Link>
        </div>
      </section>
      <div style={{ height: 96 }} />
      <SiteFooter />
    </main>
  );
}
