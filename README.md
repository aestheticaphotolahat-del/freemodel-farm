# FreeModel Account Farmer & API Tester

Automation tool untuk membuat akun freemodel.in, mengklaim kuota 1M token/bulan, mengumpulkan API keys, dan melakukan sanity test langsung pada endpoint model AI (OpenAI-compatible).

## Fitur
- **Auto Register**: Otomatis registrasi akun baru dengan penanganan CSRF & session cookies.
- **Telegram Verification Extractor**: Menangkap kode verifikasi Telegram (`@FreeModels_bbot`) untuk unlock 1.000.000 tokens bulanan gratis.
- **Model Inspector**: Mengambil dan menampilkan katalog model aktif (46 model seperti Claude Mythos Preview, Claude Fable 5, GPT-6 Astra, DeepSeek-V4-Pro-Max, dll).
- **API Completion Tester**: Menguji request chat completion via API key.
- **Auto Storage**: Menyimpan akun & kredensial ke `freemodel_accounts.json`.

## Cara Menjalankan
```bash
# Menggunakan python launcher
py -3.12 freemodel_farmer.py

# Atau via shortcut runner
run.cmd
```
