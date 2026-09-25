# 📥 Grabgram

Save Telegram videos, audio, photos, and documents with a desktop app that remembers your downloads.

## ✨ Features

- 🔄 Resume interrupted downloads and prioritize new posts.
- 📂 Separate channel folders, media filters, and download history.
- ⏯️ Pause all downloads or individual channels; set a speed limit.
- 📋 Browse queued, downloaded, failed, and removed files.
- 🌐 MTProto proxy support and Persian/Arabic text display.

## 🚀 Quick start

Requires **Python 3.11+**, **Tkinter**, and a graphical desktop. On Debian/Ubuntu,
install Tkinter and venv support with `sudo apt-get install python3-tk python3-venv`.

Run these commands from the project directory.

**Linux / macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp config.example.json config.json
```

**Windows (PowerShell)**

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item config.example.json config.json
```

Get your own API credentials from [Telegram](https://my.telegram.org/apps), then
set `api_id` and `api_hash` in `config.json`. These are client credentials, not a bot token.

Sign in, then launch:

```bash
python -m grabgram.auth
python -m grabgram
```

Telegram asks for your phone number, login code, and two-step verification password if enabled.

## 🎯 Using Grabgram

1. Click **Add channel** and enter a username, `https://t.me/example` link, or accessible channel ID.
2. Choose a folder and media filters. Set either minimum to `0` to disable that filter.
3. Follow progress in **Queue**, **Downloaded**, **Failed**, and **Removed**.

Use **Retry failed** for errors, **Return to queue** for removed items, and
**Redownload** to fetch a completed item again. Deleting a local file does not
make Grabgram download it again automatically. Pausing downloads keeps discovery running.

## ⚙️ Settings

- **Saving Directory:** choose the parent download folder, then restart. Existing files
  stay where they are; unfinished downloads restart in the new location.
- **Proxy:** enable MTProto and paste a proxy link or enter the details. Use **Check status**,
  then save and restart. Standard and `dd` secrets are supported; Fake-TLS `ee` secrets are not.
  If Telegram requires a proxy, save it in the app before running authentication.
- **Speed limit:** applies immediately; `0` means unlimited.

See [config.example.json](config.example.json) for file paths and the scan interval.
Relative paths resolve from the configuration file's directory. Use `--config PATH`
with both launch and authentication commands for a custom configuration.
`TG_API_ID` and `TG_API_HASH` override JSON credentials; the configuration file is
still required, and `.env` files are not loaded automatically.

## 🔒 Local data

Downloads default to `downloads/`. Login sessions, settings, and download history
live in `data/`. Back up that folder to preserve history, and keep it and `config.json`
private: the session grants access to your Telegram account. Local credentials and
state are ignored by Git and are not encrypted by the app.

## 🧪 Development

Code lives in `grabgram/`, tests in `tests/`, and icons in `assets/`.

```bash
python -m unittest discover -s tests -v
```

## 📜 License

[MIT](LICENSE). Not affiliated with Telegram. Download only content you are authorized
to access and copy, and follow Telegram's [Terms](https://telegram.org/tos) and
[API Terms](https://core.telegram.org/api/terms).
