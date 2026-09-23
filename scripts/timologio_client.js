// timologio_client.js  -  runs INSIDE the logged-in timologio tab (mydata.aade.gr/timologio).
// Injected by Claude through the Chrome extension. Uses the app's own form and jQuery
// so the payload is always what the app itself would send. No cookies leave the browser.
//
//   TL.findCustomer(vat)          -> {found, code, name, ...} via the app's autocomplete endpoint
//   TL.createCustomer(vat, email) -> run on /timologio/customer/NewCustomer; returns fetched registry data
//   TL.build(spec)                -> run on /timologio/invoice/newinvoice; fills the form, returns the inv JSON
//   TL.preview(inv)               -> POST PrintPreviewInvoice2PdfNew, returns {status, size, blobUrl}
//   TL.issue(inv)                 -> POST Invoice/create, returns {mark, aa}  (irreversible)
//   TL.showPdf(blobUrl) / TL.hidePdf()
(function () {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const token = () => (document.querySelector('input[name=__RequestVerificationToken]') || {}).value || '';
  const hook = () => {
    if (window.__ajaxHooked) return;
    const orig = $.ajax; window.__calls = [];
    $.ajax = function (o) { try { window.__calls.push({ url: o.url, type: o.type, data: o.data }); } catch (e) {} return orig.apply(this, arguments); };
    window.__ajaxHooked = true;
  };
  const post = async (path, inv) => {
    const t = token();
    return fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest', 'RequestVerificationToken': t }, body: $.param({ inv, __RequestVerificationToken: t }) });
  };
  const TL = {
    companyVat() { const m = (document.querySelector('#my-account') || {}).innerText || ''; return (m.match(/(\d{9})/) || [])[1] || ''; },
    async findCustomer(vat) {
      const r = await fetch('/timologio/Customer/GetProposedCustomersByName/?' + $.param({ companyVat: TL.companyVat(), invType: '20', term: vat }));
      const list = await r.json();
      const hit = (list || []).find(x => JSON.stringify(x).includes(vat));
      return hit ? { found: true, raw: hit } : { found: false };
    },
    async createCustomer(vat, email) {
      if (!location.pathname.toLowerCase().includes('newcustomer')) return { error: 'open /timologio/customer/NewCustomer first' };
      $('#CustomerType').val('2').trigger('change'); await sleep(400);
      $('#CustomerVat').val(vat).trigger('change').trigger('keyup'); await sleep(200);
      document.getElementById('btnCounterpart').click();
      for (let i = 0; i < 20 && !$('#CustomerName').val(); i++) await sleep(500);
      if (!$('#CustomerName').val()) return { error: 'registry lookup returned nothing' };
      if (email) $('#CustomerEmail').val(email).trigger('change');
      const data = { name: $('#CustomerName').val(), street: $('#CustomerAddress').val(), city: $('#CustomerCity').val(), postal_code: $('#CustomerZipCode').val(), doy: $('#Doy').val(), email: $('#CustomerEmail').val() };
      // caller clicks #btnSave after showing the data; saving navigates to ViewCustomer with "Κωδικός Πελάτη: 'N'"
      return data;
    },
    saveCustomer() { document.getElementById('btnSave').click(); return 'saving'; },
    async build(spec) {
      if (!location.pathname.toLowerCase().includes('newinvoice')) return { error: 'open /timologio/invoice/newinvoice first' };
      hook();
      $('#_invoiceType').val(spec.invoiceType).trigger('change'); await sleep(2500);
      $('#counterpartData').val(spec.customerVat); $('#counterpartData').autocomplete('search', spec.customerVat); await sleep(3000);
      const it = [...document.querySelectorAll('.ui-autocomplete li')].find(li => li.innerText.includes(spec.customerVat));
      if (!it) return { error: 'customer not in timologio: ' + spec.customerVat };
      (it.querySelector('div,a') || it).click(); await sleep(1500);
      $('select[name="invoice.paymentType"]').val(spec.paymentType).trigger('change');
      for (const line of spec.lines) {
        document.getElementById('btnNewInvoiceLine').click(); await sleep(1200);
        $('#itemLine').val(line.item).trigger('change'); await sleep(1500);
        if (line.quantity && line.quantity !== 1) $('#itemQuantity').val(String(line.quantity));
        $('#unitPrice').val(String(line.unitPrice));
        refreshInvoiceLineValues($('#unitPrice'), 'frmModalInvoiceLine'); await sleep(800);
        const cls = [...document.querySelectorAll('.modal.show table tbody tr')];
        if (cls.length === 0 && line.classification) {
          document.getElementById('btnNewClassification').click(); await sleep(1000);
          $('#clsCategory').val(line.classification.category).trigger('change'); await sleep(800);
          $('#clsCode').val(line.classification.code).trigger('change'); await sleep(300);
          document.getElementById('btnSaveClsList').click(); await sleep(800);
        }
        document.getElementById('btnAddLine').click(); await sleep(1500);
        if (document.querySelector('.modal.show')) return { error: 'line modal did not close: ' + [...document.querySelectorAll('.modal.show .text-danger')].map(e => e.innerText).join(' | ') };
      }
      if (spec.notes) $('#invoiceNotes, textarea[name="invoice.invoiceNotes"]').val(spec.notes);
      window.__calls = [];
      document.getElementById('btnPreview').click();
      for (let i = 0; i < 20 && !window.__calls.find(c => c.data && c.data.inv); i++) await sleep(500);
      const c = window.__calls.find(x => x.data && x.data.inv);
      const errs = [...document.querySelectorAll('.modal.show .modal-body')].map(e => e.innerText.trim()).filter(Boolean);
      if (!c) return { error: 'no payload captured', errs };
      window.__inv = c.data.inv;
      const t = { net: $('#totalNetPrice').val(), vat: $('#totalVatAmount').val(), gross: $('#grossAmount').val() };
      return { ok: true, totals: t, customer: c.data.inv.counterpart.vatNumber, lines: c.data.inv.invoiceLines.length };
    },
    async preview(inv) {
      const r = await post('/timologio/Invoice/PrintPreviewInvoice2PdfNew', inv || window.__inv);
      const b = await r.blob();
      if (r.status !== 200) return { status: r.status, text: (await b.text()).slice(0, 300) };
      window.__pdfUrl = URL.createObjectURL(b);
      return { status: r.status, size: b.size, blobUrl: window.__pdfUrl };
    },
    showPdf(url) {
      let f = document.getElementById('__pdfFrame');
      if (!f) { f = document.createElement('iframe'); f.id = '__pdfFrame'; f.style.cssText = 'position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:99999;background:#fff;border:0'; document.body.appendChild(f); }
      f.src = (url || window.__pdfUrl) + '#zoom=page-fit'; return 'shown';
    },
    hidePdf() { const f = document.getElementById('__pdfFrame'); if (f) f.remove(); return 'hidden'; },
    async issue(inv) {
      const r = await post('/timologio/Invoice/create', inv || window.__inv);
      const txt = await r.text(); let j = null; try { j = JSON.parse(txt); } catch (e) {}
      if (!j || !j.mark) return { status: r.status, error: (j && j.genericMsg) || txt.slice(0, 300) };
      window.__issued = j;
      return { status: r.status, mark: j.mark, aa: j.aa, msg: j.genericMsg || '' };
    },
    pdfUrlForMark(mark) { return location.origin + '/timologio/Invoice/PrintInvoice2PdfNew?mark=' + mark; },
  };
  window.TL = TL;
  return 'TL ready';
})();
