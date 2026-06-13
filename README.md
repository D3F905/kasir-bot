# Kasir Bot — Setup Guide

Bot Telegram untuk catat invoice otomatis pakai AI.

## Yang Lo Butuhkan (semua gratis)

1. **Telegram Bot Token** — dari @BotFather di Telegram
2. **Gemini API Key** — dari aistudio.google.com (gratis)
3. **Akun Railway** — dari railway.app (gratis $5/bulan)
4. **Akun GitHub** — untuk deploy ke Railway

---

## Langkah 1 — Buat Bot Telegram

1. Buka Telegram, cari **@BotFather**
2. Ketik `/newbot`
3. Kasih nama bot: misalnya `Kasir Gw`
4. Kasih username: misalnya `kasirgw_bot`
5. Copy **token**-nya — bentuknya: `7123456789:AAF...`

---

## Langkah 2 — Dapetin Gemini API Key (Gratis)

1. Buka **aistudio.google.com**
2. Login pakai Google
3. Klik **"Get API Key"** → **"Create API Key"**
4. Copy key-nya

---

## Langkah 3 — Upload ke GitHub

1. Buat akun di **github.com** (kalau belum punya)
2. Buat repository baru, nama bebas misal `kasir-bot`
3. Upload semua file ini ke repo tersebut:
   - `bot.py`
   - `requirements.txt`
   - `Procfile`

---

## Langkah 4 — Deploy ke Railway

1. Buka **railway.app** → Sign in with GitHub
2. Klik **"New Project"** → **"Deploy from GitHub repo"**
3. Pilih repo `kasir-bot`
4. Setelah deploy, klik tab **"Variables"**, tambahkan:

| Key | Value |
|-----|-------|
| `BOT_TOKEN` | token dari BotFather |
| `GEMINI_API_KEY` | key dari Google AI Studio |
| `ALLOWED_USER_ID` | (opsional) ID Telegram lo — biar bot private |

5. Klik **"Deploy"**

> Cara cari ALLOWED_USER_ID: chat @userinfobot di Telegram, dia langsung kasih ID lo.

---

## Cara Pakai Bot

Buka bot lo di Telegram, ketik `/start`

| Aksi | Cara |
|------|------|
| Catat invoice | Kirim foto invoice |
| Laporan hari ini | Ketik `/hari` atau tekan tombol |
| Laporan bulan ini | Ketik `/bulan` |
| Laporan tahun ini | Ketik `/tahun` |
| Tagihan belum dibayar | Ketik `/pending` |
| Hapus input terakhir | Ketik `/hapus` |

---

## Estimasi Biaya

- **Railway**: Gratis sampai $5/bulan (lebih dari cukup buat bot personal)
- **Gemini AI**: Gratis (free tier cukup buat ratusan invoice/bulan)
- **Total: Rp 0/bulan** untuk pemakaian normal
