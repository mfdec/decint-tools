/**
 * Google Play Billing, from inside the Android app.
 *
 * The app (android/…/PlayBilling.kt) answers requests sent over the page bridge
 * `DecintNative`, which it only exposes to this site's own origin. Each request
 * carries an id and the reply echoes it. Outside the app there is no bridge,
 * and everything here resolves to "unavailable".
 *
 * Nothing is granted on the app's say-so: a completed purchase's token goes to
 * POST /billing/play/verify, and the server checks it with Google.
 */
import { isAndroidApp } from "./platform";
import type { BillingPeriod } from "./types";

interface Bridge {
  postMessage(message: string): void;
  addEventListener(type: "message", listener: (event: { data: string }) => void): void;
}

/** One base plan of a Play subscription, priced by Google for this user. */
export interface PlayOffer {
  productId: string;
  basePlanId: BillingPeriod;
  /** Localised, tax-inclusive where required, e.g. "€5.49". */
  price: string;
  /** ISO 8601 billing period, e.g. "P1M". */
  period: string;
}

export type PlayStatus = "purchased" | "pending" | "cancelled" | "owned" | "error";

export interface PlayResult {
  status: PlayStatus;
  purchaseToken?: string;
  error?: string;
}

export interface OwnedPurchase {
  purchaseToken: string;
  productId: string;
  acknowledged: boolean;
  accountRef: string;
}

function bridge(): Bridge | null {
  if (!isAndroidApp()) return null;
  const b = (window as unknown as { DecintNative?: Bridge }).DecintNative;
  return b && typeof b.postMessage === "function" ? b : null;
}

export function playAvailable(): boolean {
  return bridge() !== null;
}

let seq = 0;
let listening = false;
const waiting = new Map<string, (reply: Record<string, unknown>) => void>();

function call<T>(type: string, body: Record<string, unknown> = {}): Promise<T> {
  const b = bridge();
  if (!b) return Promise.reject(new Error("Google Play billing is only available in the Android app."));
  if (!listening) {
    listening = true;
    b.addEventListener("message", (event) => {
      let reply: Record<string, unknown>;
      try {
        reply = JSON.parse(event.data);
      } catch {
        return;
      }
      const done = waiting.get(String(reply.id));
      if (done) {
        waiting.delete(String(reply.id));
        done(reply);
      }
    });
  }
  const id = `play-${Date.now()}-${++seq}`;
  return new Promise<T>((resolve) => {
    waiting.set(id, (reply) => resolve(reply as T));
    b.postMessage(JSON.stringify({ ...body, type, id }));
  });
}

export async function playProducts(productIds: string[]): Promise<PlayOffer[]> {
  const r = await call<{ ok: boolean; items?: PlayOffer[]; error?: string }>("play:products", { products: productIds });
  if (!r.ok) throw new Error(r.error || "Google Play prices are unavailable.");
  return r.items ?? [];
}

export function playSubscribe(opts: {
  productId: string;
  basePlanId: BillingPeriod;
  accountRef: string;
  /** The current Play subscription's token, when this is a plan change. */
  replaceToken?: string;
}): Promise<PlayResult> {
  return call<PlayResult>("play:subscribe", opts);
}

export async function playOwned(): Promise<OwnedPurchase[]> {
  const r = await call<{ ok: boolean; items?: OwnedPurchase[] }>("play:owned");
  return r.ok ? r.items ?? [] : [];
}

/** Play's own subscription screen: cancel, update payment, see renewal date. */
export function playManageUrl(packageName: string, productId?: string): string {
  const q = new URLSearchParams({ package: packageName });
  if (productId) q.set("sku", productId);
  return `https://play.google.com/store/account/subscriptions?${q.toString()}`;
}
