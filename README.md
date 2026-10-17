# <img src="assets/app-icon.svg" width="32" height="32" alt="Grabgram logo"> Grabgram

https://github.com/user-attachments/assets/62538202-f5f9-4a9b-ba7c-87be545436fe

![Python](https://img.shields.io/badge/python-3.11%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

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
2. Choose a folder and media filters, and optionally enable sequence numbering.
   Set either minimum to `0` to disable that filter.
3. New channels start **paused** while Grabgram discovers their files. Choose filenames
   in the file list, then click **Resume channel** to begin downloads.
4. Follow progress in **Queue**, **Downloaded**, **Failed**, and **Removed**.

Select files and open **Filename mode**, right-click a file, or double-click its
**Mode** cell to choose:

| Mode | Filename source |
| --- | --- |
| **Original file name (O)** | The original filename supplied by Telegram (the default). |
| **Message text (M)** | Only the message attached to the file. |
| **Replied text (R)** | Only the message this file replies to, even when it has an attached message. |
| **Custom name (C)** | Edit the filename manually, starting from the name the file currently uses. |

If the chosen message has no text or is unavailable, Grabgram uses the original
filename. Unnamed Telegram files receive a generated name such as `telegram-42.jpg`.
The file extension is kept, unsafe filename characters are cleaned, and a message ID
prevents duplicate names: for example, `Summer trip [42].mp4`.

Individual filename choices survive channel rescans. Completed files are renamed
locally; partial downloads keep their progress and sequence prefixes. Name collisions
are reported without overwriting another file. Sign in if the chosen text or original
filename has not been cached yet.
**Custom name (C)** works offline. Its editor starts with the current O, M, R, or
custom filename; the extension, sequence prefix, and message ID are kept automatically.

Use **Retry failed** for errors, **Return to queue** for removed items, and
**Redownload** to fetch a completed item again. Deleting a local file does not
make Grabgram download it again automatically. Pausing downloads keeps discovery running.

Enable **Prefix filenames with a sequence number** to number attachments from oldest
to newest. Numbers count all downloadable attachments in the channel, including files
excluded by your filters. Padding follows the total attachment count: 12 attachments
use `01`–`12`, while 150 use `001`–`150`. For example, the first file might be saved as
`001 Summer trip [42].mp4` with any filename mode.

Sequence prefixes appear in the file list after the initial channel scan finishes.
Numbered downloads also wait for that scan to finish. New attachments continue
the sequence; retries and resumed downloads keep their number and padding. Existing
downloaded files keep their names. Newly started downloads use the current total for padding.

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
