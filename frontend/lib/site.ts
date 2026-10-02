/**
 * Site-wide constants for the public pages.
 *
 * CONTACT_EMAIL is a ROLE address on your own domain, not a personal inbox —
 * this page is public, so anything here gets scraped. Change it in one place
 * (or set NEXT_PUBLIC_CONTACT_EMAIL) if you'd rather route it elsewhere.
 */
export const CONTACT_EMAIL =
  process.env.NEXT_PUBLIC_CONTACT_EMAIL || "admin@decint.tools";

export const NAV_LINKS = [
  { href: "/tools", label: "Tools" },
  { href: "/pricing", label: "Pricing" },
];
