import Link from "next/link";
import { SiteNav } from "@/components/site/SiteNav";
import { SiteFooter } from "@/components/site/SiteFooter";
import { PricingTable } from "@/components/site/PricingTable";
import { CONTACT_EMAIL } from "@/lib/site";

export const metadata = {
  title: "Pricing — DECINT",
  description: "Access tiers for the DECINT console, and custom tooling built to order.",
};

export default function PricingPage() {
  return (
    <main style={{ minHeight: "100vh" }}>
      <SiteNav current="/pricing" />

      <section style={{ maxWidth: 1080, margin: "0 auto", padding: "76px 24px 8px" }}>
        <p className="eyebrow">Pricing</p>
        <h1 style={{ fontSize: "clamp(32px, 5.5vw, 46px)", margin: "0 0 14px" }}>Access to the console.</h1>
        <p style={{ fontSize: 16.5, color: "var(--color-neutral-400)", maxWidth: "56ch", margin: 0, lineHeight: 1.6 }}>
          Every tier gets the whole toolkit — tiers differ by query volume and
          depth, not by locking tools away. See <Link href="/tools">what&apos;s included</Link>.
        </p>
        <p style={{ fontSize: 14, color: "var(--color-neutral-500)", maxWidth: "56ch", margin: "12px 0 0", lineHeight: 1.6 }}>
          Pay by card, or in Bitcoin and ~300 other assets. Crypto is billed as a
          prepaid block of access rather than a subscription — no chain can pull
          a renewal, so nothing recurs without you.
        </p>
      </section>

      <section style={{ maxWidth: 1080, margin: "0 auto", padding: "44px 24px 8px" }}>
        <PricingTable />
      </section>

      {/* custom tooling */}
      <section style={{ maxWidth: 1080, margin: "0 auto", padding: "44px 24px 76px" }}>
        <div className="card" style={{ padding: "28px 26px", gap: 8 }}>
          <div className="card-kicker">Custom work</div>
          <h3 style={{ fontSize: 20, margin: "2px 0 0" }}>Need a tool that isn&apos;t here?</h3>
          <p style={{ fontSize: 14, color: "var(--color-neutral-400)", margin: "6px 0 0", maxWidth: "64ch", lineHeight: 1.6 }}>
            Email the operator describing the source, lookup, or workflow you need.
            Custom tools are built and enabled directly on your account — on any tier.
          </p>
          <p style={{ fontSize: 13.5, color: "var(--color-neutral-500)", margin: "10px 0 0", maxWidth: "64ch" }}>
            Pricing for custom scripts and tools varies with scope, and is quoted
            per account before any work starts.
          </p>
          <div style={{ marginTop: 16 }}>
            <a href={`mailto:${CONTACT_EMAIL}?subject=DECINT%20custom%20tool%20request`} className="btn btn-primary">
              {CONTACT_EMAIL}
            </a>
          </div>
        </div>
      </section>

      <SiteFooter />
    </main>
  );
}
