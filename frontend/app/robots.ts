import type { MetadataRoute } from "next";

const BASE = (process.env.PUBLIC_BASE_URL || "https://decint.tools").replace(/\/$/, "");

export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        // Keep gated / per-user / transient areas out of the index.
        disallow: [
          "/login",
          "/signup",
          "/forgot",
          "/reset",
          "/activate",
          "/console",
          "/admin",
          "/billing",
          "/debug",
          "/api/",
        ],
      },
    ],
    sitemap: `${BASE}/sitemap.xml`,
    host: BASE,
  };
}
