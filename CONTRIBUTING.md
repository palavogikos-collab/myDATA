# Contributing

Adapters first. Everything else second.

1. Open an issue named `adapter: <ERP>` and answer the four questions in
   `adapters/<erp>.py`'s docstring (customer lookup, external document type,
   transmitted flag + MARK field, document id).
2. Implement `post_invoice(inv, dry=False)` and, if you can, `verify_single_transmission`.
   `dry=True` must print and change nothing.
3. Test on a 1 € invoice to yourself. Paste the redacted `issue.py show <id>` output in
   the PR.
4. Add a row to the table in `docs/adapters.md`.

Rules:

- No credentials, customer lists, MARKs or ΑΦΜ of real third parties in commits. `.gitignore`
  covers `.env` and `data/`; check anyway.
- No em dashes in docs. Plain Greek or plain English, short sentences.
- The browser client uses the app's own JS wherever possible. If you need to build the
  payload by hand, explain why in the PR.
- Keep the three approval stops. A PR that removes a stop is closed.

Report timologio breakage with the failing `TL.*` call, the returned error and the
timologio version (footer of the app).
