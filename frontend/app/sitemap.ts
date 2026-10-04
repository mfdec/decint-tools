import type { MetadataRoute } from "next";

// Public, indexable pages only. Auth (/login, /signup, /forgot, /reset,
// /activate), /console, /billing/* and /debug are deliberately excluded —
// they are gated, per-user, or transient and must not be in the sitemap.
const BASE = (process.env.PUBLIC_BASE_URL || "https://decint.tools").replace(/\/$/, "");

export default function sitemap(): MetadataRoute.Sitemap {
  const now = new Date();
  return [
    { url: `${BASE}/`, lastModified: now, changeFrequency: "weekly", priority: 1.0 },
    { url: `${BASE}/tools`, lastModified: now, changeFrequency: "weekly", priority: 0.9 },
    { url: `${BASE}/pricing`, lastModified: now, changeFrequency: "monthly", priority: 0.8 },
    { url: `${BASE}/privacy`, lastModified: now, changeFrequency: "yearly", priority: 0.3 },
    { url: `${BASE}/delete-account`, lastModified: now, changeFrequency: "yearly", priority: 0.2 },
  ];
}
