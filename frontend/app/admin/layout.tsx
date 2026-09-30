import type { Metadata } from "next";

// Admin is operator-only and must never be indexed. It is also gated in
// middleware.ts (cookie at the edge) and by require_admin on every /admin/* API
// call — this metadata only handles the crawler side.
export const metadata: Metadata = {
  title: "DECINT · Admin",
  robots: { index: false, follow: false },
};

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return <>{children}</>;
}
