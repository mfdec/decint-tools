# Security notes for this bundle

## Next.js upgraded 14.2.21 → 14.2.35

The version in the original repo carried a set of advisories, one of which is
directly load-bearing for this app:

> **Authorization Bypass in Next.js Middleware** — [GHSA-f82v-jwr5-mffw](https://github.com/advisories/GHSA-f82v-jwr5-mffw)

DECINT uses `frontend/middleware.ts` to gate `/console`, so this is exactly the
mechanism the advisory concerns. It was fixed in **14.2.25**.

This bundle pins **14.2.35** — the newest release in the same minor line, which
also picks up thirteen further patches. It is a patch-level move with no API
changes; the build was re-run and re-verified after the upgrade.

`eslint-config-next` was moved to 14.2.35 to match.

### What is still reported, and why it was left

`npm audit --omit=dev` still reports three **high** findings against
`postcss@8.4.31`, which is pinned *inside* `next@14.2.35`:

- XSS via unescaped `</style>` in CSS stringify output
- Arbitrary file read via attacker-controlled `sourceMappingURL`
- Path traversal in source-map auto-loading

These are **build-time only**. postcss processes your own stylesheets during
`npm run build`; it does not appear in the built server bundle (verified —
`grep -rl postcss .next/server` returns nothing) and never touches visitor
input. Exploiting them requires the ability to feed you malicious CSS, which
means you already have a bigger problem.

npm's only offered fix is `next@16.3.2` — two major versions, with breaking
changes to the App Router. That is a deliberate migration project, not something
to slip into a deployment bundle. Left as a considered decision rather than an
oversight.

To revisit later:

```bash
cd frontend
npm audit --omit=dev
npx @next/codemod@latest upgrade   # when you choose to do the v16 migration
```

## The three settings that decide whether your server is safe

| Setting | Ships as | Must be | Why |
|---|---|---|---|
| `OPERATOR_TOKEN` | *empty* | a random value | `config.py` defines `auth_enabled` as "is this non-empty". Empty means **no authentication at all**. |
| `COOKIE_SECURE` | `false` | `true` | Without it the session cookie will travel over plain HTTP. |

`deploy/decint-server-install.sh` sets both correctly. If you configure a
server by hand, these are the ones to check twice.

## Good news

`.gitignore` is genuinely thorough — `.env`, `.env.*`, `*.pem`, `*.key`,
`id_ed25519*`, `id_rsa*`, `node_modules/`, `.venv/`, `backend/data/`, `*.db` are
all excluded. Nothing sensitive can reach a public repository by accident.
