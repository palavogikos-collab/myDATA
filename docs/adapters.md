# Writing an ERP adapter

An adapter is one Python module in `adapters/` with:

```python
def post_invoice(inv: dict, dry: bool = False) -> dict
def verify_single_transmission(inv: dict) -> None   # optional
```

`issue.py erp <id>` imports the module named in `config.json` → `erp.adapter` and calls
`post_invoice`. `--dry-run` passes `dry=True`: print what you would send, change nothing.
`--yes` sets `WRITE_ENABLED=1` in the environment for that run; read it if your client
has a write guard.

## Checklist

1. **Customer by ΑΦΜ.** Look the customer up by VAT number. If missing, create it from
   `inv["customer"]` (name, street, city, postal_code, email) and store the ERP id in
   `inv["customer"]["refs"]["<erp>_id"]` and in the cache with
   `invoice_store.save_customer(vat, <erp>_id=...)`.
2. **External series / document type.** Use the ERP's series for documents issued
   outside the ERP (in SAP B1 this is a separate numbering series; in others it is a
   document type such as "Τιμολόγιο από τρίτο σύστημα"). Put the timologio
   `series/number` in the customer-reference field.
3. **Already transmitted.** Find the field that tells the ERP's myDATA connector not to
   send this document, and the field that stores the MARK. In SAP B1 with the Hellenization
   add-on these are the UDFs `U_AADE = N` and `U_Mark`. If your ERP has an "import
   external MARK" function, prefer it.
4. **Lines.** Use `inv["lines"][i]["refs"]` for item codes and accounts. Fall back to an
   account line with `description` when the item master is not reachable.
5. **Write back.** On success fill `inv["sync"]["erp"]` with `adapter`, `docentry`,
   `docnum`, `series`, `synced_at`; set `inv["status"] = "synced"`; append an audit line;
   `invoice_store.save(inv)`.
6. **Idempotency.** Refuse to post when `inv["sync"]["erp"]["docentry"]` is set. Refuse
   when `inv["mydata"]["mark"]` is empty.
7. **Verify.** After posting, ask myDATA (`scripts/mydata_api.py`, `RequestTransmittedDocs`
   for the issue date) and warn if more than one document matches the customer and
   amount. That catches a connector that ignored the flag.

## Reference

`adapters/sap_b1.py` does all seven against the SAP Business One Service Layer. Points
of interest: `next_cardcode()` (numbering series whose counter does not advance for API
inserts), `invoice_body()` (service document on a revenue account when items are not
readable), the PATCH of `U_Mark` after insert (the add-on clears UDFs on insert).

## Known ERP notes (please extend)

| ERP | Customer lookup | External document | Transmitted flag / MARK | Status |
|---|---|---|---|---|
| SAP Business One + Hellenization | `BusinessPartners` by CardCode (lists hidden by Data Ownership; key access works) | Series 83 ΤΠΥ-Χ, `DocType dDocument_Service` | `U_AADE=N`, `U_Mark`, `U_MyDataType`; `ElectronicProtocols.EBooksMARK` rejected via SL for a normal user | working |
| SoftOne (Soft1) | `/s1services/list/customer` with `AFM=<vat>` (no quotes), `/s1services/set/customer` | `/s1services/set/saldoc` (SRVLINES for services, ITELINES for goods) into a series set up as "issued by third party" (`erp.external_series`) | `SOMYDATAMARK` on SALDOC (`erp.mark_field`); never call `/s1services/einvoice` | drafted, needs a real test |
| Epsilon Pylon | REST | ? | ? | wanted |
| Megasoft Prisma Win | ? | ? | ? | wanted |
| Entersoft | EBS Web API | ? | ? | wanted |
