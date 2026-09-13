# ⚡ ATF MTProto Telethon Relayer Microservice

A high-performance, stateless microservice that interfaces with Telegram via MTProto (Telethon) to provide 24/7 autonomous session refresh and WebApp token generation for the ATF Cloud Mining Ecosystem.

Hosted live on **Render**: `https://atf-mtproto-relayer.onrender.com`

---

## 🌟 Key Capabilities

- **24/7 Autonomous Token Refresh**: Simulates authentic Telegram mobile WebApp client handshake (`RequestAppWebViewRequest` / `RequestWebViewRequest`) to generate fresh `initData` without manual user intervention.
- **Stateless & Resilient**: Sessions are encrypted and persisted in Cloudflare D1; relayer handles dynamic decryption and batch token rotation on demand.
- **Two-Step Cold-Boot Wakeup**: Responds instantly to `/wake` probes to cold-boot Render free-tier containers before batch operations.
- **Two-Factor Authentication (2FA)**: Full support for Telegram Cloud Password verification (`SessionPasswordNeededError`).
- **Humanized Jitter & Rate Shield**: Jittered requests between accounts to prevent Telegram flood limits.

---

## 📡 API Endpoints

### 1. Health & Liveness Probes
- `GET /wake` — Liveness and cold-boot wakeup probe.
- `GET /health` — Health check endpoint.
- `GET /healthz` — Kubernetes/container health probe.
- `GET /` — Root service metadata.

### 2. Interactive Telegram Authentication
- `POST /auth/send-code`
  - Body: `{"phone": "+1234567890"}`
  - Sends Telegram SMS or in-app login code and returns `phone_code_hash`.
- `POST /auth/verify-code`
  - Body: `{"phone": "+1234567890", "code": "12345", "phone_code_hash": "...", "password": "optional_2fa"}`
  - Verifies code and returns a safe Telethon `StringSession`.

### 3. Automated Batch Session Refresh
- `POST /session/refresh-batch`
  - Headers: `X-Relayer-Secret: <RELAYER_API_SECRET>`
  - Body:
    ```json
    {
      "items": [
        {
          "account_id": 1,
          "session_string": "1BVtsOH..."
        }
      ]
    }
    ```
  - Returns fresh Telegram WebApp `init_data` for all authorized sessions.

---

## ⚙️ Environment Variables

| Variable | Description | Default |
| :--- | :--- | :--- |
| `PORT` | Listening HTTP port | `10000` |
| `TELEGRAM_API_ID` | Telegram App API ID from my.telegram.org | Required |
| `TELEGRAM_API_HASH` | Telegram App API Hash | Required |
| `RELAYER_API_SECRET`| Shared cluster secret for authenticated batch calls | Required |
| `DEVICE_MODEL` | Client hardware emulation string | `Samsung Galaxy A30` |
| `SYSTEM_VERSION` | Client operating system string | `Android 14` |
| `APP_VERSION` | Telegram Android version string | `11.2.2` |

---

## 🚀 Deployment (Render)

This repository includes [`render.yaml`](./render.yaml) for automated Blueprint deployment:
1. Connect this repository to your **Render** account.
2. Ensure `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, and `RELAYER_API_SECRET` are configured in the Render Dashboard.
3. Deploy changes automatically on git push to `main`.
