BITFORGE COMPLETE APPLICATION

This ZIP contains the complete Flask application, not a homepage-only patch.
The homepage is redesigned to match the supplied marketplace reference:
- Search bar, hero carousel using the supplied real project photographs
- Auto-advancing carousel (5 seconds), manual arrows and dots
- Category navigation, featured project cards, wallet preview
- Quick links, recent activity, profile/settings menu and dark mode
- Bottom navigation

Important scope:
- The visual redesign is for the HOME PAGE only.
- Registration, login, marketplace, wallet, deposit, withdrawal, orders and account remain separate functional pages.
- Global CSS also makes forms and navigation responsive and properly spaced.
- All amounts are displayed in UGX.
- Registration and login use hashed passwords.
- Deposit/withdrawal requests are saved as pending demo requests. This app does not connect to MTN/Airtel APIs or send/collect real money.
- New users start with UGX 0. Do not treat illustrative project listings or pending requests as real investment products or confirmed payments.

LOCAL RUN:
  pip install -r requirements.txt
  python app.py

RENDER:
  Build command: pip install -r requirements.txt
  Start command: gunicorn --bind 0.0.0.0:$PORT app:app

DATABASE:
SQLite database file is created automatically. On Render, attach a persistent disk and configure a persistent database path before using real user data; the default service filesystem may be ephemeral.
