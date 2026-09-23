---
name: New ERP adapter
about: Propose or start an adapter for an ERP
title: "adapter: <ERP name>"
labels: adapter
---

**ERP and version**

**1. Customer lookup and creation.** How is a customer found by ΑΦΜ, and created with
name, address, email? (API call, table, endpoint)

**2. External document.** Which document type or series records an invoice issued
outside the ERP?

**3. Transmitted flag and MARK.** Which field tells the ERP's myDATA connector not to send
this document, and which field holds the MARK? Is there an "import external MARK" function?

**4. Document id.** What identifies the created document (for `sync.erp.docentry` /
`docnum`)?

**Access.** Do you have a test company or sandbox? Can you run a 1 € invoice end to end?

**Anything else** (auth scheme, rate limits, known quirks)
