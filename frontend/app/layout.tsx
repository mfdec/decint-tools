import type { Metadata, Viewport } from "next";
import { Suspense } from "react";
import { Inter, JetBrains_Mono } from "next/font/google";
import { AnalyticsBeacon } from "@/components/AnalyticsBeacon";
import { InAppMarker } from "@/components/InAppMarker";
import { PlayBillingSync } from "@/components/PlayBillingSync";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-body",
  display: "swap",
});
const mono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "DECINT — Every signal. One console.",
  description:
    "OSINT and network intelligence for investigators: leak database search, dark-web search, a breached-password checker, IP lookup, phone lookup, and live packet capture.",
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "any" },
      { url: "/favicon-32x32.png", type: "image/png", sizes: "32x32" },
      { url: "/favicon-16x16.png", type: "image/png", sizes: "16x16" },
    ],
    apple: "/apple-touch-icon.png",
  },
};

export const viewport: Viewport = {
  themeColor: "#0b0c13",
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${inter.variable} ${mono.variable}`}>
      <head>
        {/* Google AdSense loader. A plain tag on purpose: next/script injects it in
            the browser after hydration, so it never appears in the server HTML that
            AdSense's site verification and crawler read. Keep it in <head>. */}
        <script
          async
          src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-6305843427676378"
          crossOrigin="anonymous"
        />
      </head>
      <body>
        {children}
        {/* Suspense because the beacon reads useSearchParams — without it the
            whole tree opts out of static prerendering. */}
        <Suspense fallback={null}>
          <AnalyticsBeacon />
        </Suspense>
        <InAppMarker />
        <PlayBillingSync />
      </body>
    </html>
  );
}
