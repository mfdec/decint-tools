"""Command-line front end — everything the GUI does, scriptable.

    decint-exe-maker build --project DIR --entry main.py --name "My App" [...]
    decint-exe-maker issue --product my-app --customer "Jane" --months 6
    decint-exe-maker verify <key or .lic file>
    decint-exe-maker products
    decint-exe-maker machine-id
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from . import APP_NAME, __version__, analyzer, builder, settings, vendor
from .runtime import licensing

COMMANDS = ("build", "issue", "verify", "products", "machine-id", "gui")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="decint-exe-maker", description=f"{APP_NAME} {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="wrap a Python project in DECINT licensing and build an EXE")
    b.add_argument("--project", required=True, help="project folder")
    b.add_argument("--entry", help="entry script relative to the project (auto-detected if omitted)")
    b.add_argument("--name", help="product name (auto-suggested if omitted)")
    b.add_argument("--product-id", help="slug used for keys/licenses (default: from name)")
    b.add_argument("--version", dest="ver", default=None)
    b.add_argument("--description", default="")
    b.add_argument("--console", action="store_true", help="console app (default: auto-detect)")
    b.add_argument("--gui", action="store_true", help="windowed app (default: auto-detect)")
    b.add_argument("--out", help="output folder")
    b.add_argument("--icon", default="", help=".ico file (default: DECINT shield)")
    b.add_argument("--onedir", action="store_true")
    b.add_argument("--no-splash", action="store_true")
    b.add_argument("--no-install", action="store_true", help="don't pip-install missing packages")
    b.add_argument("--hidden-import", action="append", default=[])
    b.add_argument("--collect-all", action="append", default=[])
    b.add_argument("--exclude-module", action="append", default=[])
    b.add_argument("--data", action="append", default=None, help="project-relative file/folder to bundle")
    b.add_argument("--chdir", action="store_true", help="run the app with cwd = bundle folder")
    b.add_argument("--no-license", action="store_true")
    b.add_argument("--bind-machine", action="store_true")
    b.add_argument("--trial", type=int, default=0, help="free trial days")
    b.add_argument("--contact", default=None, help="vendor contact shown on activation screen")
    b.add_argument("--activation-ui", choices=["auto", "tk", "console"], default="auto")
    b.add_argument("--python", default=None, help="interpreter to build with")
    b.add_argument("--ai", action="store_true", help="let Venice AI refine the analysis first")

    i = sub.add_parser("issue", help="issue a license for a built product")
    i.add_argument("--product", required=True)
    i.add_argument("--customer", required=True)
    i.add_argument("--email", default="")
    i.add_argument("--months", type=float, default=6)
    i.add_argument("--expires", help="YYYY-MM-DD (overrides --months)")
    i.add_argument("--machine", help="bind to this machine ID")
    i.add_argument("--feature", action="append", default=[])
    i.add_argument("--note", default="")
    i.add_argument("--out", help="write the key to this .lic file")

    v = sub.add_parser("verify", help="decode and check a license key")
    v.add_argument("key", help="key text or path to a .lic file")

    sub.add_parser("products", help="list products with signing keys")
    sub.add_parser("machine-id", help="print this machine's ID")
    sub.add_parser("gui", help="open the GUI")
    return p


def cmd_build(a) -> int:
    cfg = settings.load()
    rep = analyzer.analyze(a.project)
    print(f"static: entry={rep.entry} type={rep.app_type} third-party={', '.join(rep.third_party) or 'none'}")
    ai = None
    if a.ai:
        from .venice import Venice
        try:
            ai = Venice(settings.venice_key(cfg), cfg.get("venice_model", "")).analyze_project(
                rep.as_dict(), analyzer.excerpts(rep))
            print(f"ai: entry={ai['entry_point']} type={ai['app_type']} name='{ai['product_name']}'")
            for r in ai["risks"]:
                print(f"ai risk: {r}")
        except Exception as ex:  # noqa: BLE001
            print(f"ai analysis failed: {ex}", file=sys.stderr)
    g = lambda key, default: (ai or {}).get(key) or default
    entry = a.entry or g("entry_point", rep.entry)
    if not entry:
        print("no entry script found; pass --entry", file=sys.stderr)
        return 2
    app_type = "console" if a.console else "gui" if a.gui else g("app_type", rep.app_type)
    spec = builder.BuildSpec(
        project_dir=a.project, entry_file=entry, product_name=a.name or g("product_name", rep.suggested_name),
        product_id=a.product_id or "", version=a.ver or g("version", rep.suggested_version),
        description=a.description or g("description", ""), app_type=app_type, onefile=not a.onedir,
        icon_path=a.icon, splash=not a.no_splash and app_type == "gui",
        hidden_imports=list(dict.fromkeys(rep.hidden_imports + g("hidden_imports", []) + a.hidden_import)),
        collect_all=list(dict.fromkeys(rep.collect_all + g("collect_all", []) + a.collect_all)),
        exclude_modules=list(dict.fromkeys(g("exclude_modules", []) + a.exclude_module)),
        data_files=a.data if a.data is not None else g("data_files", rep.data_files),
        chdir_to_bundle=a.chdir or bool(g("chdir_to_bundle", False)), install_missing=not a.no_install,
        pip_requirements=g("pip_requirements", rep.pip_requirements), third_party_imports=rep.third_party,
        licensing=not a.no_license, bind_machine=a.bind_machine, trial_days=a.trial,
        vendor_contact=a.contact if a.contact is not None else cfg.get("vendor_contact", ""),
        activation_ui=a.activation_ui, output_dir=a.out or cfg.get("output_dir", ""),
        python=a.python or cfg.get("build_python", ""), company=cfg.get("company") or "DECINT",
        copyright_holder=cfg.get("copyright_holder") or "DECINT")
    try:
        res = builder.build(spec, print)
    except builder.BuildCancelled:
        return 130
    if not res.ok:
        print(f"BUILD FAILED: {res.error}\nlog: {res.log_path}", file=sys.stderr)
        return 1
    print(f"OK {res.exe_path}")
    if spec.licensing:
        print(f"issue keys with:  decint-exe-maker issue --product {spec.product_id} --customer NAME --months 6")
    return 0


def cmd_issue(a) -> int:
    expires = None
    if a.expires:
        expires = dt.datetime.strptime(a.expires, "%Y-%m-%d").replace(hour=23, minute=59, second=59).timestamp()
    text, payload = vendor.issue_license(a.product, customer=a.customer, email=a.email, months=a.months,
                                         expires=expires, machine_id=a.machine, features=a.feature, note=a.note)
    print(f"# {licensing.describe(payload)}", file=sys.stderr)
    if a.out:
        Path(a.out).write_text(text + "\n", encoding="utf-8")
        print(f"wrote {a.out}", file=sys.stderr)
    else:
        print(text)
    return 0


def cmd_verify(a) -> int:
    # os.path.isfile (unlike Path.is_file) returns False instead of raising on a
    # 500-character "filename" — which is what a pasted key looks like to it.
    text = Path(a.key).read_text() if os.path.isfile(a.key) else a.key
    try:
        r = vendor.inspect_license(text)
    except licensing.LicenseError as ex:
        print(f"INVALID: {ex}")
        return 1
    print(json.dumps(r["payload"], indent=1))
    print("expires:", licensing.fmt_date(r["payload"]["exp"]), f"({licensing.days_left(r['payload'])} days left)")
    if r["verified"] is True:
        print("signature: valid")
        return 0
    print("signature:", "INVALID — " + r["reason"] if r["verified"] is False else "unchecked — " + r["reason"])
    return 1 if r["verified"] is False else 0


def cmd_products(_a) -> int:
    rows = vendor.list_products()
    if not rows:
        print("no products yet — build one first")
    for p in rows:
        n = len(vendor.ledger(p["product_id"]))
        print(f"{p['product_id']:30s} {p['product_name']:30s} key {p['fingerprint']}  {n} license(s)")
    return 0


def main(argv: list[str] | None = None) -> int:
    a = _parser().parse_args(argv)
    if a.cmd == "gui":
        from .gui import main as gui_main
        return gui_main()
    if a.cmd == "machine-id":
        print(licensing.machine_id())
        return 0
    return {"build": cmd_build, "issue": cmd_issue, "verify": cmd_verify, "products": cmd_products}[a.cmd](a)
