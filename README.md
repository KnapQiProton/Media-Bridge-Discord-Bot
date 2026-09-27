# 🎬 Televid Discord Bot (Zero VPS Storage)

Discord Bot Python dengan slash command `/televid` dan `/televid-info` untuk mengubah tautan pesan video Telegram menjadi playable video embed langsung di Discord **tanpa mengunduh video ke server (VPS)**.

Bot hanya berperan sebagai resolver cerdas yang memetakan Telegram `message_id` ke Teldrive `file_id` dan mengirimkan direct streaming URL berformat HTTP 206 Partial Content ke Discord.

---

## 📋 Daftar Isi
1. [Arsitektur & Prinsip Kerja](#arsitektur--prinsip-kerja)
2. [Solusi HTTP Range & Autentikasi Discord](#solusi-http-range--autentikasi-discord)
3. [Setup Cloudflare Tunnel (Jika Belum Publik)](#setup-cloudflare-tunnel-jika-belum-publik)
4. [Cara Mendapatkan TELDRIVE_ACCESS_TOKEN](#cara-mendapatkan-teldrive_access_token)
5. [Cara Membuat & Invite Bot Discord](#cara-membuat--invite-bot-discord)
6. [Instalasi & Menjalankan Bot via Docker](#instalasi--menjalankan-bot-via-docker)
7. [Pengujian Slash Command](#pengujian-slash-command)
8. [Perilaku Discord & Format Video](#perilaku-discord--format-video)

---

## 🏗️ Arsitektur & Prinsip Kerja

```
[ User di Discord ]
       │
       │ 1. Ketik /televid https://t.me/c/4483946044/2
       ▼
[ Televid Bot ]
       │
       │ 2. Parse link -> message_id: 2
       │ 3. Query Postgres / Teldrive API -> dapatkan file_id
       │ 4. Reply direct URL ke channel
       ▼
[ Discord Client ] ── 5. HTTP Range Stream (206) ──▶ [ Teldrive / Telegram ]
                                                      (Zero VPS Bandwidth)
```

- **Zero VPS Storage:** Bot tidak pernah menyimpan byte video ke harddisk VPS.
- **Direct Streaming:** Discord player langsung menarik stream dari endpoint Teldrive / Reverse Proxy.

---

## ⚡ Solusi HTTP Range & Autentikasi Discord

Agar video dapat diputar langsung di Discord (inline embed):
1. **HTTP Range Requests (206 Partial Content):** Wajib didukung agar player Discord bisa melakukan seeking dan membaca header MP4 (moov atom).
2. **Bypass Cookie Auth:** Player video Discord **tidak mengirimkan cookie browser** Anda saat memutar link eksternal.

Berikut dua pilihan reverse proxy untuk menangani Range Requests dan otomatis menginjeksi token autentikasi Teldrive:

### Opsi A: Konfigurasi Nginx (Direkomendasikan)
Tambahkan blok lokasi ini di konfigurasi Nginx server Teldrive Anda (`/etc/nginx/sites-available/teldrive`):

```nginx
server {
    server_name teldrive.domainanda.com;

    # Endpoint publik khusus streaming video Discord
    location ~* ^/stream/([a-zA-Z0-9\-]+)/(.*)$ {
        # Proxy ke Teldrive internal
        proxy_pass http://127.0.0.1:8080/api/files/$1/content;
        
        # Wajib untuk Range Streaming
        proxy_http_version 1.1;
        proxy_set_header Range $http_range;
        proxy_set_header If-Range $http_if_range;
        
        # Matikan buffering agar streaming langsung diteruskan ke Discord
        proxy_buffering off;
        proxy_request_buffering off;
        
        # Injeksi cookie autentikasi Teldrive otomatis
        proxy_set_header Cookie "user-session=TELDRIVE_ACCESS_TOKEN_ANDA_DISINI";
        proxy_set_header Host $host;
    }

    # Route normal Teldrive Web UI & API
    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }
}
```

*Setel di `.env`:*
```env
PUBLIC_STREAM_URL_TEMPLATE={host}/stream/{file_id}/{filename}
```

### Opsi B: Konfigurasi Caddy
Jika Anda menggunakan Caddy:

```caddy
teldrive.domainanda.com {
    # Route streaming dengan injeksi Cookie
    @stream path_regexp stream ^/stream/([a-zA-Z0-9\-]+)/(.*)$
    handle @stream {
        rewrite * /api/files/{re.stream.1}/content
        reverse_proxy 127.0.0.1:8080 {
            header_up Cookie "user-session=TELDRIVE_ACCESS_TOKEN_ANDA_DISINI"
            header_up Range {header.Range}
            header_up If-Range {header.If-Range}
            flush_interval -1
        }
    }

    # Web UI & API normal
    handle {
        reverse_proxy 127.0.0.1:8080
    }
}
```

---

## 🌐 Setup Cloudflare Tunnel (Jika Belum Publik)

Jika Teldrive berjalan di VPS lokal/rumah atau belum memiliki domain publik:

1. **Install cloudflared di VPS:**
   ```bash
   curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
   sudo dpkg -i cloudflared.deb
   ```

2. **Login & Buat Tunnel:**
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create teldrive-tunnel
   ```

3. **Buat file konfigurasi `~/.cloudflared/config.yml`:**
   ```yaml
   tunnel: <TUNNEL_ID>
   credentials-file: /root/.cloudflared/<TUNNEL_ID>.json

   ingress:
     - hostname: teldrive.domainanda.com
       service: http://localhost:8080
       originRequest:
         noTLSVerify: true
         # Hindari timeout saat streaming chunks besar
         connectTimeout: 30s
         keepAliveTimeout: 1m
     - service: http_status:404
   ```

4. **Arahkan DNS dan Jalankan:**
   ```bash
   cloudflared tunnel route dns teldrive-tunnel teldrive.domainanda.com
   sudo cloudflared service install
   sudo systemctl start cloudflared
   ```

> [!NOTE]
> Pada dashboard Cloudflare Dashboard -> **Caching** -> **Configuration**: pastikan **Enable Cache by Device Type** atau bypass cache untuk ekstensi video agar Cloudflare tidak mencoba mem-buffer video utuh.

---

## 🔑 Cara Mendapatkan TELDRIVE_ACCESS_TOKEN

Teldrive menggunakan JWT session cookie bernama `user-session`.

1. Buka browser dan buka Web UI Teldrive Anda (`https://teldrive.domainanda.com`).
2. Login menggunakan akun Telegram Anda seperti biasa.
3. Buka **Developer Tools** (tekan `F12` atau `Ctrl + Shift + I`):
   - Klik tab **Application** (Chrome/Edge) atau **Storage** (Firefox).
   - Di panel kiri, pilih **Cookies** -> pilih domain Teldrive Anda.
   - Cari cookie dengan nama `user-session`.
   - Salin seluruh nilai string dari kolom **Value** (panjang, biasanya berformat `eyJhbGciOi...`).
4. Tempel nilai tersebut ke dalam file `.env` pada variabel `TELDRIVE_ACCESS_TOKEN`.

---

## 🤖 Cara Membuat & Invite Bot Discord

1. Buka [Discord Developer Portal](https://discord.com/developers/applications).
2. Klik **New Application**, beri nama (misal `Televid`).
3. Masuk ke menu **Bot** di panel kiri:
   - Klik **Reset Token** untuk mendapatkan token bot. Simpan ke `.env` sebagai `DISCORD_TOKEN`.
   - Di bagian **Privileged Gateway Intents**, Anda tidak memerlukan intent khusus (Message Content Intent tidak dibutuhkan karena bot menggunakan Slash Commands murni).
4. Masuk ke menu **OAuth2** -> **URL Generator**:
   - Di kotak **Scopes**, centang:
     - `bot`
     - `applications.commands`
   - Di kotak **Bot Permissions**, centang:
     - `Send Messages`
     - `Embed Links`
     - `Attach Files`
     - `Use External Emojis`
5. Salin URL yang dihasilkan di bagian bawah, buka di browser, dan pilih server Discord tujuan.

---

## 🐳 Instalasi & Menjalankan Bot via Docker

### 1. Clone & Masuk ke Folder Proyek
```bash
git clone <repository_url> televid-bot
cd televid-bot
```

### 2. Konfigurasi Lingkungan (`.env`)
Salin file template:
```bash
cp .env.example .env
```
Edit dengan nano atau vim:
```bash
nano .env
```
Isi konfigurasi Anda:
```env
DISCORD_TOKEN=MTE5...
DISCORD_GUILD_ID=123456789012345678    # Masukkan Server ID Anda untuk sync instan saat testing
TELDRIVE_API_HOST=https://teldrive.domainanda.com
TELDRIVE_ACCESS_TOKEN=eyJhbGciOi...
TELDRIVE_CHANNEL_ID=4483946044

# Sangat disarankan jika bot satu VPS dengan PostgreSQL Teldrive:
# DATABASE_URL=postgresql://postgres:password@localhost:5432/teldrive
```

### 3. Build & Jalankan Container
```bash
docker compose up -d --build
```

### 4. Periksa Log Container
```bash
docker compose logs -f televid-bot
```
Output sukses akan menampilkan:
```text
🤖 Bot berhasil login sebagai: Televid#1234
✅ Terhubung ke database PostgreSQL Teldrive.
⚡ Berhasil sinkronisasi 2 slash command ke Guild ID ...
🚀 Televid Bot siap digunakan!
```

---

## 🧪 Pengujian Slash Command

### Command 1: `/televid <link>`
Mengubah link Telegram menjadi playable stream di Discord.
```text
/televid link: https://t.me/c/4483946044/2
```
**Respon Bot:**
```text
🎬 Video siap diputar!
Nama: Sample_Movie_1080p.mp4
Ukuran: 185.40 MB
URL: https://teldrive.domainanda.com/stream/a1b2c3d4/Sample_Movie_1080p.mp4
```
Discord client akan otomatis menampilkan HTML5 Video Player dengan tombol play/pause dan seekbar.

### Command 2: `/televid-info <link>`
Melihat metadata tanpa membagikan link streaming ke chat.
```text
/televid-info link: https://t.me/c/4483946044/2
```
**Respon Bot (Ephemeral / Hanya Anda yang melihat):**
- **Nama File:** `Sample_Movie_1080p.mp4`
- **Ukuran:** `185.40 MB`
- **MIME:** `video/mp4`
- **Teldrive ID:** `a1b2c3d4-xxxx-xxxx-xxxx`
- **Parts:** `1 part(s)`

---

## ⚠️ Perilaku Discord & Format Video

1. **Batas Ukuran Embed Eksternal:**
   - Discord mampu merender inline playable player untuk video eksternal berukuran hingga **~100 MB**.
   - Jika video berukuran di atas ~100–150 MB, Discord client mungkin tidak merender player inline untuk menghemat memori, melainkan menampilkan URL card dan tombol "Tonton / Download" yang disediakan bot.
2. **Codec Video yang Didukung:**
   - Discord Desktop dan Mobile menggunakan Chromium/WebKit media stack.
   - Codec yang didukung langsung: **H.264 (AVC)** untuk video dan **AAC** untuk audio, dalam container **MP4** atau **WebM**.
   - Video dengan codec **HEVC/H.265**, **AV1**, atau audio **AC3/DTS** tidak dapat diputar langsung di Discord player (akan muncul layar hitam). Pengguna tetap bisa mengklik link untuk membuka di player eksternal (VLC/MPV).
3. **HTTP 206 Partial Content:**
   - Pastikan Reverse Proxy Anda memiliki `proxy_set_header Range $http_range;` dan `proxy_buffering off;` agar Discord dapat meminta segmen awal dan seek secara bebas tanpa lag.
