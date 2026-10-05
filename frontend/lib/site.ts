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

/**
 * Who the Data Access Purchase Agreement (/terms) is made with, and where
 * disputes go. Set these in the frontend's environment; unset, the page falls
 * back to wording that names no entity or court rather than inventing one.
 */
export const LEGAL_ENTITY = process.env.NEXT_PUBLIC_LEGAL_ENTITY || "";
export const LEGAL_JURISDICTION = process.env.NEXT_PUBLIC_LEGAL_JURISDICTION || "";
export const LEGAL_COURTS = process.env.NEXT_PUBLIC_LEGAL_COURTS || "";

/** Revision of /terms. Mirrors TERMS_VERSION in backend/app/routers/billing.py,
 *  which logs it with every checkout; bump both when the wording changes. */
export const TERMS_VERSION = "2026-10-05";

export const NAV_LINKS = [
  { href: "/tools", label: "Tools" },
  { href: "/pricing", label: "Pricing" },
];
