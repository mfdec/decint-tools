import type { AppDef } from "./apps";

/**
 * The recon shell's command table: one place that says what each command is
 * called, what it takes, and what those arguments mean.
 *
 * `help` renders this whole table; `help <command>` (or `<command> --help`)
 * renders one entry in full, with its arguments and examples. The shell's
 * parser reads the same specs, so a flag that is documented here is a flag
 * that works, and a usage error quotes the same synopsis the help shows.
 *
 * Positional arguments are written `<name>` when required and `[name]` when
 * optional. Flags are always optional and take one value: `--mode tor` or
 * `--mode=tor`. A flag with `values` rejects anything not in the list.
 */

export interface ArgSpec {
  name: string;
  required: boolean;
  desc: string;
  /** Accepted values, when the argument is an enumeration. */
  values?: readonly string[];
  /** The value used when the flag is left out. */
  default?: string;
}

export interface CommandSpec {
  name: string;
  group: "search" | "navigate" | "system";
  summary: string;
  /** Positional arguments, in order. At most one may swallow the rest of the line. */
  args: ArgSpec[];
  flags: ArgSpec[];
  examples: string[];
}

export const COMMANDS: CommandSpec[] = [
  {
    name: "leaks",
    group: "search",
    summary: "search the leak database for an email, username or domain",
    args: [
      { name: "query", required: true, desc: "an email address, username or domain" },
    ],
    flags: [
      {
        name: "kind", required: false, default: "auto",
        values: ["auto", "email", "username", "domain"],
        desc: "how to read the query — auto detects it from its shape",
      },
    ],
    examples: [
      "leaks alice@example.com",
      "leaks example.com --kind domain",
      "leaks alice --kind username",
    ],
  },
  {
    name: "darkweb",
    group: "search",
    summary: "search onion sites for a keyword or phrase",
    args: [
      {
        name: "keyword", required: true,
        desc: 'one or more words; the rest of the line is the search. Use "quoted phrases" and -excluded words',
      },
    ],
    flags: [
      {
        name: "mode", required: false, default: "gateway",
        values: ["gateway", "tor"],
        desc: "gateway: a handful of engines over the clearnet, seconds, no Tor · tor: ~45 onion engines over Tor, slower (Pro)",
      },
      {
        name: "engines", required: false, default: "default",
        values: ["default", "all"],
        desc: "all: also the unvetted experimental engines (tor mode only)",
      },
      {
        name: "pages", required: false, default: "1",
        values: ["1", "2", "3"],
        desc: "result pages to read from each engine — more results, slower",
      },
    ],
    examples: [
      "darkweb acme corp",
      'darkweb "leaked database" -conti',
      "darkweb ransomware leak --mode tor",
    ],
  },
  {
    name: "passwords",
    group: "search",
    summary: "check whether a password has turned up in a breach",
    args: [
      {
        name: "sha1", required: false,
        desc: "a SHA-1 hash to check. Leave it out to open the checker and type the password there: " +
              "the shell keeps what you type on screen, so it never takes a password",
      },
    ],
    flags: [],
    examples: [
      "passwords",
      "passwords 5baa61e4c9b93f3f0682250b6cf8331b7ee68fd8",
    ],
  },
  {
    name: "scan",
    group: "search",
    summary: "work out what a target is and which tool fits it",
    args: [
      { name: "target", required: true, desc: "an email, domain, username or a SHA-1 hash" },
    ],
    flags: [],
    examples: ["scan alice@example.com", "scan example.com"],
  },
  {
    name: "open",
    group: "navigate",
    summary: "switch to another app",
    args: [
      { name: "app", required: true, desc: "an app name from the list", values: [] /* filled per role */ },
    ],
    flags: [],
    examples: ["open leaks", "open darkweb"],
  },
  {
    name: "tor",
    group: "system",
    summary: "show whether the Tor circuit is up",
    args: [],
    flags: [],
    examples: ["tor"],
  },
  {
    name: "clear",
    group: "system",
    summary: "clear the screen",
    args: [],
    flags: [],
    examples: ["clear"],
  },
  {
    name: "help",
    group: "system",
    summary: "list commands, or explain one in detail",
    args: [
      { name: "command", required: false, desc: "a command name to explain" },
    ],
    flags: [],
    examples: ["help", "help leaks", "darkweb --help"],
  },
];

const GROUPS: { key: CommandSpec["group"]; label: string }[] = [
  { key: "search", label: "search" },
  { key: "navigate", label: "navigate" },
  { key: "system", label: "system" },
];

export function commandByName(name: string): CommandSpec | undefined {
  return COMMANDS.find((c) => c.name === name);
}

/** The `open` command with its app list filled in for the current role. */
function withApps(spec: CommandSpec, apps: AppDef[]): CommandSpec {
  if (spec.name !== "open") return spec;
  return {
    ...spec,
    args: spec.args.map((a) => (a.name === "app" ? { ...a, values: apps.map((x) => x.key) } : a)),
  };
}

function argToken(a: ArgSpec): string {
  const inner = a.values && a.values.length ? a.values.join("|") : a.name;
  return a.required ? `<${inner}>` : `[${inner}]`;
}

function flagToken(f: ArgSpec): string {
  const val = f.values && f.values.length ? f.values.join("|") : "value";
  return `[--${f.name} ${val}]`;
}

/** One-line synopsis: `leaks <query> [--kind auto|email|username|domain]`. */
export function usage(spec: CommandSpec): string {
  return [spec.name, ...spec.args.map(argToken), ...spec.flags.map(flagToken)].join(" ");
}

/** The lines `help` prints: every command, grouped, with its synopsis and summary. */
export function helpIndex(apps: AppDef[]): string[] {
  const out: string[] = [
    "DECINT shell — commands are written:  command <required> [optional] [--flag value]",
    "",
  ];
  for (const g of GROUPS) {
    const cmds = COMMANDS.filter((c) => c.group === g.key).map((c) => withApps(c, apps));
    if (!cmds.length) continue;
    out.push(g.label);
    for (const c of cmds) {
      out.push("  " + usage(c));
      out.push("        " + c.summary);
    }
    out.push("");
  }
  out.push("shortcuts   ⌘K command palette   ·   ⌃1–⌃" + apps.length + " jump to an app");
  out.push("'help <command>' or '<command> --help' explains its arguments, e.g. help leaks");
  return out;
}

/** The lines `help <command>` prints: synopsis, each argument, examples. */
export function helpFor(name: string, apps: AppDef[]): string[] | null {
  const found = commandByName(name);
  if (!found) return null;
  const spec = withApps(found, apps);
  const out: string[] = [`${spec.name} — ${spec.summary}`, `usage: ${usage(spec)}`];

  if (spec.args.length || spec.flags.length) {
    out.push("", "arguments");
    const rows: [string, string, string][] = [];
    for (const a of spec.args) {
      rows.push([`<${a.name}>`, a.required ? "required" : "optional", a.desc]);
      if (a.values && a.values.length) rows.push(["", "", "one of: " + a.values.join(", ")]);
    }
    for (const f of spec.flags) {
      rows.push([`--${f.name}`, "optional", f.desc]);
      if (f.values && f.values.length) {
        rows.push(["", "", "one of: " + f.values.join(", ") + (f.default ? `   (default ${f.default})` : "")]);
      }
    }
    const w = Math.max(...rows.map((r) => r[0].length));
    for (const [tok, req, desc] of rows) out.push(`  ${tok.padEnd(w)}   ${req.padEnd(8)}   ${desc}`);
  }

  if (spec.examples.length) {
    out.push("", "examples");
    for (const e of spec.examples) out.push("  " + e);
  }
  return out;
}

export interface Parsed {
  /** Positional words joined back into one string. */
  query: string;
  flags: Record<string, string>;
  /** Human-readable problems; empty when the line is valid. */
  errors: string[];
}

/**
 * Split `rest` (everything after the command word) into positional words
 * and flags, validated against the spec. Flags may sit anywhere on the line.
 */
export function parseArgs(spec: CommandSpec, rest: string[], apps: AppDef[]): Parsed {
  const full = withApps(spec, apps);
  const flags: Record<string, string> = {};
  const words: string[] = [];
  const errors: string[] = [];

  for (let i = 0; i < rest.length; i++) {
    const tok = rest[i];
    if (!tok.startsWith("--")) { words.push(tok); continue; }
    const eq = tok.indexOf("=");
    const key = eq === -1 ? tok.slice(2) : tok.slice(2, eq);
    let val = eq === -1 ? undefined : tok.slice(eq + 1);
    const f = full.flags.find((x) => x.name === key);
    if (!f) { errors.push(`unknown flag --${key}`); continue; }
    if (val === undefined) {
      const next = rest[i + 1];
      if (next === undefined || next.startsWith("--")) { errors.push(`--${key} needs a value`); continue; }
      val = next; i++;
    }
    if (f.values && f.values.length && !f.values.includes(val)) {
      errors.push(`--${key} must be one of ${f.values.join(", ")} (got '${val}')`);
      continue;
    }
    flags[key] = val;
  }

  const query = words.join(" ");
  const first = full.args[0];
  if (!first && query) errors.push(`${full.name} takes no arguments (got '${query}')`);
  if (first?.required && !query) errors.push(`missing <${first.name}>`);
  if (first?.values && first.values.length && query && !first.values.includes(query)) {
    errors.push(`<${first.name}> must be one of ${first.values.join(", ")} (got '${query}')`);
  }
  return { query, flags, errors };
}

/** Which tool a bare target belongs to, by its shape. */
export function classifyTarget(t: string): { tool: "leaks" | "passwords"; kind: string; command: string } {
  const v = t.trim();
  if (/^[0-9a-f]{40}$/i.test(v)) return { tool: "passwords", kind: "SHA-1 hash", command: `passwords ${v.toLowerCase()}` };
  if (/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v)) return { tool: "leaks", kind: "email address", command: `leaks ${v} --kind email` };
  if (/^[a-z0-9-]+(\.[a-z0-9-]+)+$/i.test(v)) return { tool: "leaks", kind: "domain", command: `leaks ${v} --kind domain` };
  return { tool: "leaks", kind: "username", command: `leaks ${v} --kind username` };
}
