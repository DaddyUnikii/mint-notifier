# Pantau beast

Notifier Telegram untuk mint baru di BNB Chain. Membaca feed beastfarmer.xyz/monitorbnb dan dataflux.my.id, mengecek kontrak secara read-only (harga, supply, standar token, kepemilikan wallet), lalu mengirim satu pesan per kontrak baru.

Tidak pernah menandatangani atau mengirim transaksi.

## Secrets
- `TELEGRAM_BOT_TOKEN` dari @BotFather
- `TELEGRAM_CHAT_ID` chat tujuan
- `WATCH_WALLETS` (opsional) alamat dipisah koma untuk kolom "sudah pegang"

Jalan tiap 10 menit lewat GitHub Actions, satu cek singkat per run.
