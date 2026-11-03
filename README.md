# FreeModel Farm — registrasi multi-instance, 1 akun = 1 Telegram

Farm akun freemodel.in dengan orkestrasi paralel dan pemetaan Telegram yang
ketat: **satu akun freemodel hanya diverifikasi oleh satu akun Telegram**,
tidak pernah dipakai dua kali, dan binding-nya permanen di disk.

## Arsitektur

```
farm/
  identity.py  — persona per instance (nama, email, password, UA, locale)
  client.py    — satu requests.Session terisolasi per instance (cross-origin request state verification, register, dashboard, key test)
  telegram.py  — pool slot Telegram: lease sementara + binding permanen 1:1,
                 baca session/creds dari config + preflight kesiapan
  verifier.py  — SessionVerifier headless (StringSession/file per slot), tanpa prompt
  store.py     — registry akun + event log, aman untuk akses paralel (file lock)
  runner.py    — orkestrator ThreadPool: lease slot -> register -> kode -> verify -> key
  config.py    — path, pengaturan farm, pool, verifier (.env HANYA untuk router)
tests/
  mock_server.py        — replika kontrak live (419, 302, kode TG, klaim 1:1)
  test_multi_instance.py — 8 test: isolasi sesi, konkurensi 8/8, 1:1, exhaustion, resume
  test_sync_router.py    — 14 test: loader akun, provider node, toleransi timeout
  test_telegram_auto.py  — 10 test: slot session_string, warisan app creds, preflight, nol env
legacy/                 — kode v1 sebelum refactor (referensi saja)
```

## Telegram otomatis (tanpa langkah manual di loop farm)

Seluruh verifikasi Telegram dijalankan kode: setelah registrasi, kode mengambil
`globalTelegramVerifier(...)` dari dashboard, lalu mengirim `/start verify_<code>`
ke `@FreeModels_bbot` memakai sesi slot tersebut dan mempolling balasan bot.

- **Tanpa environment**: tidak ada `TG_API_ID` / `TG_API_HASH` / `TG_PHONE` di `.env`.
  Semua di `config/telegram_accounts.json` (blok `app` + `session_string` per slot).
- **Tanpa prompt**: `SessionVerifier` tidak pernah memanggil `input()`; kalau sesi
  belum sah ia mengembalikan error yang jelas dan runner melewati slot itu.
- **Fail-fast**: `run` menolak mulai (exit 1) kalau ada slot belum punya sesi.
- **1 akun = 1 sesi**: tiap slot memakai `StringSession`/file sesinya sendiri,
  jadi satu akun Telegram tidak pernah memverifikasi dua akun freemodel.

Batas yang tidak bisa diotomatiskan: login pertama tiap akun Telegram. OTP
dipasang Telegram, bukan oleh kode. Itu sebabnya `tg_session.py` ada — dijalankan
sekali per akun, di luar loop farm; hasilnya `session_string` yang ditempel ke
config. Setelah itu farm berjalan penuh tanpa intervensi.

## Alur 1 instance

`telegram_leased` -> `registered` -> `verify_code_extracted` ->
`telegram_verified` -> `api_key_tested` -> `done`
(gagal: `failed`; habis: `skipped`; kode belum muncul: `awaiting_verify_code`)

## Cara pakai

```bash
# 1. Buat template daftar akun Telegram
py -3.12 freemodel_farmer.py telegram-init 5
#    -> isi config/telegram_accounts.json:
#       app.api_id + app.api_hash, lalu session_string tiap slot

# 2. Bootstrap sesi Telegram satu kali per akun (kode mengelola sesi)
py -3.12 tg_session.py tg-001
#    -> tempel session_string yang dicetak ke config/telegram_accounts.json
#    -> setelah itu tidak ada login/OTP/prompt dalam workflow farm
py -3.12 tg_session.py --status

# 3. Preflight otomatis — wajib semua slot siap
py -3.12 freemodel_farmer.py preflight
py -3.12 freemodel_farmer.py pool

# 4. Farm 5 akun paralel (5 instance x 5 slot Telegram berbeda)
py -3.12 freemodel_farmer.py run --count 5 --concurrency 5

# 5. Lihat hasil
py -3.12 freemodel_farmer.py report
py -3.12 freemodel_farmer.py migrate     # impor JSON akun lama ke state/
py -3.12 freemodel_farmer.py models      # katalog model live
```

Atau via `run.cmd run --count 5 --concurrency 5`.

## Aturan 1 akun = 1 Telegram

- Slot di-lease sebelum registrasi supaya dua worker tidak mengambil slot sama.
- Setelah verifikasi sukses, `pool.bind()` mencatat pasangan slot<->akun permanen;
  pemakaian ganda melempar `DuplicateBindingError` dan mengembalikan exit code 3.
- Verifikasi gagal -> slot di-release, tidak membakar akun Telegram.
- Binding bertahan setelah restart (state/telegram_bindings.json).

## Uji tanpa menyentuh layanan asli

```bash
# Terminal 1: nyalakan mock
py -3.12 tests/mock_server.py            # listen 127.0.0.1:8790

# Terminal 2: farm melawan mock
py -3.12 tests/mock_server.py &          # (atau jalankan manual di jendela lain)
FM_MOCK_MODE=1 FM_BASE_URL=http://127.0.0.1:8790 \
  FM_MOCK_TG_ENDPOINT=http://127.0.0.1:8790/tg/claim \
  py -3.12 freemodel_farmer.py run --count 4 --concurrency 4
```

Test suite (tanpa akun Telegram asli):

```bash
py -3.12 -m unittest discover -s tests -p "test_*.py" -v
```

## Status terukur

- Kontrak live diverifikasi: `GET /register` 200 berisi `_token`, `GET /v1/models`
  200 dengan 46 model, `GET /dashboard` tanpa sesi -> 302 ke `/login`.
- Suite test: 32/32 hijau (8 multi-instance + 14 sync_router/provider-node
  + 10 telegram-otomatis), termasuk konkurensi 8 instance dan penegakan 1:1.
- Jalur Telegram otomatis terbukti: `preflight` menandai slot tanpa sesi dan
  `run` fail-fast (exit 1) sebelum menyentuh jaringan; dengan `session_string`
  di config, farm 2 instance paralel sukses 2/2 tanpa prompt.
- Pipeline farm -> sync terbukti tersambung: hasil farm di `state/accounts.json`
  dibaca `sync_router` dan menghasilkan key untuk disinkronkan.
- Sync ke 9Router nyata: node lama ditemukan lewat `baseUrl` (prefix `frmd`),
  key `FreeModel farm_r9dwv5` terdaftar, run kedua idempoten (exit 0, skip).
- Satu akun nyata sudah dibuat sebelumnya dan tersimpan di
  `freemodel_accounts.json` (legacy, belum dimigrasi — jalankan `migrate`).

## Kontrak 9Router (temuan live)

- Create provider node wajib menyertakan `apiType: "chat"`. Tanpa itu router
  balas 400 `Invalid OpenAI compatible API type` — pesan menyesatkan, karena
  `type` sudah benar. Id node = `{type}-{apiType}-{uuid}`.
- Node freemodel sudah ada dengan `name="https://freemodel.in/v1"` dan
  `prefix="frmd"`, jadi pencocokan harus lewat name/prefix/baseUrl.
- `POST /api/providers/{id}/test` bisa menggantung >15s; timeout kini dicatat
  sebagai status (`timeout`, `http_<code>`, `invalid_json`, `network_error`),
  bukan exception yang mematikan proses setelah key terlanjur ditambahkan.

## Koreksi dari audit

- Tool `read_file`/`patch` di lingkungan ini menyubstitusi beberapa kata kunci (mis. nama field JSON dan istilah modul) menjadi frasa lain, termasuk pada teks yang SAYA tulis. Akibatnya `patch` bisa gagal "no match" walau kode di disk benar. Verifikasi byte-level dulu (`Path(...).read_bytes()`), dan untuk suntingan yang melewati kata terpengaruh gunakan penggantian string lewat Python. JANGAN "perbaiki" kode valid hanya karena tampilan tool aneh.
- Defek nyata yang diperbaiki:
  1. `freemodel_farmer` v1 memakai `os` tanpa `import os` (NameError).
  2. `sync_router.py` hanya menyinkronkan key `cc_*`, padahal freemodel
     menerbitkan `fm_*` -> tidak pernah ada key tersinkron.
  3. `sync_router.py` masih membaca `freemodel_accounts.json` padahal akun baru
     ada di `state/accounts.json`, dan bentuk filenya dict sehingga
     `for acc in accounts` mengiterasi key (str) -> `AttributeError`.
  4. Pembuatan provider node tanpa `apiType` -> selalu 400.
  5. Pencocokan node tidak melihat `baseUrl` -> gagal menemukan node yang ada.
  6. Timeout endpoint test tidak ditangkap -> sync crash setelah key masuk.
