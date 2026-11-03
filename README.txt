BITFORGE BITCOIN MINING UI UPDATE

This package updates the compact BitForge interface with the three supplied mining-facility images and Bitcoin mining presentation.

IMPORTANT LIVE-MINING STATUS
This package is NOT connected to a mining pool or physical ASIC hardware. It therefore does not claim live hashrate, daily BTC earnings, or automatic mining payouts. Mining activation is intentionally blocked; the app checks the user's wallet balance but does not deduct funds or invent earnings.

To enable genuine mining, the operator must supply and configure: (1) a real mining-pool account/API with worker-level accepted/rejected share data, (2) physical ASIC hardware or a verifiable hosted-mining contract, (3) a Bitcoin payout address, and (4) confirmed pool settlement records. Daily credits must be calculated from settled pool payouts, not fixed percentage promises.

The displayed 4549-day term is only a configured display period and does not guarantee output.

Deploy: install requirements.txt, configure SECRET_KEY and a persistent BITFORGE_DATA_DIR, then start with the existing Render start command. Back up the existing SQLite database before replacing deployed files.
