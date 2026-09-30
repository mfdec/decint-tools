export type AppKey =
  | "recon" | "leaks" | "darkweb" | "discord" | "packets" | "visitors" | "fleet";

export interface AppDef {
  key: AppKey;
  tty: string;
  name: string;
  desc: string;
  hot: string; // display hint, e.g. "⌃2"
  admin?: boolean; // requires sniffer_enabled + admin
  /** Requires role=admin. Unlike `admin` above (which is really "needs the
   *  sniffer capability"), this is a pure authorisation gate. */
  adminOnly?: boolean;
}

export const APPS: AppDef[] = [
  { key: "recon", tty: "tty1", name: "recon", desc: "the shell", hot: "⌃1" },
  { key: "leaks", tty: "tty2", name: "leaks", desc: "leak database", hot: "⌃2" },
  { key: "darkweb", tty: "tty3", name: "darkweb", desc: "onion search", hot: "⌃3" },
  { key: "discord", tty: "tty4", name: "discord", desc: "id + guild osint", hot: "⌃4" },
  { key: "packets", tty: "tty5", name: "packets", desc: "live capture", hot: "⌃5", admin: true },
  { key: "visitors", tty: "tty6", name: "visitors", desc: "site analytics", hot: "⌃6", adminOnly: true },
  // Account management (create/edit/suspend/delete accounts + audit) lives ONLY
  // on admin.decint.tools now, not in the operator console. Do not re-add a
  // "users" app here.
  { key: "fleet", tty: "tty7", name: "fleet", desc: "server fleet", hot: "⌃7", adminOnly: true },
];

/** Apps a given role may see. */
export function appsForRole(
  role: string | undefined,
  snifferEnabled: boolean | undefined
): AppDef[] {
  return APPS.filter((a) => {
    if (a.adminOnly && role !== "admin") return false;
    if (a.admin && !snifferEnabled) return false;
    return true;
  });
}

export function appByKey(key: string): AppDef | undefined {
  return APPS.find((a) => a.key === key);
}
