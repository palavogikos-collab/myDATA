#!/usr/bin/env python3
"""
myDATA (AADE) REST API client, read only.

Reads only. Issuing stays with the SAP Hellenization add-on.
Credentials in <skill-dir>/.env:  MYDATA_USER, MYDATA_KEY  (and MYDATA_ENV=prod|dev)

Commands:
  income   --period 2026 | --from 2026-01-01 --to 2026-06-30 [--vat 123456789] [--by customer|month|type|doc]
  expenses --period ...   (documents suppliers issued to us)
  transmitted [--mark N]  (our own docs as stored by AADE, full detail, paginated)
  received    [--mark N]  (docs issued to us)
  raw <endpoint> [--params k=v ...]
"""

import argparse
import calendar
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    import requests
except ImportError:
    print("pip install requests", file=sys.stderr)
    sys.exit(1)

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
ENV_FILE = Path(os.environ.get("SAP_ENV_FILE", SKILL_DIR / ".env"))


def _load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("MYDATA_USER", "MYDATA_KEY", "MYDATA_ENV"):
        if k in os.environ:
            env[k] = os.environ[k]
    return env


ENV = _load_env()
BASE = "https://mydataapidev.aade.gr" if ENV.get("MYDATA_ENV", "prod") == "dev" else "https://mydatapi.aade.gr/myDATA"


def _die(msg):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def _headers():
    u, k = ENV.get("MYDATA_USER"), ENV.get("MYDATA_KEY")
    if not u or not k:
        _die(f"MYDATA_USER / MYDATA_KEY missing in {ENV_FILE}")
    return {"aade-user-id": u, "Ocp-Apim-Subscription-Key": k, "Accept": "application/xml"}


def _get(endpoint, params):
    r = requests.get(f"{BASE}/{endpoint}", headers=_headers(), params=params, timeout=90)
    if r.status_code != 200:
        _die(f"{endpoint} -> {r.status_code}: {r.text[:500]}")
    return r.text


# --------------------------------------------------------------- XML utils ---

def _local(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


def _to_dict(el):
    """Recursive element -> dict (repeated children become lists)."""
    out = {}
    for c in el:
        name = _local(c.tag)
        val = _to_dict(c) if len(c) else (c.text or "").strip()
        if name in out:
            if not isinstance(out[name], list):
                out[name] = [out[name]]
            out[name].append(val)
        else:
            out[name] = val
    return out


def _unwrap(xml_text):
    """AADE sometimes returns the XML escaped inside a <string> envelope."""
    root = ET.fromstring(xml_text)
    if _local(root.tag) == "string" and root.text and "<" in root.text:
        return root.text
    return xml_text


def _parse(xml_text, item_tag):
    root = ET.fromstring(_unwrap(xml_text))
    items = [_to_dict(e) for e in root.iter() if _local(e.tag) == item_tag]
    cont = None
    for e in root.iter():
        if _local(e.tag) == "continuationToken":
            d = _to_dict(e)
            cont = (d.get("nextPartitionKey"), d.get("nextRowKey"))
    return items, cont


# ----------------------------------------------------------------- helpers ---

def _resolve_period(period=None, dfrom=None, dto=None):
    today = date.today()
    if dfrom or dto:
        f = datetime.strptime(dfrom, "%Y-%m-%d").date() if dfrom else date(today.year, 1, 1)
        t = datetime.strptime(dto, "%Y-%m-%d").date() if dto else today
        return f, t
    p = (period or "this year").lower().strip()
    if p in ("this year", "φέτος"):
        return date(today.year, 1, 1), today
    if p in ("last year", "πέρσι"):
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if p in ("this month", "αυτόν τον μήνα"):
        return today.replace(day=1), today
    if p in ("last month", "περασμένο μήνα"):
        first = today.replace(day=1)
        lp = first - timedelta(days=1)
        return lp.replace(day=1), lp
    if p.startswith("last ") and p.endswith(" days"):
        return today - timedelta(days=int(p.split()[1])), today
    if len(p) == 7 and p[4] == "-":
        y, m = int(p[:4]), int(p[5:])
        return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
    if len(p) == 4 and p.isdigit():
        return date(int(p), 1, 1), date(int(p), 12, 31)
    _die(f"unknown period '{period}'")


def _gr(d):
    return d.strftime("%d/%m/%Y")


def _eur(x):
    try:
        return f"{float(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " €"
    except Exception:
        return str(x)


def _f(x):
    try:
        return float(x or 0)
    except Exception:
        return 0.0


def _table(rows, cols):
    if not rows:
        print("(no rows)")
        return
    w = [max(len(str(c)), *(len(str(r.get(c, ""))) for r in rows)) for c in cols]
    print("  ".join(str(c).ljust(x) for c, x in zip(cols, w)))
    print("-" * (sum(w) + 2 * len(w)))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(x)[:x] for c, x in zip(cols, w)))


VAT_INDEX_FILE = SKILL_DIR / "data" / "vat_index.json"
_VAT_INDEX = None


def vat_name(v):
    """Customer name from an optional ERP export index (data/vat_index.json), else ''."""
    global _VAT_INDEX
    if _VAT_INDEX is None:
        try:
            _VAT_INDEX = json.loads(VAT_INDEX_FILE.read_text(encoding="utf-8"))
        except Exception:
            _VAT_INDEX = {}
    e = _VAT_INDEX.get(str(v).zfill(9))
    return e["name"] if e else ""


INV_TYPES = {
    "1.1": "Τιμολόγιο Πώλησης", "1.2": "Τιμ. Πώλησης Ενδοκοινοτικές", "1.3": "Τιμ. Πώλησης Τρίτες Χώρες",
    "2.1": "Τιμολόγιο Παροχής Υπηρεσιών", "2.2": "ΤΠΥ Ενδοκοινοτικές", "2.3": "ΤΠΥ Τρίτες Χώρες",
    "5.1": "Πιστωτικό Συσχετιζόμενο", "5.2": "Πιστωτικό Μη Συσχετιζόμενο",
    "11.1": "ΑΛΠ", "11.2": "ΑΠΥ", "11.4": "Πιστωτικό Λιανικής", "17.1": "Μισθοδοσία", "17.3": "Λοιπές Εγγραφές",
}


# ---------------------------------------------------------------- commands ---

def _book_rows(items):
    # AADE already returns credit notes (5.x, 11.4) with a negative sign. Use values as they come.
    rows = []
    for it in items:
        t = it.get("invType", "")
        s = 1
        rows.append({
            "date": it.get("issueDate", "")[:10], "vat": it.get("counterVatNumber", ""),
            "name": vat_name(it.get("counterVatNumber", ""))[:40],
            "type": t, "typeName": INV_TYPES.get(t, t), "mark": it.get("maxMark") or it.get("mark", ""),
            "count": int(it.get("count") or 1),
            "invNo": f"{it.get('series','')} {it.get('aa','')}".strip(),
            "net": s * _f(it.get("netValue")), "vat_amt": s * _f(it.get("vatAmount")),
            "gross": s * _f(it.get("grossValue")), "cancelled": it.get("cancelledByMark") or "",
        })
    return rows


def _income_or_expenses(args, endpoint):
    f, t = _resolve_period(args.period, args.from_, args.to)
    params = {"dateFrom": _gr(f), "dateTo": _gr(t)}
    if args.vat:
        params["counterVatNumber"] = args.vat
    xml = _get(endpoint, params)
    items, _ = _parse(xml, "bookInfo")
    rows = [r for r in _book_rows(items) if not r["cancelled"]]
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return
    ndocs = sum(r["count"] for r in rows)
    print(f"{endpoint} {f} .. {t}: {ndocs} docs, net {_eur(sum(r['net'] for r in rows))}, "
          f"gross {_eur(sum(r['gross'] for r in rows))}\n")
    key = {"customer": lambda r: f"{r['vat']} {r['name']}", "month": lambda r: r["date"][:7],
           "type": lambda r: f"{r['type']} {r['typeName']}", "doc": None}[args.by]
    if key is None:
        rows.sort(key=lambda r: r["date"], reverse=True)
        for r in rows:
            r["net"], r["gross"] = _eur(r["net"]), _eur(r["gross"])
        _table(rows[: args.top], ["date", "vat", "name", "type", "count", "net", "gross", "mark"])
        return
    agg = defaultdict(lambda: {"n": 0, "net": 0.0, "gross": 0.0})
    for r in rows:
        a = agg[key(r)]
        a["n"] += r["count"]; a["net"] += r["net"]; a["gross"] += r["gross"]
    out = [{"key": k, "docs": v["n"], "net": _eur(v["net"]), "gross": _eur(v["gross"]), "_n": v["net"]}
           for k, v in agg.items()]
    out.sort(key=lambda x: -x["_n"] if args.by != "month" else x["key"])
    _table(out[: args.top], ["key", "docs", "net", "gross"])


def cmd_income(args):
    _income_or_expenses(args, "RequestMyIncome")


def cmd_expenses(args):
    _income_or_expenses(args, "RequestMyExpenses")


def _docs(args, endpoint):
    mark = args.mark
    out, cont = [], None
    while True:
        params = {"mark": mark}
        if cont:
            params["nextPartitionKey"], params["nextRowKey"] = cont
        if args.date_from:
            params["dateFrom"] = _gr(datetime.strptime(args.date_from, "%Y-%m-%d").date())
        if args.date_to:
            params["dateTo"] = _gr(datetime.strptime(args.date_to, "%Y-%m-%d").date())
        xml = _get(endpoint, params)
        items, cont = _parse(xml, "invoice")
        out.extend(items)
        if not cont or not items or len(out) >= args.limit:
            break
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return
    rows = []
    for inv in out:
        h = inv.get("invoiceHeader", {}) if isinstance(inv.get("invoiceHeader"), dict) else {}
        cp = inv.get("counterpart", {}) if isinstance(inv.get("counterpart"), dict) else {}
        iss = inv.get("issuer", {}) if isinstance(inv.get("issuer"), dict) else {}
        s = inv.get("invoiceSummary", {}) if isinstance(inv.get("invoiceSummary"), dict) else {}
        rows.append({"date": h.get("issueDate", ""), "type": h.get("invoiceType", ""),
                     "no": f"{h.get('series','')} {h.get('aa','')}".strip(),
                     "issuer": iss.get("vatNumber", ""), "counterpart": cp.get("vatNumber", ""),
                     "net": _eur(s.get("totalNetValue")), "gross": _eur(s.get("totalGrossValue")),
                     "mark": inv.get("mark", "")})
    _table(rows, ["date", "type", "no", "issuer", "counterpart", "net", "gross", "mark"])
    print(f"\n{len(rows)} docs. Last mark: {max((int(r['mark']) for r in rows if r['mark']), default=mark)}")


def cmd_transmitted(args):
    _docs(args, "RequestTransmittedDocs")


def cmd_received(args):
    _docs(args, "RequestDocs")


def cmd_raw(args):
    params = dict(kv.split("=", 1) for kv in (args.params or []))
    print(_unwrap(_get(args.endpoint, params))[: args.chars])


def build_parser():
    p = argparse.ArgumentParser(description="myDATA REST client (read only)")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    for name, fn in (("income", cmd_income), ("expenses", cmd_expenses)):
        s = sub.add_parser(name)
        s.add_argument("--period"); s.add_argument("--from", dest="from_"); s.add_argument("--to")
        s.add_argument("--vat"); s.add_argument("--by", choices=["customer", "month", "type", "doc"], default="customer")
        s.add_argument("--top", type=int, default=40)
        s.set_defaults(fn=fn)

    for name, fn in (("transmitted", cmd_transmitted), ("received", cmd_received)):
        s = sub.add_parser(name)
        s.add_argument("--mark", type=int, default=0); s.add_argument("--date-from"); s.add_argument("--date-to")
        s.add_argument("--limit", type=int, default=2000)
        s.set_defaults(fn=fn)

    s = sub.add_parser("raw"); s.add_argument("endpoint"); s.add_argument("--params", nargs="*")
    s.add_argument("--chars", type=int, default=3000); s.set_defaults(fn=cmd_raw)
    return p


if __name__ == "__main__":
    a = build_parser().parse_args()
    a.fn(a)
