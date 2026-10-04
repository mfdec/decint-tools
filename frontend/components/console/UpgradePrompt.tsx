"use client";

import * as React from "react";
import Link from "next/link";
import { ApiError } from "@/lib/api";

/**
 * What a search app shows instead of an error when the API answers 402.
 *
 * The free tier is three searches, ever — a trial whose purpose is to lead
 * here. So this is not an error state: the tools worked, the allowance is
 * spent, and the next step is a plan. A paid tier that has used its month's
 * allowance gets the same card with the reset date instead of a hard sell.
 */
export function isQuotaError(e: unknown): e is ApiError {
  return e instanceof ApiError && e.status === 402;
}

export function UpgradePrompt({ message }: { message: string }) {
  return (
    <div
      className="card"
      style={{
        maxWidth: 520,
        marginTop: 8,
        borderColor: "var(--color-accent)",
        background:
          "linear-gradient(180deg, color-mix(in srgb, var(--color-accent) 10%, var(--color-surface)), var(--color-surface))",
      }}
    >
      <div className="card-kicker">Allowance used</div>
      <div className="card-title">{message}</div>
      <p className="card-body app-only">Plans can&apos;t be bought in the Android app.</p>
      <p className="card-body web-only">
        Plans start at $4.95/month with 500 searches, full leak, dark-web and
        Discord coverage, and cancel any time. Card payments activate instantly.
      </p>
      <div className="web-only" style={{ display: "flex", gap: 10, marginTop: 6, flexWrap: "wrap" }}>
        <Link href="/pricing" className="btn btn-solid">See plans</Link>
        <Link href="/billing" className="btn btn-ghost">Billing</Link>
      </div>
    </div>
  );
}
