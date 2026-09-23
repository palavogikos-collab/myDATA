"""
adapters/softone.py  -  STUB. Wanted.

Implement the two functions below against the ERP's API and open a pull request.
Read adapters/sap_b1.py for the reference implementation and docs/adapters.md for the
checklist. The input is the invoice JSON described in docs/invoice-schema.md; it already
has a MARK when this is called.

Questions to answer for this ERP before coding:
  1. How is a customer looked up by ΑΦΜ, and created with name/address/email?
  2. Which document type / series is used for invoices issued OUTSIDE the ERP?
  3. Which field says "already transmitted to myDATA" and which field holds the MARK,
     so the ERP's own connector does not send it again?
  4. What identifies the created document (for inv["sync"]["erp"]["docentry"/"docnum"])?
"""
import sys


def post_invoice(inv, dry=False):
    raise NotImplementedError("softone adapter not written yet. See docstring and docs/adapters.md.")


def verify_single_transmission(inv):
    pass
