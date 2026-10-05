# Reconstructed: frontend/lib

The Aug 30 state of `frontend/lib/` did not survive anywhere — OneDrive left the
directory empty and no transcript captured those files after Aug 21. The Aug 21
copies restored from the snapshot zip were 10 days behind the components that
import them, so **the recovered tree did not build**: 3 named exports and 12
`api.*` methods were missing.

Rebuilt 2026-09-02 against the backend, which *did* survive at Aug 30 and is the
authoritative contract. Verified: `tsc --noEmit` clean, `next build` green, all
four backend suites pass, site serving.

## Reconstructed faithfully (shape derived from the backend)

| File | Added | Source of truth |
|---|---|---|
| `api.ts` | `passwordForgot`, `passwordResetCheck`, `passwordReset` | `routers/auth.py` |
| `api.ts` | `billingConfig`, `cryptoCurrencies`, `billingMe`, `checkout`, `billingPortal` | `routers/billing.py` |
| `api.ts` | `password_reset_enabled` on `bootstrap` + `signupInfo` | `routers/auth.py` |
| `types.ts` | `BillingPlan/Config/Order/Subscription/Summary`, `OrderStatus`, `CheckoutResponse` | `services/billing/{plans,store}.py` |
| `pricing.ts` | `FALLBACK_PLANS` | mirrors `services/billing/plans.py` exactly |

These match the running API. Request/response shapes were read off the routes,
not guessed.

## ⚠ Invented values — review these

`tools.ts` gained `price_cents`, `included_in` and `deeper`, because
`app/(marketing)/tools/page.tsx` renders them. **The real figures are lost.**
Placeholders chosen so the bundle argument holds:

| Tool | `price_cents` | |
|---|---|---|
| Leak search | 1200 | $12/mo |
| Dark-web search | 1900 | $19/mo |

`STANDALONE_TOTAL_CENTS` sums to $40 against the entry plan (Starter, $4.95
since 2026-09-17; the page reads the figure from `lib/pricing.ts`). **If these were different before, edit `lib/tools.ts`
— nothing else needs to change.** The `deeper` copy on dark-web search is also
newly written.
