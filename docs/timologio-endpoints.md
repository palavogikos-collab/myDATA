# timologio: what the browser client touches

timologio (`https://mydata.aade.gr/timologio`) is a server-rendered ASP.NET MVC app with
jQuery. There is no public API. `scripts/timologio_client.js` runs inside the user's
logged-in tab and uses the app's own form, its own JavaScript (`refreshInvoiceLineValues`,
the jQuery UI autocomplete) and its own AJAX endpoints. The anti-forgery token is read
from the page. No cookie ever leaves the browser.

Observed on 23.09.2026. Expect changes without notice.

| Purpose | Call | Notes |
|---|---|---|
| Customer lookup | `GET /timologio/Customer/GetProposedCustomersByName/?companyVat=&invType=&term=` | autocomplete source; `term` can be the ΑΦΜ |
| Registry lookup (new customer) | `GET /timologio/Customer/GetCounterpart?companyVat=&customerVat=` | fired by the «Φορολογικό Μητρώο» button; returns name, address, city, ZIP, ΔΟΥ |
| Create customer | `POST /timologio/customer/NewCustomer` (form) | anti-forgery token; result page shows `Κωδικός Πελάτη: 'N'` |
| Validation rules per type | `GET /timologio/Invoice/GetValidationDoc?invType=&selfPrice=&fromNewInvoice=true` | |
| Product defaults | `GET /timologio/Product/GetProduct?sCompanyVat=&productCode=&invoiceType=&selfPrice=` | unit, price, VAT, default classification |
| Preview | `POST /timologio/Invoice/PrintPreviewInvoice2PdfNew` body `inv=<json>` | full server validation, returns `application/pdf` stamped «Προεπισκόπηση» |
| Issue | `POST /timologio/Invoice/create` body `inv=<json>` | returns `{mark, aa, qrUrl, genericMsg}`; irreversible |
| Official PDF | `GET /timologio/Invoice/PrintInvoice2PdfNew?mark=<MARK>` | `Content-Disposition: attachment`, Chrome saves `printinvoice<MARK>.pdf` |
| List / search | `POST /timologio/invoice/SearchInvoices` (form) | |
| Temp save | `POST /timologio/TempInvoice/savetempinvoice` | not used |

## The `inv` payload

The client does not build `inv` by hand. It fills the form and intercepts the object the
app itself sends to the preview endpoint (`$.ajax` hook), then re-uses that object for
`create`. This keeps the client correct when AADE adds fields. Top-level keys seen:

`_invoiceType, invoiceHeader{series, aa, issueDate (YYYY-M-D, no padding), currency,
multipleConnectedMarks, otherCorrelatedEntities, ...}, issuer{vatNumber, country, branch},
counterpart{vatNumber, name, address{street, number, city, postalCode}, country, branch,
customerCode, emailAddress}, paymentType, invoiceLines[{lineNumber, itemCode, itemId,
itemDescr, unitPrice, netValueWithoutDiscount, netValueWithDiscount, vatCategory,
vatAmount, totalValue, classifications[{category, code, amount}], ...}], invoiceTaxes,
invoiceNotes, isB2G, isDeliveryNote, selfPricing, timologioIssueLanguage`.

## Things that bit us

- Setting `#unitPrice` with jQuery `.val()` does not recalculate the line. Call
  `refreshInvoiceLineValues($('#unitPrice'), 'frmModalInvoiceLine')` afterwards.
- If the product has a default classification, do not add another one; the line then
  carries two and the server rejects it ("ποσό χαρακτηρισμών E3 μεγαλύτερο από το
  καθαρό ποσό").
- `issueDate` is `2026-9-23`, not `2026-09-23`.
- Downloads triggered from inside the page (blob + `a.click()`) go to Chrome's download
  folder, which may be a Google Drive folder. `issue.py pdf` looks in
  `config.downloads_dirs`.
- The preview PDF can be shown inside the page in an `<iframe>` (`TL.showPdf`), which is
  how the agent screenshots it for the user.
