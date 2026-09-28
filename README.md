# 🎬 Televid Discord Bot (Direct Telegram Streamer)

Discord Bot Python dengan slash command `/televid` dan `/televid-info` untuk mengubah tautan pesan video Telegram (termasuk channel privat) menjadi playable video embed langsung di Discord **tanpa mengunduh video ke harddisk VPS (Zero VPS Storage)**.

Bot ini terhubung langsung ke Telegram via protokol **MTProto (Telethon)** dan mem-pipe video potongan per potongan (*in-memory chunk streaming*) melalui web server internal (`aiohttp`) yang mendukung **HTTP Range Requests (206 Partial Content)**.

---

## 🏗️ Alur Kerja Sistem

```
[ User di Discord ]
       │
       │ 1. Ketik /televid https://t.me/c/4483946044/2
       ▼
[ Televid Bot ]
       │
       │ 2. Ambil metadata video dari Telegram via MTProto
       │ 3. Reply URL streaming langsung ke chat Discord
       ▼
[ Discord Video Player ] ── 4. Request HTTP Range (206) ──▶ [ Web Server Bot ]
                                                                   │
                                                                   │ 5. Pipe chunks (RAM)
                                                                   ▼
                                                            [ Server Telegram ]
```

* **Zero Disk Usage:** Video ukuran 500 MB – 2 GB tidak pernah disimpan ke harddisk server bot. Potongan video hanya lewat di RAM lalu langsung diteruskan ke Discord player.
* **HTTP 206 Partial Content:** Player Discord bisa melakukan *seeking* (maju/mundur durasi) secara instan.

---

## 📋 Langkah Persiapan & Kredensial

### 1. Telegram API ID & Hash (Sudah Anda Miliki)
* **`TG_API_ID`**: `36608325`
* **`TG_API_HASH`**: `5959d8fbeba4ebc48ce6da42ac8d43c9`
*(Diperoleh dari https://my.telegram.org)*.

### 2. Buat Bot Telegram via @BotFather
Bot memerlukan token Telegram Bot agar bisa membaca file dari channel Anda:
1. Buka aplikasi Telegram, cari **`@BotFather`**.
2. Kirim perintah `/newbot`.
3. Masukkan nama bot dan username (misal: `MyStreamerBot`).
4. `@BotFather` akan memberikan token HTTP API (contoh: `7123456789:AAHxxxxx...`).
   👉 Simpan ke `.env` sebagai `TG_BOT_TOKEN`.
5. **PENTING (Wajib):**
   - Buka Channel Telegram tempat video Anda disimpan.
   - Buka **Channel Settings** $\rightarrow$ **Administrators** $\rightarrow$ **Add Admin**.
   - Cari username bot Telegram yang baru Anda buat, lalu tambahkan sebagai **Admin** (agar bot memiliki izin membaca pesan & file video di channel tersebut).

### 3. Buat Bot Discord
1. Buka [Discord Developer Portal](https://discord.com/developers/applications).
2. Klik **New Application** $\rightarrow$ beri nama bot.
3. Masuk ke menu **Bot** $\rightarrow$ klik **Reset Token** untuk mendapatkan `DISCORD_TOKEN`.
4. Masuk ke menu **OAuth2** $\rightarrow$ **URL Generator**:
   - Scopes: centang `bot` dan `applications.commands`.
   - Permissions: centang `Send Messages`, `Embed Links`, `Attach Files`.
5. Buka URL yang dihasilkan di browser untuk mengundang bot ke server Discord Anda.

### 4. Menentukan STREAM_BASE_URL (Akses Publik)
Karena Discord membutuhkan URL publik yang bisa diakses untuk memutar video:
* **Jika menggunakan Cloudflare Tunnel (Gratis & Sangat Mudah):**
  Jalankan perintah ini di VPS/server:
  ```bash
  cloudflared tunnel --url http://localhost:8080
  ```
  Anda akan mendapatkan URL HTTPS instan (contoh: `https://contoh-random.trycloudflare.com`).
  Masukkan URL tersebut ke `STREAM_BASE_URL`.
* **Jika memiliki domain sendiri:** Masukkan domain Anda (contoh: `https://stream.domainanda.com`).
* **Jika Kerit Cloud menyediakan IP & Port:** Masukkan format `http://nodeX.kerit.cloud:PORT`.

---

## ⚙️ Konfigurasi Environment (`.env`)

Buat file `.env` di direktori bot:

```env
# 1. Kredensial Discord
DISCORD_TOKEN=MTE5OD...
DISCORD_GUILD_ID=123456789012345678    # Server ID Anda untuk sync slash command instan

# 2. Kredensial Telegram (dari my.telegram.org)
TG_API_ID=36608325
TG_API_HASH=5959d8fbeba4ebc48ce6da42ac8d43c9

# 3. Token Bot Telegram (dari @BotFather)
TG_BOT_TOKEN=7123456789:AAHxxxxx...

# 4. Pengaturan Web Server Stream
WEB_HOST=0.0.0.0
WEB_PORT=8080

# 5. URL Publik untuk Discord
STREAM_BASE_URL=https://stream.domainanda.com
```

---

## 🚀 Cara Menjalankan Bot

### Opsi A: Menggunakan Docker Compose (Direkomendasikan)
```bash
docker compose up -d --build
```
Cek log bot:
```bash
docker compose logs -f televid-bot
```

### Opsi B: Di Panel Kerit Cloud / Pterodactyl
1. Masukkan file repo GitHub ini ke server Kerit Cloud Anda.
2. Buat file `.env` di menu **Files** dan isi variabel di atas.
3. Di tab **Startup**, pastikan file utama adalah `bot.py` atau `main.py`.
4. Di tab **Console**, klik **Start**.

---

## 🧪 Pengujian Slash Command

### 1. `/televid <link_telegram>`
Mengubah link pesan Telegram menjadi playable video stream:
```text
/televid link: https://t.me/c/4483946044/2
```
**Respon Bot:**
```text
🎬 Video siap diputar!
Nama: One_Piece_Episode_1000.mp4
Ukuran: 450.25 MB
URL: https://stream.domainanda.com/stream/4483946044/2/One_Piece_Episode_1000.mp4
```
Discord client akan langsung menampilkan pemutar video atau link card dengan tombol tonton langsung.

### 2. `/televid-info <link_telegram>`
Melihat metadata video Telegram tanpa membagikan URL streaming ke chat:
- Menampilkan Nama File, Ukuran (MB/GB), Durasi, Resolusi, Tipe Konten, dan Channel ID.
- Respon bersifat *ephemeral* (hanya dapat dilihat oleh Anda).
