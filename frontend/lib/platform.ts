/**
 * The DECINT Android app (android/) shows this site in a WebView and appends
 * this token to the WebView's user agent.
 *
 * Inside the app, Google Play's payments policy applies: nothing may sell a
 * plan or point at a way to pay outside Play. So the purchase UI is marked
 * `web-only` and hidden there by CSS (globals.css), and `app-only` marks the
 * neutral line that replaces it. CSS rather than a React check, so the marketing
 * pages — static server components — are covered too, and nothing flashes:
 * the app sets `html.in-app` before the page's own scripts run, and
 * <InAppMarker/> sets it again after hydration in case that ever misses.
 */
export const APP_UA_TOKEN = "DecintAndroid/";

export function isAndroidApp(): boolean {
  return typeof navigator !== "undefined" && navigator.userAgent.includes(APP_UA_TOKEN);
}
