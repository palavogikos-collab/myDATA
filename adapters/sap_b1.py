#!/usr/bin/env python3
"""
adapters/sap_b1.py  -  push an issued invoice (from invoice_store) into SAP Business One.

  ensure_bp(inv)      creates the business partner if the VAT is unknown to SAP
  post_invoice(inv)   creates the A/R invoice in the external series (ΤΠΥ-Χ, 83)
                      with U_AADE=N and the timologio MARK, so the Hellenization
                      add-on does not transmit it again.

Needs WRITE_ENABLED=1 (env var or .env).  Never posts twice: if the invoice
already has sync.sap_b1.docentry it stops.
"""
import importlib.util, json, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "scripts"))
import invoice_store as store  # noqa: E402

_spec = importlib.util.spec_from_file_location("sap_b1_api", HERE / "sap_b1_api.py")
sap = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sap)

def BP_TEMPLATE():
    """Business partner defaults: copy them from an existing customer of yours into config.json
    ("erp": {"bp_defaults": {...}}). GroupCode, PayTermsGrpCode, DebitorAccount, VatGroup, UDFs..."""
    base = {"CardType": "cCustomer", "Currency": "EUR", "VatLiable": "vLiable", "Country": "EL",
            "PriceListNum": 1, "CompanyPrivate": "cCompany"}
    base.update(store.config().get("erp", {}).get("bp_defaults", {}))
    return base


def _sl():
    return sap.SL()


def _ok(r):
    return r.status_code in (200, 201, 204)


def _err(r):
    try:
        return r.json()["error"]["message"]["value"]
    except Exception:
        return r.text[:300]


def find_bp_by_vat(sl, vat):
    """Key access only (lists are hidden by Data Ownership)."""
    cust = store.customer(vat)
    code = cust.get("sap_cardcode")
    if code:
        r = sl._req("GET", f"BusinessPartners('{code}')?$select=CardCode,CardName,FederalTaxID")
        if r.status_code == 200:
            return r.json()
    return None


def next_cardcode(sl):
    r = sl._req("POST", "SeriesService_GetDocumentSeries",
                json={"DocumentTypeParams": {"Document": "2", "DocumentSubType": "C"}})
    for s in r.json().get("value", []):
        if s.get("Prefix") == "C." and s.get("NextNumber"):
            n = int(s["NextNumber"])
            while True:  # skip numbers that exist (the counter does not advance for API inserts)
                code = f"C.{n:06d}"
                if sl._req("GET", f"BusinessPartners('{code}')?$select=CardCode").status_code == 404:
                    return code
                n += 1
    sys.exit("no C. series found for business partners")


def ensure_bp(inv, sl=None, dry=False):
    sl = sl or _sl()
    c = inv["customer"]
    bp = find_bp_by_vat(sl, c["vat"])
    if bp:
        c["refs"]["sap_cardcode"] = bp["CardCode"]
        return bp["CardCode"], False
    code = next_cardcode(sl)
    street, no = c.get("street", ""), ""
    parts = street.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].isdigit():
        street, no = parts
    body = dict(BP_TEMPLATE(), CardCode=code, CardName=c["name"], FederalTaxID=f"EL{c['vat']}",
                EmailAddress=c.get("email") or None,
                BPAddresses=[{"AddressName": "ΕΔΡΑ", "Street": street, "StreetNo": no, "ZipCode": c.get("postal_code", ""),
                              "City": c.get("city", ""), "Country": "EL", "AddressType": "bo_BillTo"}],
                BilltoDefault="ΕΔΡΑ", ShipToDefault="ΕΔΡΑ")
    if dry:
        print("PREVIEW BusinessPartner:", json.dumps(body, ensure_ascii=False, indent=1))
        return code, True
    r = sl._req("POST", "BusinessPartners", json=body)
    if not _ok(r):
        sys.exit(f"SAP BusinessPartners POST failed: {_err(r)}")
    c["refs"]["sap_cardcode"] = code
    store.save_customer(c["vat"], sap_cardcode=code, name=c["name"])
    store.audit(inv, "sap_bp_created", cardcode=code)
    return code, True


def invoice_body(inv, cardcode, use_items=False):
    cfg = store.config().get("erp", {})
    mark = inv["mydata"]["mark"]
    lines = []
    for l in inv["lines"]:
        line = {"VatGroup": cfg.get("vat_group", "Φ7300-24"), "U_KPCategory": "BP"}
        if l["refs"].get("sap_project"):
            line["ProjectCode"] = l["refs"]["sap_project"]
        if use_items and l["refs"].get("sap_itemcode"):
            line.update(ItemCode=l["refs"]["sap_itemcode"], Quantity=l["quantity"], UnitPrice=l["unit_price"])
        else:
            line.update(ItemDescription=l["description"], AccountCode=l["refs"].get("sap_account") or cfg.get("service_account", "73.00.00.0000"),
                        LineTotal=l["net"])
        lines.append(line)
    body = {
        "CardCode": cardcode, "Series": inv["sync"]["erp"]["series"] or cfg.get("external_series"),
        "DocDate": inv["issue_date"], "DocDueDate": inv["payment"]["due_date"], "TaxDate": inv["issue_date"],
        "NumAtCard": f"timologio {inv['series']} {inv['number']}",
        "Comments": (inv.get("notes") or f"{inv['customer']['name'][:40]}_{inv['lines'][0]['description']}")[:200]
                    + f" (timologio {inv['series']}/{inv['number']}, MARK {mark})",
        "U_AADE": "N", "U_Transport": "Πώληση",
        "DocumentLines": lines,
    }
    if not use_items:
        body["DocType"] = "dDocument_Service"
    return body


def post_invoice(inv, sl=None, dry=False, use_items=None):
    if inv["sync"]["erp"].get("docentry"):
        print(f"already in SAP: DocEntry {inv['sync']['erp']['docentry']} / DocNum {inv['sync']['erp']['docnum']}")
        return inv
    if not inv["mydata"]["mark"]:
        sys.exit("invoice has no MARK yet; issue it in timologio first")
    sl = sl or _sl()
    cardcode, _ = ensure_bp(inv, sl, dry=dry)
    if use_items is None:
        use_items = store.config().get("erp", {}).get("items_readable", False)
    body = invoice_body(inv, cardcode, use_items=use_items)
    if dry:
        print("PREVIEW Invoice:", json.dumps(body, ensure_ascii=False, indent=1))
        return inv
    r = sl._req("POST", "Invoices", json=body)
    if not _ok(r):
        sys.exit(f"SAP Invoices POST failed: {_err(r)}")
    d = r.json()
    # UDFs with the MARK are dropped by the add-on on insert; set them afterwards
    p = sl._req("PATCH", f"Invoices({d['DocEntry']})",
                json={"U_Mark": inv["mydata"]["mark"], "U_MyDataType": inv["type"]})
    inv["sync"]["erp"].update(adapter="sap_b1", docentry=d["DocEntry"], docnum=d["DocNum"], synced_at=store._now(),
                                 mark_patch="ok" if _ok(p) else _err(p))
    inv["status"] = "synced"
    store.audit(inv, "sap_invoice", docentry=d["DocEntry"], docnum=d["DocNum"], total=d.get("DocTotal"))
    store.save(inv)
    print(f"SAP: DocEntry {d['DocEntry']}  DocNum {d['DocNum']}  total {d.get('DocTotal')}  (U_Mark patch: {inv['sync']['erp']['mark_patch']})")
    return inv


def verify_single_transmission(inv):
    """Ask myDATA how many docs carry this invoice's date/customer; warn if the add-on re-sent."""
    try:
        _s = importlib.util.spec_from_file_location("mydata_api", HERE.parent / "scripts" / "mydata_api.py")
        md = importlib.util.module_from_spec(_s); _s.loader.exec_module(md)
    except Exception as e:
        print("myDATA check skipped:", e); return
    d = inv["issue_date"]
    docs = md.transmitted_docs(d, d) if hasattr(md, "transmitted_docs") else None  # noqa
    if docs is None:
        print("myDATA check: run  python3 scripts/mydata_api.py transmitted --date-from", d, "--date-to", d)
        return
    same = [x for x in docs if x.get("counterpart") == inv["customer"]["vat"] and abs(float(x.get("net", 0)) - inv["totals"]["net"]) < 0.01]
    print(f"myDATA docs for {inv['customer']['vat']} on {d}: {len(same)} (expected 1)")
    if len(same) > 1:
        print("WARNING: duplicate transmission. Cancel the extra MARK(s):", [x.get("mark") for x in same if x.get("mark") != inv["mydata"]["mark"]])
