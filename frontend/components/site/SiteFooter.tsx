import Link from "next/link";
import { Wordmark } from "@/components/site/Wordmark";
import { NAV_LINKS, CONTACT_EMAIL } from "@/lib/site";

export function SiteFooter() {
  return (
    <footer style={{ borderTop: "1px solid var(--color-divider)", marginTop: 8 }}>
      <div
        style={{
          maxWidth: 1080,
          margin: "0 auto",
          padding: "34px 24px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 20,
          flexWrap: "wrap",
        }}
      >
        <Wordmark size="sm" muted />

        <nav style={{ display: "flex", gap: 20, flexWrap: "wrap" }}>
          {NAV_LINKS.map((l) => (
            <Link key={l.href} href={l.href} style={{ fontSize: 13, color: "var(--color-neutral-500)" }}>
              {l.label}
            </Link>
          ))}
          <a href={`mailto:${CONTACT_EMAIL}`} style={{ fontSize: 13, color: "var(--color-neutral-500)" }}>
            Contact
          </a>
        </nav>

        {/* Rendered on the server at build time. Deliberately not `new Date()`
            in a client component — that would mismatch during hydration. */}
        <span style={{ fontSize: 12, color: "var(--color-neutral-600)", fontFamily: "var(--mono)" }}>
          © {new Date().getFullYear()} · Self-hosted OSINT &amp; network intelligence
        </span>
      </div>
    </footer>
  );
}
