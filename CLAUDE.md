# decint-tools — working agreements for Claude

## GitHub is the system of record (owner's standing instruction, 2026-09-17)

- Remote: https://github.com/mfdec/decint-tools (private). Branch: `main`.
- **Every change made in this directory gets committed and pushed to GitHub as
  part of the same task.** Commit at each meaningful iteration, not only at the
  end, and never leave un-pushed work on the server. This is standing
  authorization — do not ask before committing or pushing routine work.
- Commit directly to `main`. No branches/PRs unless asked.
- Commit messages: short imperative summary line, body only when it explains
  *why*. End with the Co-Authored-By trailer the session provides.

## Before every commit

- This must print nothing (secrets/DB/caches are never committed):

      git status --porcelain | grep -E '\.env$|\.env\.bak|\.db$|\.mmdb$|\.jks$|\.keystore$|node_modules|\.venv|\.next|\.cache/|\.npm/|\.gradle/'

- Only `backend/.env.example` is tracked. `backend/.env*`, `backend/data/`,
  private keys (including the Android upload keystore) and tool caches stay
  out (see `.gitignore`).

## Auth & ownership

- `gh` is logged in as **mfdec** via the device-code flow; git uses the `gh`
  credential helper over HTTPS. If a push fails with 401/403, re-run
  `gh auth login --web` and give the owner the one-time code — never ask them
  to paste a token into chat.
- Project files belong to the `decint` user; Claude runs as root. After any git
  operation that writes to `.git`, run `chown -R decint:decint .git` so the
  `decint` user can still use git.

## Deploy note

- The server pulls from this repo (see `docs/INSTALL-WALKTHROUGH.md` and
  `docs/UPDATE-STEPS.md`). Pushing to `main` is how updates reach other hosts.
