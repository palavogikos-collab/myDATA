---
name: mydata-invoicing
description: >
  Issue Greek invoices through AADE's timologio, get the MARK, keep one JSON per invoice,
  push it into the ERP (SAP Business One today; other adapters welcome) and email the PDF.
  Trigger on: τιμολόγιο, τιμολόγησε, έκδοση τιμολογίου, invoice, myDATA, timologio, MARK,
  ΤΠΥ, πελάτης ΑΦΜ, προεπισκόπηση τιμολογίου.
---

# myDATA invoicing (timologio + ERP)

`ROOT` = this folder. Run everything with `python3 ROOT/scripts/issue.py ...`.
Config in `ROOT/config.json`, credentials in `ROOT/.env` (never read aloud, never printed).

Preconditions: Chrome with the user logged in at https://mydata.aade.gr/timologio.
The browser client is `ROOT/scripts/timologio_client.js`; inject its full text with the
JavaScript tool into the timologio tab after every navigation.

## The rule

Three actions are irreversible: `TL.issue()`, `issue.py erp --yes`, sending the email.
Each one happens only after the user has seen the result of the previous step and said
«ναι» in the chat. Never chain them without that.

## Flow

1. `issue.py new --vat <ΑΦΜ> --item <key> --net <amount> [--descr] [--notes] [--payment]`
   Prints the draft id and a SPEC json.
2. Customer unknown to timologio? Open `/timologio/customer/NewCustomer`, inject the
   client, `TL.createCustomer(vat, email)` fills it from the tax registry. Show the data,
   then `TL.saveCustomer()`. Record the code from the green banner:
   `invoice_store.save_customer(vat, timologio_code=N, name=..., street=..., ...)`.
3. Open `/timologio/invoice/newinvoice`, inject, `await TL.build(spec)`. Check
   `totals` against `spec.expect`. `await TL.preview()`, `TL.showPdf()`, screenshot, show
   the user. `issue.py previewed <id>`. `TL.hidePdf()`.
4. Wait for «ναι». `await TL.issue()` → `{mark, aa}`. At once:
   `issue.py issued <id> --mark <mark> --aa <aa>`.
5. Navigate the tab to `https://mydata.aade.gr/timologio/Invoice/PrintInvoice2PdfNew?mark=<mark>`
   (it downloads). `issue.py pdf <id>`.
6. `issue.py erp <id> --dry-run`, show the body. On «ναι»: `issue.py erp <id> --yes`.
   Report DocNum and the myDATA duplicate check.
7. `issue.py email <id>` → to / subject / body / attachment. Send from Gmail in Chrome:
   open `https://mail.google.com/mail/?view=cm&fs=1&to=<to>&su=<subject>`, click the body,
   type it, upload the PDF with the file-upload tool on the compose file input, click Send.
   Then `issue.py emailed <id> --to ... --to ...`.

`issue.py list` shows every invoice with MARK, ERP document and state.

## When things fail

- `TL.build` returns `{error}`: read it, fix the input, `btnClear`, retry. Do not issue.
- Preview PDF shows wrong amounts: stop, ask.
- `issue()` returned no MARK: check `/timologio/invoice/listinvoices` before retrying;
  a second issue creates a second invoice.
- ERP post failed after issuance: the JSON stays `issued`. Fix and rerun `erp --yes`; the
  adapter will not double-post.
- myDATA check shows 2 documents: the ERP connector re-sent. Cancel the extra MARK in
  myDATA, then set the ERP's "transmitted" flag by hand and note it in the audit.
