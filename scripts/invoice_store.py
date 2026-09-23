#!/usr/bin/env python3
"""
invoice_store.py  -  one JSON per invoice, the single source of truth.

Layout:  <skill>/data/invoices/<year>/<id>.json   (+ the PDF next to it)
Status:  draft -> previewed -> issued (has MARK) -> synced (has SAP DocEntry)

Also keeps <skill>/data/customers.json  {vat: {...}}  merged from vat_index.json
(SAP export) and whatever timologio / SAP told us later.
"""
import json, os, re, sys, datetime as dt
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent
INV_DIR = SKILL_DIR / "data" / "invoices"
CUST_FILE = SKILL_DIR / "data" / "customers.json"
VAT_INDEX = SKILL_DIR / "data" / "vat_index.json"  # optional ERP export {vat: {code, name}}
CONFIG_FILE = SKILL_DIR / "config.json"

def ISSUER():
    """Issuer block from config.json ("issuer"); the company that signs the invoices."""
    return config().get("issuer", {"vat": "", "name": "", "branch": 0})

PAYMENT = {"credit": {"timologio": "5", "label": "Επί πιστώσει", "sap": ""},
           "bank":   {"timologio": "1", "label": "Επαγ. Λογαριασμός Πληρωμών Ημεδαπής", "sap": ""},
           "cash":   {"timologio": "3", "label": "Μετρητά", "sap": ""},
           "cheque": {"timologio": "4", "label": "Επιταγή", "sap": ""}}


def _now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def config():
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8")) if CONFIG_FILE.exists() else {}


# ------------------------------------------------------------ customers ---

def load_customers():
    c = {}
    if VAT_INDEX.exists():
        for vat, v in json.loads(VAT_INDEX.read_text(encoding="utf-8")).items():
            if re.fullmatch(r"\d{9}", vat):
                c[vat] = {"vat": vat, "name": v.get("name"), "sap_cardcode": v.get("code")}
    if CUST_FILE.exists():
        for vat, v in json.loads(CUST_FILE.read_text(encoding="utf-8")).items():
            c.setdefault(vat, {"vat": vat}).update({k: x for k, x in v.items() if x not in (None, "")})
    return c


def save_customer(vat, **fields):
    data = json.loads(CUST_FILE.read_text(encoding="utf-8")) if CUST_FILE.exists() else {}
    data.setdefault(vat, {"vat": vat}).update({k: v for k, v in fields.items() if v not in (None, "")})
    CUST_FILE.parent.mkdir(parents=True, exist_ok=True)
    CUST_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return data[vat]


def customer(vat):
    return load_customers().get(vat, {"vat": vat})


# ------------------------------------------------------------- invoices ---

def find(inv_id):
    for p in INV_DIR.glob(f"*/{inv_id}.json"):
        return p
    return None


def load(inv_id):
    p = find(inv_id)
    if not p:
        sys.exit(f"invoice {inv_id} not found under {INV_DIR}")
    return json.loads(p.read_text(encoding="utf-8"))


def save(inv):
    year = inv["issue_date"][:4]
    p = INV_DIR / year / f"{inv['id']}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(inv, ensure_ascii=False, indent=1), encoding="utf-8")
    return p


def rename(inv, new_id):
    old = find(inv["id"])
    inv["id"] = new_id
    if old:
        new_path = old.with_name(f"{new_id}.json")
        old.replace(new_path)  # rename in place (deleting is not allowed in the synced folder)
    return save(inv)


def audit(inv, step, **extra):
    inv.setdefault("audit", []).append({"at": _now(), "step": step, **extra})


def all_invoices():
    out = []
    for p in sorted(INV_DIR.glob("*/*.json")):
        if p.parent.name.startswith("_"):
            continue
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            pass
    return out


def new_draft(vat, item_key, net, descr=None, issue_date=None, payment="credit",
              project=None, notes="", due_days=None, quantity=1):
    cfg = config()
    items = cfg.get("items", {})
    if item_key not in items:
        sys.exit(f"unknown item '{item_key}'. Known: {', '.join(items)}")
    it = items[item_key]
    cust = customer(vat)
    issue_date = issue_date or dt.date.today().isoformat()
    due_days = cfg.get("default_due_days", 30) if due_days is None else due_days
    due = (dt.date.fromisoformat(issue_date) + dt.timedelta(days=due_days)).isoformat()
    vat_rate = it.get("vat_rate", 24)
    net = round(float(net), 2)
    vat_amt = round(net * vat_rate / 100, 2)
    gross = round(net + vat_amt, 2)
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    inv = {
        "schema": "mydata.invoice/1",
        "id": f"DRAFT-{stamp}",
        "status": "draft",
        "type": it.get("mydata_type", "2.1"),
        "series": cfg.get("timologio", {}).get("series", "MYDATA"),
        "number": None,
        "issue_date": issue_date,
        "currency": "EUR",
        "issuer": ISSUER(),
        "customer": {
            "vat": vat, "name": cust.get("name", ""), "street": cust.get("street", ""),
            "city": cust.get("city", ""), "postal_code": cust.get("postal_code", ""),
            "country": "GR", "doy": cust.get("doy", ""), "email": cust.get("email", ""),
            "refs": {"sap_cardcode": cust.get("sap_cardcode"), "timologio_code": cust.get("timologio_code")},
        },
        "payment": {"method": payment, "label": PAYMENT[payment]["label"], "due_date": due},
        "lines": [{
            "no": 1,
            "item_key": item_key,
            "description": descr or it.get("name", item_key),
            "quantity": quantity, "unit_price": round(net / quantity, 2),
            "net": net, "vat_rate": vat_rate, "vat": vat_amt, "gross": gross,
            "classification": it.get("classification", {"category": "category1_3", "code": "E3_561_001"}),
            "refs": {"sap_itemcode": it.get("code"), "sap_account": it.get("account"),
                     "sap_project": project or cust.get("sap_project"),
                     "timologio_item": it.get("timologio_item")},
        }],
        "totals": {"net": net, "vat": vat_amt, "withheld": 0, "gross": gross},
        "notes": notes,
        "mydata": {"mark": None, "uid": None, "transmitted_at": None, "channel": "timologio"},
        "pdf": None,
        "sync": {"erp": {"adapter": cfg.get("erp", {}).get("adapter"), "docentry": None, "docnum": None, "series": cfg.get("erp", {}).get("external_series"), "synced_at": None},
                 "email": {"sent_at": None, "to": []}},
        "audit": [],
    }
    audit(inv, "draft", by=os.environ.get("USER", "user"))
    return inv


# ------------------------------------------------------------- adapters ---

def to_timologio_spec(inv):
    """What the browser client needs to fill the timologio form."""
    l = inv["lines"][0]
    return {
        "invoiceType": {"2.1": "20", "1.1": "1"}[inv["type"]],
        "customerVat": inv["customer"]["vat"],
        "customerName": inv["customer"]["name"],
        "customerEmail": inv["customer"]["email"],
        "paymentType": PAYMENT[inv["payment"]["method"]]["timologio"],
        "lines": [{"item": str(l["refs"].get("timologio_item") or ""), "quantity": l["quantity"],
                   "unitPrice": l["unit_price"], "classification": l["classification"]} for l in inv["lines"]],
        "notes": inv.get("notes", ""),
        "expect": {"net": inv["totals"]["net"], "vat": inv["totals"]["vat"], "gross": inv["totals"]["gross"]},
    }


def mydata_summary(inv):
    c = inv["customer"]
    t = inv["totals"]
    return (f"{inv['id']}  {inv['type']}  {inv['issue_date']}  {c['name']} ({c['vat']})  "
            f"net {t['net']:.2f}  vat {t['vat']:.2f}  gross {t['gross']:.2f}  "
            f"MARK {inv['mydata']['mark'] or '-'}  ERP {inv['sync']['erp']['docnum'] or '-'}  [{inv['status']}]")
