#!/usr/bin/env python3
"""
SAP Business One Service Layer client (generic; company data comes from .env).

Credentials come from a .env file next to this script (or SAP_ENV_FILE).
Never pass credentials on the command line.

Read commands:   test, whoami, series, items, projects, customer, invoices,
                 open-ar, overdue, payments, analytics, sqlquery, invoice
Write commands:  issue, credit-note, activity   (require WRITE_ENABLED=1 in .env
                 and --yes on the command line)
"""

import argparse
import calendar
import json
import os
import sys
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
CONFIG_FILE = SKILL_DIR / "config.json"
SESSION_FILE = Path(os.environ.get("SAP_SESSION_FILE", Path.home() / ".sapb1_session.json"))


# ----------------------------------------------------------------- config ---

def _load_env():
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k in ("SAP_URL", "SAP_DB", "SAP_USER", "SAP_PASS", "WRITE_ENABLED"):
        if k in os.environ:
            env[k] = os.environ[k]
    return env


ENV = _load_env()
BASE_URL = ENV.get("SAP_URL", "https://your-b1-host/b1s/v1").rstrip("/")
COMPANY_DB = ENV.get("SAP_DB", "COMPANY_DB")
WRITE_ENABLED = ENV.get("WRITE_ENABLED", "0") == "1"

CONFIG = {}
if CONFIG_FILE.exists():
    CONFIG = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


def _die(msg, code=1):
    print(f"ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------- session ---

class SL:
    """Thin Service Layer client with session reuse."""

    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Prefer": "odata.maxpagesize=1000",
        })
        self._restore()

    def _apply(self, sid, route):
        # Send the session explicitly; the cookie jar is unreliable behind the
        # provider's nginx (ROUTEID) with mismatched requests/urllib3 versions.
        cookie = f"B1SESSION={sid}"
        if route:
            cookie += f"; ROUTEID={route}"
        self.s.headers["Cookie"] = cookie
        self.sid, self.route = sid, route

    def _restore(self):
        self.sid = self.route = None
        if SESSION_FILE.exists():
            try:
                d = json.loads(SESSION_FILE.read_text())
                if d.get("db") == COMPANY_DB and d.get("url") == BASE_URL and d.get("B1SESSION"):
                    self._apply(d["B1SESSION"], d.get("ROUTEID"))
            except Exception:
                pass

    def _save(self):
        SESSION_FILE.write_text(json.dumps({
            "db": COMPANY_DB, "url": BASE_URL,
            "B1SESSION": self.sid, "ROUTEID": self.route,
        }))
        try:
            SESSION_FILE.chmod(0o600)
        except Exception:
            pass

    def login(self):
        user, pw = ENV.get("SAP_USER"), ENV.get("SAP_PASS")
        if not user or not pw:
            _die(f"SAP_USER / SAP_PASS missing. Create {ENV_FILE} (see .env.example).")
        self.s.headers.pop("Cookie", None)
        r = self.s.post(f"{BASE_URL}/Login", json={
            "CompanyDB": COMPANY_DB, "UserName": user, "Password": pw}, timeout=30)
        if r.status_code != 200:
            _die(f"Login failed {r.status_code}: {r.text[:300]}")
        sid = r.json().get("SessionId") or r.cookies.get("B1SESSION")
        route = r.cookies.get("ROUTEID")
        if not route:
            for h in r.headers.get("Set-Cookie", "").split(","):
                if "ROUTEID=" in h:
                    route = h.split("ROUTEID=", 1)[1].split(";", 1)[0]
        if not sid:
            _die("Login returned no SessionId")
        self._apply(sid, route)
        self._save()
        if os.environ.get("SAP_DEBUG"):
            print(f"debug: login ok, session {sid[:8]}..., route {route}", file=sys.stderr)

    def _req(self, method, path, retry=True, **kw):
        url = path if path.startswith("http") else f"{BASE_URL}/{path.lstrip('/')}"
        r = self.s.request(method, url, timeout=kw.pop("timeout", 60), **kw)
        if r.status_code == 401 and retry:
            self.login()
            return self._req(method, path, retry=False, **kw)
        return r

    def get(self, path, **params):
        if params:
            from urllib.parse import quote
            SAFE = "(),'/:$=*"
            qs = "&".join(k + "=" + quote(str(v), safe=SAFE) for k, v in params.items())
            path = f"{path}{'&' if '?' in path else '?'}{qs}"
        r = self._req("GET", path)
        if r.status_code >= 400:
            _die(f"GET {path} -> {r.status_code}: {r.text[:400]}")
        return r.json() if r.text else {}

    def get_all(self, path, **params):
        """Follow odata.nextLink until exhausted."""
        out = []
        data = self.get(path, **params)
        out.extend(data.get("value", []))
        nxt = data.get("odata.nextLink") or data.get("@odata.nextLink")
        while nxt:
            data = self.get(nxt)
            out.extend(data.get("value", []))
            nxt = data.get("odata.nextLink") or data.get("@odata.nextLink")
        return out

    def post(self, path, body):
        r = self._req("POST", path, json=body)
        if r.status_code >= 400:
            _die(f"POST {path} -> {r.status_code}: {r.text[:600]}")
        return r.json() if r.text else {}

    def patch(self, path, body):
        r = self._req("PATCH", path, json=body)
        if r.status_code >= 400:
            _die(f"PATCH {path} -> {r.status_code}: {r.text[:600]}")
        return r.json() if r.text else {}


# ---------------------------------------------------------------- helpers ---

def _iso(d):
    return d.strftime("%Y-%m-%d")


def _resolve_period(period=None, dfrom=None, dto=None):
    today = date.today()
    if dfrom or dto:
        f = datetime.strptime(dfrom, "%Y-%m-%d").date() if dfrom else date(2000, 1, 1)
        t = datetime.strptime(dto, "%Y-%m-%d").date() if dto else today
        return f, t
    p = (period or "this month").lower().strip()
    if p in ("today", "σήμερα"):
        return today, today
    if p in ("yesterday", "χθες"):
        y = today - timedelta(days=1)
        return y, y
    if p in ("this week", "αυτή την εβδομάδα"):
        return today - timedelta(days=today.weekday()), today
    if p in ("last week", "περασμένη εβδομάδα"):
        s = today - timedelta(days=today.weekday() + 7)
        return s, s + timedelta(days=6)
    if p in ("this month", "αυτόν τον μήνα"):
        return today.replace(day=1), today
    if p in ("last month", "περασμένο μήνα", "περασμένος μήνας"):
        first = today.replace(day=1)
        last_prev = first - timedelta(days=1)
        return last_prev.replace(day=1), last_prev
    if p in ("this year", "φέτος"):
        return today.replace(month=1, day=1), today
    if p in ("last year", "πέρσι"):
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if p.startswith("last ") and p.endswith(" days"):
        n = int(p.split()[1])
        return today - timedelta(days=n), today
    if len(p) == 7 and p[4] == "-":  # YYYY-MM
        y, m = int(p[:4]), int(p[5:])
        return date(y, m, 1), date(y, m, calendar.monthrange(y, m)[1])
    if len(p) == 4 and p.isdigit():
        return date(int(p), 1, 1), date(int(p), 12, 31)
    _die(f"Unknown period '{period}'. Use e.g. 'this month', 'last month', '2026-09', '2025', or --from/--to.")


def _eur(x):
    try:
        return f"{float(x):,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
    except Exception:
        return str(x)


def _table(rows, cols, widths=None):
    if not rows:
        print("(no rows)")
        return
    widths = widths or [max(len(str(c)), *(len(str(r.get(c, ""))) for r in rows)) for c in cols]
    line = "  ".join(str(c).ljust(w)[:w] for c, w in zip(cols, widths))
    print(line)
    print("-" * len(line))
    for r in rows:
        print("  ".join(str(r.get(c, "")).ljust(w)[:w] for c, w in zip(cols, widths)))


def _out(args, rows, cols):
    if getattr(args, "json", False):
        print(json.dumps(rows, ensure_ascii=False, indent=1, default=str))
    else:
        _table(rows, cols)


# ------------------------------------------------------------- SQL layer ---
# Data Ownership hides list results for BusinessPartners / documents from
# non-superusers, while key access and raw SQL are not filtered. Reads go
# through Service Layer SQLQueries (HANA SQL) created once by `setup-queries`.

QUERIES = {
    "MD_BP": """SELECT "CardCode","CardName","CardType","GroupCode","LicTradNum","Balance",
        "E_Mail","Phone1","Cellular","SlpCode","GroupNum","CntctPrsn","validFor","frozenFor"
        FROM OCRD""",
    "MD_INV": """SELECT T0."DocEntry",T0."DocNum",T0."CardCode",T0."CardName",T0."DocDate",T0."DocDueDate",
        T0."DocTotal",T0."PaidToDate",T0."VatSum",T0."DocStatus",T0."CANCELED",T0."Project",T0."Comments",
        T0."Series",T0."SlpCode",T0."OwnerCode",T0."NumAtCard"
        FROM OINV T0 WHERE T0."CANCELED"='N'""",
    "MD_INV_LINES": """SELECT T1."DocEntry",T1."LineNum",T1."ItemCode",T1."Dscription",T1."Quantity",
        T1."Price",T1."LineTotal",T1."Project",T0."DocDate",T0."CardCode",T0."CardName",T0."DocNum"
        FROM INV1 T1 INNER JOIN OINV T0 ON T0."DocEntry"=T1."DocEntry" WHERE T0."CANCELED"='N'""",
    "MD_CN": """SELECT T0."DocEntry",T0."DocNum",T0."CardCode",T0."CardName",T0."DocDate",T0."DocTotal",
        T0."VatSum",T0."DocStatus",T0."Comments" FROM ORIN T0 WHERE T0."CANCELED"='N'""",
    "MD_CN_LINES": """SELECT T1."DocEntry",T1."LineNum",T1."ItemCode",T1."Dscription",T1."Quantity",
        T1."LineTotal",T1."Project",T0."DocDate",T0."CardCode",T0."CardName",T0."DocNum"
        FROM RIN1 T1 INNER JOIN ORIN T0 ON T0."DocEntry"=T1."DocEntry" WHERE T0."CANCELED"='N'""",
    "MD_RCT": """SELECT "DocEntry","DocNum","DocDate","CardCode","CardName","CashSum","TrsfrSum","CheckSum",
        "CreditSum","DocTotal","Canceled","Comments" FROM ORCT WHERE "Canceled"='N'""",
    "MD_VPM": """SELECT "DocEntry","DocNum","DocDate","CardCode","CardName","CashSum","TrsfrSum","CheckSum",
        "CreditSum","DocTotal","Canceled","Comments" FROM OVPM WHERE "Canceled"='N'""",
    "MD_PRJ": """SELECT "PrjCode","PrjName","Active","ValidFrom","ValidTo" FROM OPRJ""",
    "MD_ITEMS": """SELECT "ItemCode","ItemName","ItemType","validFor","SellItem" FROM OITM""",
}

_q_cache = {}


def q(code, sl=None):
    """Run a stored SQLQuery and return all rows (paginated)."""
    if code in _q_cache:
        return _q_cache[code]
    sl = sl or SL()
    r = sl._req("GET", f"SQLQueries('{code}')/List")
    if r.status_code == 404 or (r.status_code >= 400 and "not exist" in r.text.lower()):
        _die(f"SQL query {code} is not installed. Run:  python3 {sys.argv[0]} setup-queries")
    if r.status_code >= 400:
        _die(f"SQLQuery {code} -> {r.status_code}: {r.text[:400]}")
    data = r.json()
    rows = list(data.get("value", []))
    nxt = data.get("odata.nextLink") or data.get("@odata.nextLink")
    while nxt:
        data = sl.get(nxt)
        rows.extend(data.get("value", []))
        nxt = data.get("odata.nextLink") or data.get("@odata.nextLink")
    _q_cache[code] = rows
    return rows


def _d(v):
    """Date string from SL ('2026-09-18' or '2026-09-18T00:00:00Z') -> date."""
    return datetime.strptime(str(v)[:10], "%Y-%m-%d").date() if v else None


def _in_range(v, f, t):
    d = _d(v)
    return d is not None and f <= d <= t


def cmd_setup_queries(args):
    sl = SL()
    sl.login()
    for code, text in QUERIES.items():
        text = " ".join(text.split())
        r = sl._req("GET", f"SQLQueries('{code}')")
        body = {"SqlCode": code, "SqlName": code, "SqlText": text}
        if r.status_code == 200:
            sl.patch(f"SQLQueries('{code}')", {"SqlText": text})
            print(f"updated {code}")
        else:
            sl.post("SQLQueries", body)
            print(f"created {code}")
    print("\nverify: ", end="")
    n = len(q("MD_BP", sl))
    print(f"{n} business partners visible via SQL")


# ----------------------------------------------------------- read commands ---

def cmd_test(args):
    sl = SL()
    sl.login()
    r = sl._req("GET", "SQLQueries('MD_BP')")
    ok = r.status_code == 200
    print(f"OK  login to {COMPANY_DB} at {BASE_URL}")
    print(f"    write enabled : {WRITE_ENABLED}")
    print(f"    sql queries   : {'installed' if ok else 'MISSING, run setup-queries'}")
    if ok:
        print(f"    business partners: {len(q('MD_BP', sl))}")


def cmd_raw(args):
    """Print status and raw body of a GET, e.g. raw "BusinessPartners?$top=2" """
    sl = SL()
    r = sl._req("GET", args.path)
    print(f"GET {r.url}\nstatus {r.status_code}\n{r.text[:args.chars]}")


def cmd_whoami(args):
    print(f"user: {ENV.get('SAP_USER')}  db: {COMPANY_DB}")


def cmd_series(args):
    sl = SL()
    body = {"DocumentTypeParams": {"Document": str(args.object)}}
    d = sl.post("SeriesService_GetDocumentSeries", body)
    rows = [{"Series": s.get("Series"), "Name": s.get("Name"), "Next": s.get("NextNumber"),
             "Locked": s.get("Locked"), "Prefix": s.get("Prefix")}
            for s in d.get("value", d if isinstance(d, list) else [])]
    _out(args, rows, ["Series", "Name", "Next", "Locked", "Prefix"])


def cmd_items(args):
    rows = [r for r in q("MD_ITEMS") if r.get("SellItem") == "Y"]
    if args.search:
        k = args.search.lower()
        rows = [r for r in rows if k in (r.get("ItemName") or "").lower() or k in (r.get("ItemCode") or "").lower()]
    rows.sort(key=lambda r: r["ItemCode"])
    _out(args, rows, ["ItemCode", "ItemName", "ItemType", "validFor"])


def cmd_projects(args):
    rows = q("MD_PRJ")
    if args.search:
        k = args.search.lower()
        rows = [r for r in rows if k in (r.get("PrjName") or "").lower() or k in (r.get("PrjCode") or "").lower()]
    rows.sort(key=lambda r: r["PrjCode"])
    for r in rows:
        r["ValidFrom"] = str(r.get("ValidFrom") or "")[:10]
        r["ValidTo"] = str(r.get("ValidTo") or "")[:10]
    _out(args, rows, ["PrjCode", "PrjName", "Active", "ValidFrom", "ValidTo"])


def cmd_customer(args):
    rows = q("MD_BP")
    if args.vat:
        v = args.vat.upper().replace("EL", "")
        rows = [r for r in rows if (r.get("LicTradNum") or "").upper().replace("EL", "") == v]
    elif args.name:
        k = args.name.lower()
        rows = [r for r in rows if k in (r.get("CardName") or "").lower()]
    elif args.code:
        rows = [r for r in rows if r.get("CardCode") == args.code]
    else:
        _die("give --vat, --name or --code")
    out = []
    for r in rows:
        out.append({"CardCode": r["CardCode"], "CardName": (r.get("CardName") or "")[:40],
                    "Type": r.get("CardType"), "VAT": r.get("LicTradNum") or "",
                    "Balance": _eur(r.get("Balance") or 0), "Email": r.get("E_Mail") or "",
                    "Phone": r.get("Phone1") or r.get("Cellular") or ""})
    _out(args, out, ["CardCode", "CardName", "Type", "VAT", "Balance", "Email", "Phone"])


def _inv_rows(invs):
    rows = []
    for i in invs:
        open_amt = float(i.get("DocTotal") or 0) - float(i.get("PaidToDate") or 0)
        rows.append({
            "DocNum": i.get("DocNum"), "Date": str(i.get("DocDate"))[:10], "Due": str(i.get("DocDueDate"))[:10],
            "CardCode": i.get("CardCode"), "CardName": (i.get("CardName") or "")[:34],
            "Total": _eur(i.get("DocTotal")), "Open": _eur(open_amt), "OpenNum": open_amt,
            "Status": "Open" if i.get("DocStatus") == "O" else "Closed",
            "Project": i.get("Project") or "", "Comments": (i.get("Comments") or "")[:60],
            "DocEntry": i.get("DocEntry"),
        })
    return rows


def cmd_invoices(args):
    f, t = _resolve_period(args.period, args.from_, args.to)
    invs = [i for i in q("MD_INV") if _in_range(i.get("DocDate"), f, t)]
    if args.customer:
        invs = [i for i in invs if i.get("CardCode") == args.customer]
    invs.sort(key=lambda i: (str(i["DocDate"]), i["DocNum"]), reverse=True)
    rows = _inv_rows(invs)
    _out(args, rows, ["DocNum", "Date", "CardCode", "CardName", "Total", "Open", "Status", "Comments"])
    if not args.json:
        print(f"\n{len(rows)} invoices, total {_eur(sum(float(i['DocTotal'] or 0) for i in invs))}, "
              f"open {_eur(sum(r['OpenNum'] for r in rows))}")


def cmd_open_ar(args):
    invs = [i for i in q("MD_INV") if i.get("DocStatus") == "O"]
    if args.customer:
        invs = [i for i in invs if i.get("CardCode") == args.customer]
    if args.project:
        invs = [i for i in invs if (i.get("Project") or "") == args.project]
    invs.sort(key=lambda i: str(i.get("DocDueDate")))
    rows = _inv_rows(invs)
    if args.by == "customer":
        agg = defaultdict(lambda: {"n": 0, "open": 0.0})
        for r in rows:
            k = f"{r['CardCode']} {r['CardName']}"
            agg[k]["n"] += 1
            agg[k]["open"] += r["OpenNum"]
        out = [{"Customer": k, "Invoices": v["n"], "Open": _eur(v["open"])}
               for k, v in sorted(agg.items(), key=lambda kv: -kv[1]["open"])]
        _out(args, out, ["Customer", "Invoices", "Open"])
    else:
        _out(args, rows, ["DocNum", "Date", "Due", "CardCode", "CardName", "Open", "Project"])
    if not args.json:
        print(f"\n{len(rows)} open invoices, open total {_eur(sum(r['OpenNum'] for r in rows))}")


def cmd_overdue(args):
    cutoff = date.today() - timedelta(days=args.days)
    invs = [i for i in q("MD_INV") if i.get("DocStatus") == "O" and _d(i.get("DocDueDate")) and _d(i.get("DocDueDate")) < cutoff]
    invs.sort(key=lambda i: str(i.get("DocDueDate")))
    rows = _inv_rows(invs)
    for r in rows:
        r["DaysLate"] = (date.today() - _d(r["Due"])).days
    _out(args, rows, ["DocNum", "Due", "DaysLate", "CardCode", "CardName", "Open"])
    if not args.json:
        print(f"\n{len(rows)} overdue > {args.days} days, {_eur(sum(r['OpenNum'] for r in rows))}")


def cmd_payments(args):
    f, t = _resolve_period(args.period, args.from_, args.to)
    pays = q("MD_RCT" if args.direction == "in" else "MD_VPM")
    pays = [p for p in pays if _in_range(p.get("DocDate"), f, t)]
    if args.customer:
        pays = [p for p in pays if p.get("CardCode") == args.customer]
    pays.sort(key=lambda p: str(p["DocDate"]), reverse=True)
    rows = []
    for p in pays:
        amt = float(p.get("DocTotal") or 0) or sum(float(p.get(k) or 0) for k in ("CashSum", "TrsfrSum", "CheckSum", "CreditSum"))
        rows.append({"DocNum": p["DocNum"], "Date": str(p["DocDate"])[:10], "CardCode": p.get("CardCode"),
                     "CardName": (p.get("CardName") or "")[:34], "Amount": _eur(amt), "_a": amt,
                     "Remarks": (p.get("Comments") or "")[:50]})
    _out(args, rows, ["DocNum", "Date", "CardCode", "CardName", "Amount", "Remarks"])
    if not args.json:
        print(f"\n{len(rows)} payments, {_eur(sum(r['_a'] for r in rows))}")


def cmd_analytics(args):
    f, t = _resolve_period(args.period, args.from_, args.to)
    invs = [i for i in q("MD_INV") if _in_range(i.get("DocDate"), f, t)]
    creds = [c for c in q("MD_CN") if _in_range(c.get("DocDate"), f, t)]
    il = [l for l in q("MD_INV_LINES") if _in_range(l.get("DocDate"), f, t)]
    cl = [l for l in q("MD_CN_LINES") if _in_range(l.get("DocDate"), f, t)]
    gross = sum(float(i["DocTotal"] or 0) for i in invs) - sum(float(c["DocTotal"] or 0) for c in creds)
    vat = sum(float(i.get("VatSum") or 0) for i in invs) - sum(float(c.get("VatSum") or 0) for c in creds)
    print(f"Period {f} .. {t}")
    print(f"Invoices {len(invs)}  Credit notes {len(creds)}")
    print(f"Gross {_eur(gross)}   Net {_eur(gross - vat)}   VAT {_eur(vat)}\n")

    def group(key_fn, title):
        agg = defaultdict(float)
        for l in il:
            agg[key_fn(l)] += float(l.get("LineTotal") or 0)
        for l in cl:
            agg[key_fn(l)] -= float(l.get("LineTotal") or 0)
        rows = [{"Key": k, "Net": _eur(v)} for k, v in sorted(agg.items(), key=lambda kv: -kv[1])]
        print(title)
        _table(rows[: args.top], ["Key", "Net"])
        print()

    group(lambda l: f"{l.get('ItemCode')} {(l.get('Dscription') or '')[:45]}", "By item (net)")
    group(lambda l: l.get("Project") or "(no project)", "By project (net)")
    group(lambda l: f"{l.get('CardCode')} {(l.get('CardName') or '')[:40]}", "By customer (net)")
    group(lambda l: str(l.get("DocDate"))[:7], "By month (net)")


def cmd_sqlquery(args):
    sl = SL()
    if args.list:
        rows = sl.get_all("SQLQueries", **{"$select": "SqlCode,SqlName,SqlText"})
        for r in rows:
            r["SqlText"] = (r.get("SqlText") or "")[:70]
        _out(args, rows, ["SqlCode", "SqlName", "SqlText"])
        return
    if not args.code:
        _die("give --list or --code")
    rows = q(args.code, sl)
    if args.json or not rows:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
    else:
        _table(rows, list(rows[0].keys()))


def cmd_invoice(args):
    sl = SL()
    if args.docnum:
        hit = [i for i in q("MD_INV", sl) if int(i["DocNum"]) == args.docnum]
        if not hit:
            _die("not found")
        entry = hit[0]["DocEntry"]
    else:
        entry = args.entry
    print(json.dumps(sl.get(f"Invoices({entry})"), ensure_ascii=False, indent=1))


# ---------------------------------------------------------- write commands ---

def _guard_write(args):
    if not WRITE_ENABLED:
        _die("Write commands are disabled. Set WRITE_ENABLED=1 in .env to allow them.")
    if not getattr(args, "yes", False):
        _die("Refusing to post without --yes. Show the preview to the user first.")


def _resolve_item(key):
    it = CONFIG.get("items", {}).get(key)
    if it:
        return it
    for k, v in CONFIG.get("items", {}).items():
        if key.lower() in [x.lower() for x in v.get("keywords", [])] or key == v.get("code"):
            return v
    return {"code": key, "name": key}


def _preview_invoice(body, item, customer):
    print("PREVIEW Invoice")
    print(f"  Customer : {customer.get('CardCode')} {customer.get('CardName')}  VAT {customer.get('FederalTaxID')}")
    print(f"  Series   : {body.get('Series')}")
    print(f"  Date     : {body['DocDate']}  Due {body['DocDueDate']}")
    print(f"  Comments : {body.get('Comments')}")
    for l in body["DocumentLines"]:
        print(f"  Line     : {l['ItemCode']} {item.get('name','')} x{l['Quantity']} @ {_eur(l['UnitPrice'])}"
              f"  project {l.get('ProjectCode','')}")
    net = sum(float(l["UnitPrice"]) * float(l["Quantity"]) for l in body["DocumentLines"])
    rate = CONFIG.get("vat_rate", 0.24)
    print(f"  Net {_eur(net)}  VAT {_eur(net*rate)}  Gross {_eur(net*(1+rate))}")


def cmd_issue(args):
    sl = SL()
    r = sl._req("GET", f"BusinessPartners('{args.customer}')?$select=CardCode,CardName,FederalTaxID,PayTermsGrpCode")
    if r.status_code != 200:
        _die(f"customer {args.customer} not found")
    cust = r.json()
    item = _resolve_item(args.item)
    series = args.series or CONFIG.get("default_series")
    today = date.today()
    due = datetime.strptime(args.due, "%Y-%m-%d").date() if args.due else today + timedelta(days=CONFIG.get("default_due_days", 30))
    unit = float(args.net) / float(args.qty)
    line = {"ItemCode": item["code"], "Quantity": args.qty, "UnitPrice": round(unit, 2)}
    if args.project:
        line["ProjectCode"] = args.project
    body = {
        "CardCode": cust["CardCode"], "DocDate": _iso(today), "DocDueDate": _iso(due),
        "Comments": args.comments or "", "DocumentLines": [line],
    }
    if series:
        body["Series"] = int(series)
    for kv in args.udf or []:
        k, v = kv.split("=", 1)
        body[k] = v
    for kv in CONFIG.get("issue_defaults", {}).items():
        body.setdefault(*kv)
    _preview_invoice(body, item, cust)
    if args.dry_run:
        print("\n(dry run, nothing posted)")
        return
    _guard_write(args)
    d = sl.post("Invoices", body)
    print(f"\nPOSTED  DocNum {d.get('DocNum')}  DocEntry {d.get('DocEntry')}  Total {_eur(d.get('DocTotal'))}")


def cmd_credit_note(args):
    sl = SL()
    hit = [i for i in q("MD_INV", sl) if int(i["DocNum"]) == args.docnum]
    if not hit:
        _die("invoice not found")
    inv = sl.get(f"Invoices({hit[0]['DocEntry']})")
    lines = []
    for l in inv["DocumentLines"]:
        lines.append({"BaseType": 13, "BaseEntry": inv["DocEntry"], "BaseLine": l["LineNum"]})
    body = {"CardCode": inv["CardCode"], "DocDate": _iso(date.today()), "DocDueDate": _iso(date.today()),
            "Comments": args.comments or f"Πιστωτικό για τιμολόγιο {inv['DocNum']}", "DocumentLines": lines}
    series = args.series or CONFIG.get("credit_series")
    if series:
        body["Series"] = int(series)
    print(f"PREVIEW Credit note for invoice {inv['DocNum']} {inv['CardCode']} {inv['CardName']} "
          f"total {_eur(inv['DocTotal'])}, {len(lines)} lines")
    if args.dry_run:
        print("(dry run)")
        return
    _guard_write(args)
    d = sl.post("CreditNotes", body)
    print(f"POSTED  CreditNote DocNum {d.get('DocNum')} DocEntry {d.get('DocEntry')}")


def cmd_activity(args):
    sl = SL()
    body = {"CardCode": args.customer, "Notes": args.notes, "ActivityType": "cn_Meeting" if args.type == "meeting"
            else "cn_PhoneCall" if args.type == "call" else "cn_Note",
            "Subject": None, "ActivityDate": _iso(date.today())}
    print(f"PREVIEW Activity {args.type} for {args.customer}: {args.notes[:80]}")
    if args.dry_run:
        return
    _guard_write(args)
    d = sl.post("Activities", body)
    print(f"POSTED Activity {d.get('ActivityCode')}")


# ------------------------------------------------------------------ parser ---

def build_parser():
    p = argparse.ArgumentParser(description="SAP B1 Service Layer client")
    p.add_argument("--json", action="store_true", help="JSON output")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("test").set_defaults(fn=cmd_test)
    sub.add_parser("whoami").set_defaults(fn=cmd_whoami)
    sub.add_parser("setup-queries", help="install/refresh the SQL queries the read commands use").set_defaults(fn=cmd_setup_queries)
    s = sub.add_parser("raw"); s.add_argument("path"); s.add_argument("--chars", type=int, default=1500)
    s.set_defaults(fn=cmd_raw)

    s = sub.add_parser("series", help="document series (13=invoice, 14=credit note, 17=order)")
    s.add_argument("--object", type=int, default=13)
    s.set_defaults(fn=cmd_series)

    s = sub.add_parser("items"); s.add_argument("--search"); s.set_defaults(fn=cmd_items)
    s = sub.add_parser("projects"); s.add_argument("--search"); s.set_defaults(fn=cmd_projects)

    s = sub.add_parser("customer")
    s.add_argument("--vat"); s.add_argument("--name"); s.add_argument("--code")
    s.set_defaults(fn=cmd_customer)

    def period_args(s):
        s.add_argument("--period"); s.add_argument("--from", dest="from_"); s.add_argument("--to")

    s = sub.add_parser("invoices"); period_args(s); s.add_argument("--customer"); s.set_defaults(fn=cmd_invoices)

    s = sub.add_parser("open-ar")
    s.add_argument("--customer"); s.add_argument("--project")
    s.add_argument("--by", choices=["invoice", "customer"], default="invoice")
    s.set_defaults(fn=cmd_open_ar)

    s = sub.add_parser("overdue"); s.add_argument("--days", type=int, default=0); s.set_defaults(fn=cmd_overdue)

    s = sub.add_parser("payments"); period_args(s)
    s.add_argument("--direction", choices=["in", "out"], default="in"); s.add_argument("--customer")
    s.set_defaults(fn=cmd_payments)

    s = sub.add_parser("analytics"); period_args(s); s.add_argument("--top", type=int, default=15)
    s.set_defaults(fn=cmd_analytics)

    s = sub.add_parser("sqlquery"); s.add_argument("--list", action="store_true"); s.add_argument("--code")
    s.set_defaults(fn=cmd_sqlquery)

    s = sub.add_parser("invoice"); s.add_argument("--docnum", type=int); s.add_argument("--entry", type=int)
    s.set_defaults(fn=cmd_invoice)

    s = sub.add_parser("issue", help="create A/R invoice")
    s.add_argument("--customer", required=True, help="CardCode")
    s.add_argument("--item", required=True, help="config key, keyword or ItemCode")
    s.add_argument("--net", required=True, help="net total before VAT")
    s.add_argument("--qty", type=float, default=1)
    s.add_argument("--project"); s.add_argument("--comments"); s.add_argument("--series"); s.add_argument("--due")
    s.add_argument("--udf", action="append", help="U_Field=value, repeatable")
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_issue)

    s = sub.add_parser("credit-note")
    s.add_argument("--docnum", type=int, required=True); s.add_argument("--comments"); s.add_argument("--series")
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_credit_note)

    s = sub.add_parser("activity")
    s.add_argument("--customer", required=True); s.add_argument("--notes", required=True)
    s.add_argument("--type", choices=["note", "call", "meeting"], default="note")
    s.add_argument("--dry-run", action="store_true"); s.add_argument("--yes", action="store_true")
    s.set_defaults(fn=cmd_activity)
    return p


if __name__ == "__main__":
    a = build_parser().parse_args()
    a.fn(a)
