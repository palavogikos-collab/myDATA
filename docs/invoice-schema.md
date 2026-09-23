# The invoice JSON (`mydata.invoice/1`)

One file per invoice: `data/invoices/<year>/<id>.json`. Drafts are `DRAFT-<timestamp>`;
after issuance the file is renamed to `<series>-<aa padded to 6>` (`MYDATA-000012`).
The official PDF sits next to it.

Status flow: `draft` → `previewed` → `issued` (has MARK) → `synced` (has ERP document).

```json
{
  "schema": "mydata.invoice/1",
  "id": "MYDATA-000012",
  "status": "synced",
  "type": "2.1",
  "series": "MYDATA",
  "number": 12,
  "issue_date": "2026-09-23",
  "currency": "EUR",
  "issuer":   { "vat": "...", "name": "...", "branch": 0, "street": "...", "city": "...", "postal_code": "...", "doy": "..." },
  "customer": {
    "vat": "999999999", "name": "...", "street": "...", "city": "...", "postal_code": "...",
    "country": "GR", "doy": "...", "email": "...",
    "refs": { "sap_cardcode": "C.000001", "timologio_code": 1 }
  },
  "payment": { "method": "credit", "label": "Επί πιστώσει", "due_date": "2026-10-23" },
  "lines": [{
    "no": 1, "item_key": "service", "description": "Παροχή υπηρεσιών",
    "quantity": 1, "unit_price": 900.0, "net": 900.0, "vat_rate": 24, "vat": 216.0, "gross": 1116.0,
    "classification": { "category": "category1_3", "code": "E3_561_001" },
    "refs": { "sap_itemcode": "I.000001", "sap_account": "73.00.00.0000", "sap_project": "000001", "timologio_item": 1 }
  }],
  "totals": { "net": 900.0, "vat": 216.0, "withheld": 0, "gross": 1116.0 },
  "notes": "free text printed on the invoice",
  "mydata": { "mark": "400000000000001", "uid": null, "transmitted_at": "2026-09-23T14:35:02+03:00", "channel": "timologio" },
  "pdf": "data/invoices/2026/MYDATA-000012_999999999_400000000000001.pdf",
  "sync": {
    "erp":   { "adapter": "sap_b1", "docentry": 1001, "docnum": 1001, "series": 83, "synced_at": "...", "mark_patch": "ok" },
    "email": { "sent_at": "...", "to": ["info@customer.gr", "accounting@issuer.gr"] }
  },
  "audit": [
    { "at": "...", "step": "draft", "by": "user" },
    { "at": "...", "step": "previewed" },
    { "at": "...", "step": "issued", "mark": "400000000000001", "aa": 12 },
    { "at": "...", "step": "sap_bp_created", "cardcode": "C.000001" },
    { "at": "...", "step": "sap_invoice", "docentry": 1001, "docnum": 1001, "total": 1116.0 },
    { "at": "...", "step": "emailed", "to": ["..."] }
  ]
}
```

## Field notes

- `type` is the myDATA invoice type (`1.1` sales, `2.1` services, `5.1` credit note ...).
  Only `2.1` and `1.1` are wired into `to_timologio_spec` today.
- `customer.refs` holds every system's id for the same ΑΦΜ. Add a key per ERP
  (`softone_trdr`, `pylon_id`, ...). The customer cache `data/customers.json` is keyed by
  ΑΦΜ and merges these.
- `lines[].classification` is the myDATA income classification. timologio applies the
  product's default; the browser client adds one only when the product has none.
- `lines[].refs` is where the ERP adapter finds its own codes (item, account, project).
- `mydata.mark` is the primary key across systems. Adapters must store it in the ERP
  document and must never post a document for an invoice without a MARK.
- `sync.erp` is written by the adapter. `docentry`/`docnum` are whatever identifies the
  document in that ERP. An adapter refuses to post when `docentry` is already set.
- `audit` is append-only.

## Helpers in `scripts/invoice_store.py`

- `new_draft(vat, item_key, net, ...)` builds a draft from `config.json` items and the
  customer cache.
- `to_timologio_spec(inv)` is what `TL.build()` consumes in the browser.
- `save_customer(vat, **fields)` updates the cache (timologio code, ERP code, address).
- `load / save / rename / audit / all_invoices`.
