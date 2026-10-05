export type AppKey =
  | "recon" | "leaks" | "spider" | "darkweb" | "passwords" | "ip" | "phone" | "visitors";

export interface AppDef {
  key: AppKey;
  tty: string;
  name: string;
  desc: string;
  hot: string; // display hint, e.g. "⌃2"
  /** Requires role=admin — a pure authorisation gate. */
  adminOnly?: boolean;
}

export const APPS: AppDef[] = [
  { key: "recon", tty: "tty1", name: "console", desc: "the shell", hot: "⌃1" },
  { key: "leaks", tty: "tty2", name: "leaks", desc: "leak database", hot: "⌃2" },
  { key: "spider", tty: "tty3", name: "spider", desc: "link findings", hot: "⌃3" },
  { key: "darkweb", tty: "tty4", name: "darkweb", desc: "onion search", hot: "⌃4" },
  { key: "passwords", tty: "tty5", name: "passwords", desc: "password checker", hot: "⌃5" },
  { key: "ip", tty: "tty6", name: "ip", desc: "ip intelligence", hot: "⌃6" },
  { key: "phone", tty: "tty7", name: "phone", desc: "phone intelligence", hot: "⌃7" },
  { key: "visitors", tty: "tty8", name: "visitors", desc: "site analytics", hot: "⌃8", adminOnly: true },
  // Account management (create/edit/suspend/delete accounts + audit) lives ONLY
  // on admin.decint.tools now, not in the operator console. Do not re-add a
  // "users" app here.
];

/** Apps a given role may see. */
export function appsForRole(role: string | undefined): AppDef[] {
  return APPS.filter((a) => !(a.adminOnly && role !== "admin"));
}

export function appByKey(key: string): AppDef | undefined {
  return APPS.find((a) => a.key === key);
}
