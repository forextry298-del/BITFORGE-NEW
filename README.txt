BITFORGE BITCOIN MINING UPDATE

This package updates the BitForge mobile mining UI and account workflows.

Included:
- Withdrawal request reserves/deducts the full requested amount immediately.
- Withdrawal fee is 6%; the displayed net payout is 94%.
- Admin can approve a withdrawal or reject it; rejection refunds the full reserved amount.
- Orders page lists mining-rig records only, not deposit/withdrawal transactions.
- Insufficient-balance actions display: INSUFFICIENT FUNDS.
- Admin panel supports user listing, balance/phone viewing, block/unblock, password reset,
  pending deposit verification, and withdrawal approval/rejection.
- Admin shortcut is shown for phone 0758878297 (stored in normalized form +256758878297).
- The large shaded mining disclosure panel is removed; a compact disclosure remains.

REQUIRED RENDER ENVIRONMENT VARIABLES
- SECRET_KEY: set a long random secret.
- BITFORGE_ADMIN_PHONE: 0758878297 (or +256758878297).
- BITFORGE_ADMIN_PANEL_KEY: set a strong private key. Admin must sign in to the account
  registered to the designated phone and then enter this key.
- BITFORGE_DATA_DIR: /var/data only if a persistent disk is actually mounted there.

IMPORTANT MINING LIMITATION
The app does not have a verified mining-pool API, worker identity, payout wallet, or
settlement feed configured. Mining activation remains blocked and this package does NOT
fabricate live mining, daily earnings, or automatic midnight credits. The 20%, 34%, and
47.6% figures remain illustrative scenario calculations only. Do not advertise them as
verified mining yields. Real midnight credits require integration with a genuine pool's
verified settlement records and idempotent payout reconciliation.

DEPLOYMENT
Run: python -m py_compile app.py
Then deploy the contents of this folder to the existing BitForge service/repository.
Back up the persistent database before deployment. Do not delete or replace bitforge.db.
