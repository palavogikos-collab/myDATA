#!/usr/bin/env python3
"""
adapters/softone.py  -  record an issued invoice (from invoice_store) in SoftOne (Soft1).

Uses the Soft1 Web Services (s1services) JSON API:
  POST /s1services/list/customer   filters "AFM=<vat>"       customer lookup by ΑΦΜ
  POST /s1services/set/customer                              create customer
  POST /s1services/list/service    filters "CODE=<code>"     resolve the service item (MTRL)
  POST /s1services/set/saldoc      SALDOC + SRVLINES         create the sales document
  POST /s1services/get/saldoc                                read it back (FINCODE, MARK fields)

It never calls /s1services/einvoice: the invoice was issued and transmitted by timologio.
The document goes into a series configured in Soft1 as "issued by a third party / not
transmitted" and carries the MARK in the field named by config (default SOMYDATAMARK).

Credentials in .env:  S1_URL (https://go.s1cloud.net), S1_CODE (s1code), S1_APPID, S1_TOKEN
config.json "erp": {
  "adapter": "softone",
  "external_series": "7099",        series id for externally issued invoices
  "payment_code": "400",            Soft1 payment code for "επί πιστώσει"
  "mark_field": "SOMYDATAMARK",     SALDOC field that stores the MARK
  "extra_saldoc": {}                any other SALDOC fields your setup requires
}
config.json "items": { "<key>": { "softone_code": "<CODE in Soft1>", "softone_kind": "service" | "item",
                                    "softone_vat": "<Soft1 VAT id>" } }
  kind "service" writes SRVLINES (services), kind "item" writes ITELINES (goods).

STATUS: drafted from a working Soft1 issuing client, not yet tested end to end for the
"external MARK" case. Questions 2 and 3 of docs/adapters.md (series behaviour, MARK field)
need confirming on a real installation. Run with --dry-run first.
"""
import os, sys, json
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import invoice_store as store  # noqa: E402


# ------------------------------------------------------------------ config ---

def _env():
    env = {}
    f = store.SKILL_DIR / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    env.update({k: v for k, v in os.environ.items() if k.startswith("S1_")})
    return env


ENV = _env()
BASE_URL = ENV.get("S1_URL", "https://go.s1cloud.net").rstrip("/")


def _cfg():
    return store.config().get("erp", {})


def _payload(**kw):
    p = {"appId": ENV.get("S1_APPID", ""), "token": ENV.get("S1_TOKEN", "")}
    p.update(kw)
    return p


def _post(endpoint, payload, timeout=20):
    if not ENV.get("S1_CODE") or not ENV.get("S1_APPID"):
        sys.exit("S1_CODE / S1_APPID / S1_TOKEN missing in .env")
    r = requests.post(f"{BASE_URL}{endpoint}", json=payload,
                      headers={"s1code": ENV["S1_CODE"], "Content-Type": "application/json"}, timeout=timeout)
    r.raise_for_status()
    return r.json()


def _idx(fields, *names):
    for i, f in enumerate(fields or []):
        if str(f.get("name", "")).upper() in [n.upper() for n in names]:
            return i
    return -1


def _row_id(row):
    raw = str(row[0])
    return raw.split(";")[1] if ";" in raw else raw


# --------------------------------------------------------------- customers ---

def find_customer(vat):
    """Soft1 wants AFM=123456789 without quotes; with quotes it silently returns nothing."""
    d = _post("/s1services/list/customer", _payload(filters=f"AFM={vat}"))
    if d.get("success") and d.get("totalcount", 0) > 0 and d.get("rows"):
        return _row_id(d["rows"][0])
    return None


def create_customer(c):
    body = {"AFM": c["vat"], "NAME": c["name"], "EMAIL": c.get("email") or "", "ISACTIVE": "1"}
    if c.get("street"):
        body["ADDRESS"] = c["street"]
    if c.get("city"):
        body["CITY"] = c["city"]
    if c.get("postal_code"):
        body["ZIP"] = c["postal_code"]
    d = _post("/s1services/set/customer", _payload(locateinfo="CUSTOMER:" + ",".join(body), key="", data={"CUSTOMER": [body]}))
    if not d.get("success"):
        sys.exit(f"Soft1 customer creation failed: {d.get('error')}")
    return str(d.get("id"))


def ensure_customer(inv, dry=False):
    c = inv["customer"]
    trdr = c["refs"].get("softone_trdr") or find_customer(c["vat"])
    if trdr:
        c["refs"]["softone_trdr"] = str(trdr)
        return str(trdr), False
    if dry:
        print("PREVIEW Soft1 customer:", json.dumps({k: c.get(k) for k in ("vat", "name", "street", "city", "postal_code", "email")}, ensure_ascii=False))
        return "NEW", True
    trdr = create_customer(c)
    c["refs"]["softone_trdr"] = trdr
    store.save_customer(c["vat"], softone_trdr=trdr, name=c["name"])
    store.audit(inv, "softone_customer_created", trdr=trdr)
    return trdr, True


# ------------------------------------------------------------ items/services ---

def resolve_mtrl(code, kind):
    endpoint = "/s1services/list/item" if kind == "item" else "/s1services/list/service"
    d = _post(endpoint, _payload(filters=f"CODE='{code}'"))
    if d.get("success") and d.get("rows"):
        return _row_id(d["rows"][0])
    sys.exit(f"Soft1 {kind} with CODE {code} not found (config items.<key>.softone_code)")


# ---------------------------------------------------------------- document ---

def document_body(inv, trdr, mtrl_by_line):
    cfg = _cfg()
    items = store.config().get("items", {})
    mark_field = cfg.get("mark_field", "SOMYDATAMARK")
    saldoc = {
        "SERIES": str(cfg.get("external_series", "")),
        "TRDR": str(trdr),
        "PAYMENT": str(cfg.get("payment_code", "")),
        "TRNDATE": inv["issue_date"],
        "FINALDATE": inv["payment"]["due_date"],
        "COMMENTS": f"timologio {inv['series']}/{inv['number']} MARK {inv['mydata']['mark']}",
        mark_field: str(inv["mydata"]["mark"]),
    }
    saldoc.update(cfg.get("extra_saldoc", {}))
    body = {"SALDOC": [saldoc]}
    for l in inv["lines"]:
        it = items.get(l["item_key"], {})
        key = "ITELINES" if it.get("softone_kind") == "item" else "SRVLINES"
        vat = it.get("softone_vat") or cfg.get("vat_code")
        if not vat:
            sys.exit(f"config items.{l['item_key']}.softone_vat (or erp.vat_code) is required: the Soft1 VAT id for {l['vat_rate']}%")
        body.setdefault(key, []).append({"LINENUM": l["no"], "MTRL": mtrl_by_line[l["no"]],
                                         "QTY1": l["quantity"], "PRICE": l["unit_price"],
                                         "VAT": str(vat), "COMMENTS": l["description"]})
    return body


def post_invoice(inv, dry=False):
    if inv["sync"]["erp"].get("docentry"):
        print(f"already in Soft1: SALDOC {inv['sync']['erp']['docentry']} / {inv['sync']['erp']['docnum']}")
        return inv
    if not inv["mydata"]["mark"]:
        sys.exit("invoice has no MARK yet; issue it in timologio first")
    cfg = _cfg()
    if not cfg.get("external_series"):
        sys.exit('config.json erp.external_series is required (Soft1 series for externally issued invoices)')
    trdr, _ = ensure_customer(inv, dry=dry)
    items = store.config().get("items", {})
    mtrl = {}
    for l in inv["lines"]:
        it = items.get(l["item_key"], {})
        if not it.get("softone_code"):
            sys.exit(f"config items.{l['item_key']}.softone_code missing")
        mtrl[l["no"]] = "DRY" if dry else resolve_mtrl(it["softone_code"], it.get("softone_kind", "service"))
    body = document_body(inv, trdr, mtrl)
    if dry:
        print("PREVIEW Soft1 SALDOC:", json.dumps(body, ensure_ascii=False, indent=1))
        return inv
    locate = ";".join(f"{k}:" + ",".join(v[0]) for k, v in body.items())
    d = _post("/s1services/set/saldoc", _payload(locateinfo=locate, key="", data=body))
    if not d.get("success"):
        sys.exit(f"Soft1 saldoc creation failed: {d.get('error')}")
    saldoc_id = str(d.get("id"))
    # read back the document number and confirm the MARK stuck
    g = _post("/s1services/get/saldoc", _payload(key=saldoc_id))
    fincode, mark_back = None, None
    try:
        row = (g.get("data") or {}).get("SALDOC", [{}])[0]
        fincode = row.get("FINCODE")
        mark_back = row.get(cfg.get("mark_field", "SOMYDATAMARK"))
    except Exception:
        pass
    inv["sync"]["erp"].update(adapter="softone", docentry=saldoc_id, docnum=fincode, series=cfg["external_series"],
                              synced_at=store._now(), mark_back=mark_back)
    inv["status"] = "synced"
    store.audit(inv, "softone_saldoc", saldoc=saldoc_id, fincode=fincode)
    store.save(inv)
    print(f"Soft1: SALDOC {saldoc_id}  {fincode or ''}  MARK stored: {mark_back or 'not returned, verify in Soft1'}")
    return inv


def verify_single_transmission(inv):
    try:
        import importlib.util
        s = importlib.util.spec_from_file_location("mydata_api", HERE.parent / "scripts" / "mydata_api.py")
        md = importlib.util.module_from_spec(s); s.loader.exec_module(md)
    except Exception as e:
        print("myDATA check skipped:", e); return
    d = inv["issue_date"]
    print(f"myDATA check: run  python3 scripts/mydata_api.py transmitted --date-from {d} --date-to {d}  and expect exactly one document for {inv['customer']['vat']}")
