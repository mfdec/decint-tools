"use client";

import * as React from "react";
import { api } from "@/lib/api";
import { playAvailable, playOwned } from "@/lib/playBilling";

/**
 * Inside the Android app, once per launch: hand the server any Google Play
 * purchase it hasn't acknowledged yet. A purchase whose report was lost (the
 * app closed mid-purchase, the network dropped) is then applied on the next
 * launch, well inside Google's three-day acknowledgement window. The server's
 * Play notifications cover the same case; this is the faster path.
 */
export function PlayBillingSync() {
  React.useEffect(() => {
    if (!playAvailable()) return;
    (async () => {
      const pending = (await playOwned().catch(() => [])).filter((p) => !p.acknowledged);
      if (!pending.length) return;
      const account = await api.playAccount().catch(() => null);
      if (!account) return;
      for (const p of pending) {
        if (p.accountRef === account.account_ref) await api.playVerify(p.purchaseToken).catch(() => {});
      }
    })();
  }, []);
  return null;
}
