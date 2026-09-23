#!/usr/bin/env python3
"""
issue.py  -  one invoice, end to end.   python3 scripts/issue.py <command> ...

  new     --vat 999999999 --item service --net 900 [--descr ...] [--date YYYY-MM-DD] [--payment credit|bank|cash|cheque]
          [--project 000001] [--notes "..."]            -> writes a DRAFT json, prints the timologio spec
  spec    <id>                                          -> prints the timologio spec again (for TL.build)
  previewed <id>                                        -> marks the draft as previewed (after the PDF was shown)
  issued  <id> --mark 4000... --aa 1 [--series MYDATA]  -> renames DRAFT to MYDATA-000001, status issued
  pdf     <id>                                          -> copies printinvoice<mark>.pdf from the Chrome download
                                                           folder next to the json
  erp     <id> [--dry-run] [--yes] [--adapter sap_b1]   -> customer (if new) + A/R invoice in the ERP (adapters/)
  email   <id>                                          -> prints to / subject / body / attachment for the Gmail step
  emailed <id> --to a@b.gr --to c@d.gr                  -> records the send
  list    [--year 2026]                                 -> table of all invoices and their state
  show    <id>                                          -> full json

Nothing here talks to the browser. The timologio steps run inside the Chrome tab
with scripts/timologio_client.js (Claude injects it). Order of a normal run:

  new -> TL.build(spec) -> TL.preview() -> TL.showPdf()   [user says «ναι»]
  -> TL.issue() -> issued -> pdf -> erp --yes -> email (Gmail with the PDF) -> emailed
"""
import argparse, json, os, shutil, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import invoice_store as store  # noqa: E402

def download_dirs():
    """Where Chrome saves the timologio PDF. config.json "downloads_dirs" first, then ~/Downloads."""
    dirs = [Path(os.path.expanduser(d)) for d in store.config().get("downloads_dirs", [])]
    return dirs + [Path.home() / "Downloads"]


def cmd_new(a):
    inv = store.new_draft(a.vat, a.item, a.net, descr=a.descr, issue_date=a.date, payment=a.payment,
                          project=a.project, notes=a.notes or "", quantity=a.quantity)
    if not inv["customer"]["name"]:
        print("NOTE: customer name unknown locally; timologio will fill it from the registry (create the customer first if new).")
    p = store.save(inv)
    print("draft:", p)
    print(store.mydata_summary(inv))
    print("SPEC:", json.dumps(store.to_timologio_spec(inv), ensure_ascii=False))


def cmd_spec(a):
    inv = store.load(a.id)
    print(json.dumps(store.to_timologio_spec(inv), ensure_ascii=False))


def cmd_previewed(a):
    inv = store.load(a.id)
    inv["status"] = "previewed"
    store.audit(inv, "previewed")
    store.save(inv)
    print(store.mydata_summary(inv))


def cmd_issued(a):
    inv = store.load(a.id)
    if inv["mydata"]["mark"]:
        sys.exit(f"{a.id} already issued with MARK {inv['mydata']['mark']}")
    inv["series"] = a.series
    inv["number"] = int(a.aa)
    inv["mydata"].update(mark=str(a.mark), transmitted_at=store._now())
    inv["status"] = "issued"
    store.audit(inv, "issued", mark=str(a.mark), aa=int(a.aa))
    new_id = f"{a.series}-{int(a.aa):06d}"
    p = store.rename(inv, new_id)
    if inv["customer"]["name"]:
        store.save_customer(inv["customer"]["vat"], name=inv["customer"]["name"], email=inv["customer"].get("email"))
    print("issued:", p)
    print(store.mydata_summary(inv))


def cmd_pdf(a):
    inv = store.load(a.id)
    mark = inv["mydata"]["mark"]
    if not mark:
        sys.exit("no MARK yet")
    src = None
    for d in download_dirs():
        cands = sorted(d.glob(f"printinvoice{mark}*.pdf"), key=lambda x: x.stat().st_mtime, reverse=True) if d.exists() else []
        if cands:
            src = cands[0]
            break
    if not src:
        sys.exit(f"printinvoice{mark}.pdf not found in {[str(d) for d in download_dirs()]}. Open "
                 f"https://mydata.aade.gr/timologio/Invoice/PrintInvoice2PdfNew?mark={mark} in the logged-in Chrome first.")
    dst = store.find(inv["id"]).with_name(f"{inv['id']}_{inv['customer']['vat']}_{mark}.pdf")
    shutil.copy2(src, dst)
    inv["pdf"] = str(dst.relative_to(store.SKILL_DIR))
    store.audit(inv, "pdf", file=inv["pdf"])
    store.save(inv)
    print("pdf:", dst)


def cmd_erp(a):
    """Push the issued invoice into the ERP named in config.json ("erp": {"adapter": "sap_b1"})."""
    inv = store.load(a.id)
    name = a.adapter or store.config().get("erp", {}).get("adapter")
    if not name:
        sys.exit('no ERP adapter configured (config.json "erp.adapter", e.g. "sap_b1")')
    sys.path.insert(0, str(HERE.parent / "adapters"))
    import importlib
    adapter = importlib.import_module(name)
    if not a.dry_run:
        if not a.yes:
            sys.exit("add --yes to post to the ERP (after the user confirmed), or --dry-run to preview")
        os.environ["WRITE_ENABLED"] = "1"
    adapter.post_invoice(inv, dry=a.dry_run)
    if not a.dry_run and hasattr(adapter, "verify_single_transmission"):
        adapter.verify_single_transmission(inv)


def cmd_email(a):
    inv = store.load(a.id)
    c, t = inv["customer"], inv["totals"]
    label = {"2.1": "Τιμολόγιο Παροχής Υπηρεσιών", "1.1": "Τιμολόγιο Πώλησης"}.get(inv["type"], "Παραστατικό")
    issuer = store.ISSUER()
    subject = f"{label} {inv['series']}/{inv['number']} - {issuer.get('name', '')}"
    d = inv["issue_date"]
    dd = f"{d[8:10]}/{d[5:7]}/{d[0:4]}"
    body = (f"Καλησπέρα σας,\n\nΕπισυνάπτουμε το {label} {inv['series']}/{inv['number']} της {dd} "
            f"({inv['lines'][0]['description']}, καθαρή αξία {t['net']:.2f} €, ΦΠΑ {inv['lines'][0]['vat_rate']}% {t['vat']:.2f} €, "
            f"σύνολο {t['gross']:.2f} €).\n\nΜΑΡΚ myDATA: {inv['mydata']['mark']}\n\n"
            f"Με εκτίμηση,\n{issuer.get('name', '')}\n{issuer.get('street', '')}, {issuer.get('postal_code', '')} {issuer.get('city', '')}, ΑΦΜ {issuer.get('vat', '')}")
    to = [x for x in [c.get("email")] if x] + list(store.config().get("email", {}).get("always_cc", []))
    print(json.dumps({"to": to, "subject": subject, "body": body,
                      "attachment": str(store.SKILL_DIR / inv["pdf"]) if inv.get("pdf") else None}, ensure_ascii=False, indent=1))


def cmd_emailed(a):
    inv = store.load(a.id)
    inv["sync"]["email"] = {"sent_at": store._now(), "to": a.to}
    store.audit(inv, "emailed", to=a.to)
    store.save(inv)
    print(store.mydata_summary(inv))


def cmd_list(a):
    for inv in store.all_invoices():
        if a.year and not inv["issue_date"].startswith(str(a.year)):
            continue
        print(store.mydata_summary(inv))


def cmd_show(a):
    print(json.dumps(store.load(a.id), ensure_ascii=False, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    p = sp.add_parser("new"); p.add_argument("--vat", required=True); p.add_argument("--item", required=True)
    p.add_argument("--net", required=True, type=float); p.add_argument("--descr"); p.add_argument("--date")
    p.add_argument("--payment", default="credit", choices=list(store.PAYMENT)); p.add_argument("--project")
    p.add_argument("--notes"); p.add_argument("--quantity", type=int, default=1); p.set_defaults(f=cmd_new)
    for name, f in [("spec", cmd_spec), ("previewed", cmd_previewed), ("pdf", cmd_pdf), ("email", cmd_email), ("show", cmd_show)]:
        p = sp.add_parser(name); p.add_argument("id"); p.set_defaults(f=f)
    p = sp.add_parser("issued"); p.add_argument("id"); p.add_argument("--mark", required=True); p.add_argument("--aa", required=True)
    p.add_argument("--series", default="MYDATA"); p.set_defaults(f=cmd_issued)
    p = sp.add_parser("erp"); p.add_argument("id"); p.add_argument("--adapter"); p.add_argument("--dry-run", action="store_true"); p.add_argument("--yes", action="store_true"); p.set_defaults(f=cmd_erp)
    p = sp.add_parser("emailed"); p.add_argument("id"); p.add_argument("--to", action="append", default=[]); p.set_defaults(f=cmd_emailed)
    p = sp.add_parser("list"); p.add_argument("--year", type=int); p.set_defaults(f=cmd_list)
    a = ap.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
