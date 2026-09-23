# myDATA invoicing skill

Issue Greek invoices through AADE's free **timologio** application, get the MARK, keep one
JSON per invoice, and push it into your ERP through a small adapter. Driven by a Claude
skill: you say "invoice X to customer Y for Z euros", you approve a preview, everything else
happens.

Works today with **SAP Business One** (Service Layer). Adapters for **SoftOne**, **Epsilon
Pylon**, **Megasoft** and others are the point of this repo being public. See
[Contributing](#contributing).

---

## What it is

A set of scripts and a `SKILL.md` that let an AI agent (Claude, through the Claude desktop
app and the Chrome extension) do the full invoicing loop for a small Greek company:

1. build the invoice in **timologio** (AADE's own e-invoicing app, free of charge),
2. show you the PDF preview and stop,
3. after your explicit "yes", issue it and receive the **MARK**,
4. download the official PDF (with MARK and QR),
5. write the invoice into the ERP, flagged as *already transmitted*, so the ERP's own
   myDATA connector does not send it a second time,
6. email the PDF to the customer,
7. keep a JSON file per invoice with every step recorded.

The JSON file is the single source of truth. timologio provides the MARK and the PDF, the
ERP keeps the books, the JSON links the two. Tomorrow's ERP reads the same file.

## Why it exists

From 2026 B2B e-invoicing is mandatory in Greece. The accepted channels are a certified
provider (ΥΠΑΗΕΣ) or AADE's timologio. Providers charge per invoice plus yearly fees. For a
company that issues a few dozen invoices a year the cost is out of proportion, and the ERP's
myDATA connector alone does not satisfy the e-invoicing rule.

timologio is free, official and complete, but it is a web form. This repo turns that web
form into something an agent can operate reliably, and closes the gap back to the ERP.

## What it does, concretely

```
python3 scripts/issue.py new --vat 999999999 --item service --net 900 --descr "Παροχή υπηρεσιών, Οκτώβριος"
```

creates `data/invoices/2026/DRAFT-....json` and prints a *spec*. The agent injects
`scripts/timologio_client.js` into the logged-in timologio tab and runs:

```js
await TL.build(spec)      // fills the form using the app's own JS, captures the payload
await TL.preview()        // server-side validation + PDF, nothing issued
TL.showPdf()              // shown to the user, who says «ναι»
await TL.issue()          // -> { mark, aa }
```

then

```
python3 scripts/issue.py issued  DRAFT-... --mark 4000... --aa 12   # DRAFT -> MYDATA-000012
python3 scripts/issue.py pdf     MYDATA-000012                       # picks up printinvoice<mark>.pdf
python3 scripts/issue.py erp     MYDATA-000012 --dry-run             # shows the ERP body
python3 scripts/issue.py erp     MYDATA-000012 --yes                 # posts it
python3 scripts/issue.py email   MYDATA-000012                       # to / subject / body / attachment
python3 scripts/issue.py list
```

`scripts/mydata_api.py` is a read-only client for the myDATA REST API (`RequestMyIncome`,
`RequestMyExpenses`, `RequestTransmittedDocs`, `RequestDocs`). It is used after every
issuance to confirm the ERP did not re-transmit, and it is useful on its own for
reconciliation.

## What it is NOT

- **Not** a Πάροχος Υπηρεσιών Ηλεκτρονικής Τιμολόγησης (ΥΠΑΗΕΣ). It does not issue
  anything itself. AADE's timologio issues the invoice; this repo operates timologio.
- **Not** an official AADE API client for issuing. timologio has no public API. The
  browser client uses the app's own form and endpoints from inside the user's logged-in
  session. When AADE changes the app, `timologio_client.js` needs a fix. Expect this.
- **Not** an accounting system. It records what was issued and where it was synced.
  Balances, VAT returns, payments live in the ERP.
- **Not** unattended. Every irreversible step (issue, ERP post, email) waits for a human
  "yes". There is no batch mode on purpose.
- **Not** a credential store. TAXISnet login is done by the user in Chrome. myDATA and
  ERP credentials live in a local `.env` that is never committed.

## Where it helps

- Companies with low invoice volume that do not want to pay a provider.
- Consultants who issue the same service invoice to the same customers every month.
- Anyone whose ERP already transmits to myDATA and needs a clean way to record invoices
  issued *outside* the ERP without double transmission.
- Accountants who want a per-invoice audit trail (draft, preview, MARK, ERP document,
  email) in plain files.

## Who can use it, and how

**A business owner or accountant, as is.** You need: a computer with Python 3, Chrome with
the Claude extension, the Claude desktop app, a TAXISnet login for timologio, myDATA REST
credentials (free, from mydata.aade.gr) and, for the ERP leg, SAP Business One with Service
Layer access. Follow Setup, add the folder to the Claude desktop app, log in to timologio,
then say «τιμολόγιο 900 ευρώ στον ΑΦΜ 123456789 για παροχή υπηρεσιών». Claude reads
`SKILL.md`, stops at the preview, waits for «ναι», issues, syncs, emails.

Without an ERP you still get issuance, MARK, PDF, email and the JSON record. The `erp`
step just reports that no adapter is configured.

**Manual mode, without Claude.** `issue.py` is a plain CLI and `timologio_client.js` runs
in any browser console. Open the timologio invoice page, paste the file's contents into the
DevTools console, then call `TL.build(spec)` with the SPEC printed by `issue.py new`,
`TL.preview()`, `TL.showPdf()`, and `TL.issue()` when you are sure. Continue with
`issue.py issued`, `pdf`, `erp`, `email` by hand.

**A developer, adding an ERP.** Copy one of the stubs in `adapters/`, answer the four
questions in its docstring, implement `post_invoice(inv, dry=False)`, test with a 1 €
invoice, open a pull request. See `docs/adapters.md`.

**Known limits.** timologio updates break the browser client until someone patches it. The
SAP adapter depends on Service Layer permissions the SAP partner controls (item master
read, E-Books protocol write). There are no automated tests; a real 1 € invoice is the test.

## Repository layout

```
SKILL.md                    Claude skill: the step-by-step flow with the approval stops
scripts/
  issue.py                  CLI orchestrator (new / issued / pdf / erp / email / list)
  invoice_store.py          JSON schema, customer cache, adapter helpers
  timologio_client.js       browser-side client for timologio (injected into the tab)
  mydata_api.py             read-only myDATA REST client
adapters/
  sap_b1.py                 SAP Business One adapter (working)
  sap_b1_api.py             SAP B1 Service Layer client used by the adapter
  softone.py                stub, wanted
  epsilon_pylon.py          stub, wanted
  megasoft.py               stub, wanted
docs/
  invoice-schema.md         the JSON file, field by field
  timologio-endpoints.md    what the browser client calls and why
  adapters.md               how to write an ERP adapter
examples/
  invoice.example.json      a synced invoice (anonymised)
config.example.json         copy to config.json
.env.example                copy to .env, never commit .env
```

## Setup

1. `cp config.example.json config.json` and fill in your issuer block, items, ERP settings,
   and where Chrome saves downloads.
2. `cp .env.example .env` and fill in myDATA API credentials (from
   https://mydata.aade.gr, "Εγγραφή στο REST API") and, if you use SAP, the Service Layer
   login.
3. `pip3 install requests`.
4. In timologio (https://mydata.aade.gr/timologio): set up your series, your products
   with a default income classification (e.g. `E3_561_001`), and confirm the company
   profile. Products without a default classification are handled by the client, but a
   default is cleaner.
5. In the Claude desktop app: add this folder, connect Chrome, log in to timologio.
   Then ask for an invoice.

## Safety rules baked in

- `TL.preview()` runs the server's full validation and returns a PDF marked
  «Προεπισκόπηση». Nothing is issued until `TL.issue()`.
- `issue.py erp --yes` is required to post; `--dry-run` prints the body.
- The ERP adapter refuses to post an invoice that already has an ERP document id.
- After posting, `verify_single_transmission` asks myDATA how many documents match the
  invoice and warns on duplicates.
- `.gitignore` excludes `.env`, `data/`, and anything that looks like a customer list.

## Contributing

The interesting work is in `adapters/`. An adapter is a module with two functions:

```python
def post_invoice(inv: dict, dry: bool = False) -> dict   # creates customer if needed, posts, writes inv["sync"]["erp"]
def verify_single_transmission(inv: dict) -> None        # optional
```

`inv` is the JSON described in `docs/invoice-schema.md`. Read `adapters/sap_b1.py` as
the reference, and `docs/adapters.md` for the checklist (customer lookup by ΑΦΜ, external
series, "already transmitted" flag, MARK field, idempotency).

Wanted: **SoftOne** (Soft1 Web Services / S1 REST), **Epsilon Pylon** (REST), **Megasoft**
(Prisma Win), **Entersoft**, **Epsilon Net Hyper.Axon**. Open an issue with the ERP's
"external MARK" mechanism (how it marks an invoice as issued elsewhere) and we take it from
there.

Bug reports on `timologio_client.js` are expected whenever AADE ships a new version of
timologio. Include the failing step and the console output.

## License

MIT. See `LICENSE`.

## Disclaimer

Not affiliated with AADE, SAP, or any ERP vendor. Use at your own responsibility; the
issuer of an invoice remains responsible for its content.
