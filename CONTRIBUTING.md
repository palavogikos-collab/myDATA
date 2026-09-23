# Contributing

Adapters first. Everything else second.

## The mechanics

1. Fork the repository on GitHub.
2. Create a branch in your fork (`adapter-softone`, `fix-timologio-client`).
3. Commit your change with a message that says what and why.
4. Open a pull request against `main`. Describe what you tested and how.
5. A maintainer reviews, asks questions in the PR, and merges.

Small fixes (typos, a wrong endpoint name) can go straight to a PR. Anything that touches
the approval stops or the invoice schema starts as an issue first.

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
