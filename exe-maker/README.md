# DECINT EXE Maker

Take any Python 3 app — something you built with Claude, Venice, or by hand —
and turn it into a **DECINT-branded Windows EXE that only runs with a
time-limited DECINT license key**. Sell a copy for 6 months, issue the key,
done.

```
exe-maker/
  decint_exe_maker.py      start the GUI (or the CLI — see below)
  build_maker.bat          Windows: one click → dist\DECINT-EXE-Maker.exe
  build_maker.py           same, cross-platform
  decint_exe_maker/
    gui.py                 the desktop app (tkinter, DECINT dark-violet theme)
    analyzer.py            static AST analysis of the target project
    venice.py              Venice AI: smart analysis + build-failure diagnosis
    builder.py             stages the project + runtime, runs PyInstaller
    vendor.py              per-product RSA keys, license issuing, ledger
    branding.py            DECINT icon / splash / Windows version info
    runtime/               what ships INSIDE every built EXE (stdlib only):
      launcher_template.py   real entry point: license check → activation UI → your app
      licensing.py           license format, verification, machine ID, storage
      rsa_lite.py            pure-Python RSA-2048 PKCS#1 v1.5 (verify needs no deps)
  examples/                two tiny apps to try it on
  tests/                   unit tests + a real end-to-end PyInstaller build
```

## Quick start (Windows)

1. Install Python 3.9+ from python.org (tick **Add to PATH**).
2. Either run from source:
   ```bat
   pip install -r requirements.txt
   python decint_exe_maker.py
   ```
   or double-click **`build_maker.bat`** to get `dist\DECINT-EXE-Maker.exe`
   (a standalone GUI) and `dist\decint-exe-maker-cli.exe`.
3. **Settings tab** → paste your Venice API key → *Test* → *Save*.
   (Or set the `VENICE_API_KEY` environment variable; it takes precedence.
   The key is stored in `%APPDATA%\DECINT\ExeMaker\settings.json`, never in
   any project or build.)

## Workflow

**Build tab**
1. *Browse…* to the project folder. Static analysis runs immediately and
   fills in the entry script, product name, GUI/console, data files and any
   PyInstaller hidden-imports it knows about.
2. *Analyze with Venice AI* — the model reads the static report plus the
   most relevant source files and refines everything: the true entry point,
   a proper product name/description, packages that need `collect-all`,
   which files the app opens at runtime, and a list of **risks** (things
   that break once frozen, e.g. `multiprocessing` without `freeze_support`).
3. Check the licensing options (see below), pick an output folder, **BUILD EXE**.
   Missing third-party packages are pip-installed into the build interpreter
   automatically.
4. If PyInstaller fails: **Diagnose failure with AI** reads the log and
   returns concrete fixes; **Apply AI fixes** puts them into the form
   (hidden imports, collect-all, pip installs, onedir, …). Build again.

**Licenses tab**
- Pick the product, enter the customer, choose **6 months** (default) or an
  exact expiry date, optionally bind to their Machine ID, **GENERATE LICENSE**.
- *Copy key* or *Save .lic…* and send it to the customer. Every key you issue
  is recorded in the ledger (export to CSV).
- **Backup product key…** — do this once per product and keep the file
  somewhere safe. It is the private key; without it you can no longer issue
  keys for EXEs already shipped.

**Verify tab** — paste any key to see who it was issued to, when it expires,
and whether the signature is genuine. Also shows this machine's ID.

### What the customer sees

First launch shows a DECINT-branded activation window with their Machine ID
(copyable), a box to paste the key, and your vendor contact. A valid key is
saved to `%LOCALAPPDATA%\DECINT\<product-id>\license.lic` and the app starts
straight away on every later launch. 14 days before expiry they get a
warning; after expiry the activation window returns and asks for a new key.
Keys can also be dropped next to the EXE as `license.lic` / `<product-id>.lic`
or passed via the `DECINT_LICENSE` environment variable (text or path).

Your app can read `DECINT_LICENSE_CUSTOMER`, `DECINT_LICENSE_EXPIRES`
(unix time), `DECINT_LICENSE_FEATURES` (comma-separated) and
`DECINT_LICENSE_MODE` (`licensed` / `trial`) from `os.environ` to unlock
features per customer.

## How the licensing works

- One **RSA-2048 keypair per product**, generated on first build and stored
  in `%APPDATA%\DECINT\ExeMaker\keys\<product-id>.json` (mode 600).
- The EXE embeds only the **public** key. A license is one line:
  `DECINT1.<base64 JSON payload>.<base64 signature>` where the payload holds
  product id, customer, email, issued/expiry timestamps, optional machine
  ID, feature flags and a note. Changing a single character invalidates it.
- Verification is pure Python (one modular exponentiation), so the runtime
  adds **no dependencies** to the customer's binary and works fully offline.
- **Machine binding** (optional) hashes the Windows `MachineGuid`; reinstalls
  and new network adapters do not change it.
- **Free trial** (optional, N days) runs without a key, then asks for one.
- **Clock rollback** detection refuses to run if the system clock moves back
  more than 36 hours from the latest time the app has seen.

Honest limits: this is *offline, deterrent-grade* licensing. PyInstaller
bundles can be unpacked and the bytecode decompiled by a determined person;
if that matters for a particular product, run it through PyArmor before
building, or add an online activation call. Also: PyInstaller one-file EXEs
sometimes trip antivirus heuristics — code-signing the EXE fixes that.

## Command line

```bash
python decint_exe_maker.py build --project C:\apps\invoicer --ai --out C:\builds --contact sales@decint.tools
python decint_exe_maker.py issue --product invoicer --customer "Jane Doe" --email jane@x.io --months 6
python decint_exe_maker.py issue --product invoicer --customer "ACME" --expires 2027-03-31 --machine 53A7-EFA8-8AB2-DB1E --feature pro --out acme.lic
python decint_exe_maker.py verify acme.lic
python decint_exe_maker.py products
python decint_exe_maker.py machine-id
```

`build --help` lists every switch (`--console/--gui`, `--hidden-import`,
`--collect-all`, `--data`, `--trial`, `--bind-machine`, `--no-license`,
`--onedir`, `--python`, …).

## Notes

- **Build on Windows to get a .exe.** PyInstaller does not cross-compile;
  on Linux/macOS the same pipeline produces a native binary for that OS
  (which is how the test suite runs here).
- The build interpreter needs tkinter (python.org installers include it) and
  the target app's packages — the maker installs missing ones for you.
- Windows file properties show `CompanyName: DECINT`, your product name,
  version and description; the icon is the DECINT shield unless you pick
  another `.ico`. GUI apps get a DECINT splash screen while Python unpacks.

## Tests

```bash
pip install -r requirements.txt
python -m pytest -q tests/
```

`tests/test_build_e2e.py` really builds `examples/hello_console` and runs the
result with no key, a bad key, an expired key and a valid key (~30 s).
