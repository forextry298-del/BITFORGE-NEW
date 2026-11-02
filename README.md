# BitForge local demo

A compact mobile-first Flask prototype inspired by the supplied BitForge reference screens.

## Run in Termux
```bash
pkg install python -y
cd ~/storage/downloads
unzip -o BITFORGE_REBUILT.zip -d ~/BITFORGE_REBUILT
cd ~/BITFORGE_REBUILT
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000

The first screen is Registration. After registering, sign in; successful login opens the investment dashboard.
All balances are explicitly shown as demo-only; deposit/withdrawal/investment forms save pending requests and do not process real money.
# Bitforge-
