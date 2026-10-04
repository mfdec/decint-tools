/**
 * Site-wide constants for the public pages.
 *
 * CONTACT_EMAIL is a ROLE address on your own domain, not a personal inbox —
 * this page is public, so anything here gets scraped. Change it in one place
 * (or set NEXT_PUBLIC_CONTACT_EMAIL) if you'd rather route it elsewhere.
 */
export const CONTACT_EMAIL =
  process.env.NEXT_PUBLIC_CONTACT_EMAIL || "admin@decint.tools";

/** The Android app's package name (android/app/build.gradle.kts applicationId). */
export const ANDROID_PACKAGE =
  process.env.NEXT_PUBLIC_ANDROID_PACKAGE || "tools.decint.app";

export const NAV_LINKS = [
  { href: "/tools", label: "Tools" },
  { href: "/pricing", label: "Pricing" },
];
