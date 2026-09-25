# Grabgram

A Python desktop application for downloading Telegram channel media, including audio, video, images, and documents. It maintains a permanent SQLite ledger, resumes partial files, prioritizes newly posted media over historical backlog, and shows per-channel queue details.

Version 2.7.2 uses a simple, responsive Tkinter interface with a clear connection header, channel summary cards, prominent download progress, status-aware media tables, dedicated empty and error states, collapsible channel details, removable channels, optional MTProto proxy support and status checking, protocol-safe bandwidth limiting, recoverable connection failures, and MTProxy secret normalization. Tkinter is included with standard Windows Python installations, so the interface has no additional GUI package dependency.

## Setup

Run these commands from the project directory. The application requires Python
3.11 or later, Tkinter, and a graphical desktop session.

1. Install Python 3.11 or later. Tkinter is normally included with Windows Python.
   On Debian/Ubuntu, install it with `sudo apt-get install python3-tk python3-venv`.
   Check Tkinter with `python -m tkinter` (or `python3 -m tkinter` on Linux).
2. Create Telegram API credentials at <https://my.telegram.org/apps>. These are account credentials for an API client, not a bot token.
3. In this directory, create and activate a virtual environment:

   **Windows (PowerShell):**

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   ```

   **Linux / macOS:**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   python -m pip install -r requirements.txt
   ```

   Dependencies include Telethon, Pillow, cryptg for faster encrypted transfers,
   and the Persian/Arabic text-rendering packages.

4. Copy `config.example.json` to `config.json`, then replace `api_id` and `api_hash`
   with credentials created for your own Telegram account. Do not use credentials
   belonging to the project maintainer or another user. As an alternative, set
   `TG_API_ID` and `TG_API_HASH` in your shell environment; these override the JSON
   credentials. A configuration file is still required, and `.env` files are not
   loaded automatically.

   Copy the example with `Copy-Item config.example.json config.json` in PowerShell
   or `cp config.example.json config.json` on Linux/macOS, then edit `config.json`.
5. Authenticate once in a terminal:

   ```powershell
   python -m grabgram.auth
   ```

   Telegram will request your phone number, login code, and two-step verification password if enabled. The resulting session stays under `data/` and must be kept private.

6. Start the desktop application:

   ```powershell
   python -m grabgram
   ```

Use **Add channel** and enter a public username such as `example`, a `https://t.me/example` link, or an accessible channel ID. Your Telegram account must already have access to private channels.

Choose the channel's folder, minimum duration, and minimum file size. Set the
minimum duration to `0` to include photos and documents without a duration.
Use the channel tabs to inspect downloads, retry failures, or return removed
items to the queue.

## Configuration

Start with [config.example.json](config.example.json):

| Setting | Default | Purpose |
| --- | --- | --- |
| `api_id` | `0` | Replace with your Telegram API ID, or set `TG_API_ID`. |
| `api_hash` | Placeholder | Replace with your Telegram API hash, or set `TG_API_HASH`. |
| `session_file` | `data/telegram` | Telegram session path; Telethon adds `.session`. |
| `database_file` | `data/downloader.sqlite3` | Download history and saved application settings. |
| `download_root` | `downloads` | Initial parent directory for channel downloads. |
| `scan_interval_seconds` | `60` | Scan interval in seconds, with a minimum of `15`. |

Relative paths are resolved against the configuration file's directory. A saving
directory selected in **Settings** overrides `download_root`.

To use a different configuration, pass the same path to authentication and startup:

```bash
python -m grabgram.auth --config /path/to/config.json
python -m grabgram --config /path/to/config.json
```

## Behavior

- Each channel has its own folder and its own minimum duration and file-size filters.
- A file must meet **both** configured minimums. Set either minimum to `0` to disable it.
- Re-adding a previously known channel reapplies its new minimums to the entire saved media ledger. Rows below either minimum are discarded regardless of status; completed files already on disk are left untouched.
- New channel posts enter at the front of the waiting queue. The file currently downloading is allowed to finish; the new post is selected next.
- Exactly one file is claimed and downloaded at a time.
- A per-database instance lock prevents a second copy of the application from starting another downloader.
- **Pause downloads** stops after the current network chunk and keeps the `.part` file. Scanning and discovery continue while paused.
- Each channel also has its own Pause/Resume control. A paused channel keeps its partial file and queue, continues scanning for new media, and is skipped by the download worker until resumed. This state is remembered after restart.
- The toolbar speed limit is expressed in KB/s, applies to the current and future downloads, and is remembered in SQLite. Use `0` for unlimited speed.
- Speed limiting delays locally yielded download chunks and does not alter Telegram's validated API request limit. Interrupted partial files are rewound by at most 4 KiB when necessary so resumed offsets remain protocol-aligned.
- Completed Telegram message IDs remain `downloaded` in SQLite even if their local files are deleted. They are never automatically downloaded again.
- Each channel has separate paged **Queue**, **Downloaded**, **Failed**, and **Removed** tabs. Their record counts appear in the tab titles.
- The filename and right-aligned percentage, transferred size, total size, and speed share one row above the progress bar.
- **Collapse details** hides the tabs, tables, and their buttons. The current-file metrics row, progress bar, and folder/minimum row remain visible, while list totals appear beside the channel name.
- Table data is revision-cached and only the selected tab is loaded. Live progress remains refreshed independently, preventing unchanged database tables from blocking Tkinter's click handling.
- Click any table column heading to sort the complete list; click it again to reverse the order.
- Queue shows the Telegram date and live status; Downloaded shows the database's completion date; only Failed shows the error column.
- **Remove selected** moves queued or failed files to the persistent Removed list. Removed files are never claimed for download; select them and choose **Return to queue** to restore them.
- To intentionally fetch one again, select it in the channel table and choose **Redownload**.
- Failed items remain visible and can be selected and retried. With no selection, **Retry failed** retries every failed item in that channel.
- **Remove channel** stops its active transfer, prevents its remaining queue from downloading, and removes the channel from the interface. Its channel record, media ledger, queue, and completed files remain intact. Only the active partial file is discarded and reset to zero, so adding the channel again restores its history and restarts that item from the beginning.
- An optional MTProto proxy can route both Telegram authentication and downloader traffic.

## Persian text on Linux

The Linux interface uses `arabic-reshaper` and `python-bidi` (installed from
`requirements.txt`) to join Persian/Arabic letters and display right-to-left
text in channel titles, media tables, and status labels. This follows the
[reshaper documentation](https://github.com/mpcabd/python-arabic-reshaper).
Stored filenames and search queries retain their original Unicode text.
Editable fields still use Tk’s native text editing and may show unjoined text
on Linux. Windows and macOS retain their native rendering.

## MTProto proxy

Open **Settings**, enable **Use MTProto proxy**, and either paste a standard `tg://proxy` / `https://t.me/proxy` link or enter its server, port, and secret manually. Hexadecimal, Base64, and URL-safe Base64 representations are accepted for standard 16-byte and `dd` random-padding secrets. Restart the application after saving. The same proxy is also used by `python -m grabgram.auth`, so save it before authenticating if Telegram is not directly reachable.

Telethon 1.x does not implement the extra Fake-TLS handshake used by genuine variable-length `ee` secrets. The application detects those secrets and reports this limitation directly instead of attempting an incompatible MTProto handshake. Such a proxy needs to provide a standard/`dd` secret, or be used through an official Telegram client with Fake-TLS support.

Use **Check status** to test the proxy values currently shown in the dialog without saving them. The check runs in the background with a 15-second timeout, establishes a temporary in-memory Telegram connection, reports its handshake time, and disconnects immediately. It does not use or modify the account session, and proxy secrets are removed from displayed errors.

The proxy secret is masked in the interface and omitted from application messages. It is stored locally in `data/downloader.sqlite3`, so protect that database as you would the Telegram session. Leaving the secret empty uses Telethon's no-secret value of 32 zeroes. Disable the checkbox and restart to return to a direct connection.

Connection failures are displayed in a non-modal error banner. Settings and Exit remain available; restart the application after changing connection settings.

## Measuring UI lag

Run a representative workload with UI measurement enabled, keep the window visible, and interact
with it normally. Close the application cleanly to write the report:

```powershell
python -m grabgram --ui-performance-report data/ui-baseline.json --ui-performance-label baseline
```

For statistically useful tail measurements, run for at least five minutes. After making a
performance change, repeat the same workload (selected channel, download activity, and speed
limit) for approximately the same duration:

```powershell
python -m grabgram --ui-performance-report data/ui-next.json --ui-performance-label next
```

Compare the two reports:

```powershell
python -m grabgram.ui_performance data/ui-baseline.json data/ui-next.json
```

Stalls per minute and estimated UI-delayed percentage are normalized for unequal run lengths.
Event-loop tail percentiles describe freeze severity, while poll-phase percentiles show whether
time was spent draining events, refreshing channel cards, calculating queue positions, or
refreshing the selected panel. Avoid minimizing the application or putting Windows to sleep
during a measurement because either will appear as event-loop lag.

## Files and recovery

- Downloads default to `downloads/<channel folder>/`. Change the parent folder in
  **Settings → Saving Directory**, then restart the application. This applies to
  all channels and is remembered across restarts. Unfinished downloads reset to zero and start again in the new location.
  Existing files are not moved. Proxy controls are
  grouped under **Settings → Proxy**.
- Persistent history is stored at `data/downloader.sqlite3`.
- Telegram authentication is stored in `data/telegram.session`.
- An interrupted file remains beside its target with a `.part` suffix and resumes on the next run.
- Back up the SQLite database if the "never download this message again" history is important.

## Security and privacy

This application runs locally and has no telemetry. It does not provide shared
Telegram API credentials: every user must create and supply their own `api_id`
and `api_hash`. The maintainer therefore does not receive users' authentication
data or proxy settings and cannot control or assume responsibility for their
Telegram API usage.

`config.json`, the Telethon session, the SQLite database, proxy secret, download
folders, partial files, and `.env` files are excluded from Git. The session is a
reusable authorization credential; anyone who obtains it may be able to access
the Telegram account without another login code. These values are stored locally
without application-level encryption, so keep the Windows account and disk
secure.

## Responsible use

Users are responsible for complying with Telegram's [Terms of Service](https://telegram.org/tos)
and [API Terms](https://core.telegram.org/api/terms), respecting API limits,
securing their credentials, and downloading only media they are authorized to
access and copy. Do not use this software for spam,
flooding, access-control bypass, copyright infringement, or other abusive or
unlawful activity. Telegram may restrict or ban accounts or API clients used in
violation of its rules.

This project is not affiliated with or endorsed by Telegram. It is distributed
without warranty under the [MIT License](LICENSE).

## Tests

Run the unittest suite from the project directory with the dependencies installed:

```powershell
python -m unittest discover -s tests -v
```

## Project layout

```text
grabgram/            Desktop UI, authentication, download engine, and persistence
tests/              Unit tests for downloads, storage, proxies, and UI helpers
assets/             Application icons
config.example.json Configuration template without account credentials
requirements.txt    Pinned Python dependencies
data/               Local session and database (created at runtime; ignored by Git)
downloads/          Default download directory (created at runtime; ignored by Git)
```
