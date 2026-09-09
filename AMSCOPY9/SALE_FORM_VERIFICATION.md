# Sale Section — Form Fields Verification (2026-08-26)

**Question asked:** After many PR changes, is the Sale section broken — are the sale
forms missing fields / data-input fields?

**Verdict: NOT CONFIRMED — the sale forms in the current code (main @ 509be4f,
all 26 PRs merged) are complete.** Every field the backend reads is present in the
UI, every page renders HTTP 200, and the modals open with all controls visible
under the page's real JavaScript (Bootstrap + Flatpickr). No missing-field
defect was found.

## How it was verified

1. **Backend ↔ template parity** — every `request.form` key read by
   `app/blueprints/sales/_direct_sales_add_direct_sale.py` (43 fields) and
   `_direct_sales_edit_direct_sale.py` was matched against an actual rendered
   page. All present. (`driver_name` is intentionally not an input — it is
   derived from the Delivery Person rows; rent fields are computed/readonly by
   design.)
2. **Live render** — app booted on a seeded scratch DB; `GET /direct_sales`
   returns 200 with the full Add-Sale modal in the DOM; the lazy Edit modal
   endpoint `/direct_sales/<id>/edit-modal` returns 200 with 38 controls.
3. **Browser simulation with the page's real JS** (jsdom + real
   bootstrap.bundle + flatpickr served by the app):
   - "New Billed Sale" tile → modal opens, **0 JS errors**, 45 controls.
   - Switching category to **Cash Sale** correctly reveals the hidden-by-design
     Payment Method + Select Account sections.
   - Edit (pencil) button → lazy modal loads and shows all sections
     (client, delivery persons, sale date, item grid with Material / GRN /
     Alternate / Qty / Rate / Item Total, rent fields, bill nos, photo, URL,
     notes, total/discount/paid, payment method/account).
4. **Whole sale section** — `/bookings`, `/payments`, `/material_returns`,
   `/pending_bills`, `/dispatching` all render 200 with their inputs.
5. **PR review** — all 26 PRs were checked. The ones touching sale code:
   - #19 "form alignment": CSS-only tweaks (mobile labels, input heights, one
     `<div>` nesting fix). No fields removed.
   - #21 "notes on PDFs": display/print only.
   - #24 "PRED defects": idempotency hardening (server-side replay guard,
     payload hash). No fields removed.
   - #25/#26: schema/import paths, no sale-form changes.

## Add-Sale modal — verified field inventory (Billed mode)

Visible when opened: Sale Type, Client search (+ hidden `client_name` sync),
booking status panel, client balance panel, Delivery Person / Bags / Rent rows
(+ "Add Delivery Person"), Sale Date picker, item rows: Material, GRN,
Alternate, Qty (−/+), Unit Price (+ Reserved badge, per-item Ignore booking),
Item Total, Remove; RENT CHARGED TO CLIENT, DELIVERY PERSON RENT (ACTUAL),
RENT DIFFERENCE; AUTO BILL NO, MANUAL BILL NO; BILL PHOTO, PHOTO URL; NOTES;
TOTAL AMOUNT, DISCOUNT (+ reason), PAID NOW; Allow Negative Stock override;
Reset / Save Sale.

Hidden **by design**, revealed on interaction (verified working):
`manual_client_name` (Cash / Open Khata), PAYMENT METHOD + SELECT ACCOUNT
(Cash / Due / Mixed or paid > 0), `has_bill` / `create_invoice` /
`track_as_cash` / `draft_id` / `idempotency_key` / `bank_name` /
`account_name` / `account_no` (hidden technical inputs auto-filled from the
selected account).

## If fields still look missing in a browser

The code is not the cause. Likely explanations to check on the affected device:

1. **Stale cached JS/CSS** — hard-refresh (Ctrl+Shift+R) or clear cache; the
   lazy edit modal depends on `lazy_modal_loader.js?v=3`.
2. **The live deployment is not running this build** — confirm the deployed
   revision includes PRs #19–#25 (all merged 2026-08-24/25).
3. **Fields hidden by category logic** — e.g. a "Booked Sale" (Booking
   Delivery) default hides Payment Method and locks PAID NOW at 0 until the
   category/paid amount changes. This is intended behaviour, not data loss.
