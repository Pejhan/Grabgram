from __future__ import annotations

import asyncio
import math
import queue
import re
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from typing import Any

try:
    from .config import Config
    from .database import Channel, Database
    from .engine import DownloaderEngine
    from .media import format_bytes
    from .proxy import (
        MtProtoProxy, ensure_telethon_compatible, load_mtproto_proxy, parse_proxy_link,
        save_mtproto_proxy,
    )
    from .proxy_check import check_mtproto_proxy, sanitized_proxy_error
except ImportError:
    from config import Config
    from database import Channel, Database
    from engine import DownloaderEngine
    from media import format_bytes
    from proxy import (
        MtProtoProxy, ensure_telethon_compatible, load_mtproto_proxy, parse_proxy_link,
        save_mtproto_proxy,
    )
    from proxy_check import check_mtproto_proxy, sanitized_proxy_error


ICON_NAMES = (
    "add", "collapse", "expand", "failed", "pause",
    "play", "queue", "refresh", "settings", "success",
    "previous", "next", "remove",
)


def load_icons(master: tk.Misc) -> dict[str, tk.PhotoImage]:
    icon_directory = Path(__file__).resolve().parent.parent / "assets" / "png"
    icons: dict[str, tk.PhotoImage] = {}
    for name in ICON_NAMES:
        try:
            icons[name] = tk.PhotoImage(master=master, file=str(icon_directory / f"{name}.png"))
        except tk.TclError:
            pass
    return icons


class ToolTip:
    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self.show_job: str | None = None
        self.window: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text

    def _schedule(self, _event: Any = None) -> None:
        self.hide()
        self.show_job = self.widget.after(450, self.show)

    def show(self) -> None:
        self.show_job = None
        if self.window is not None or not self.widget.winfo_exists():
            return
        self.window = tk.Toplevel(self.widget)
        self.window.wm_overrideredirect(True)
        self.window.wm_geometry(f"+{self.widget.winfo_rootx() + 4}+{self.widget.winfo_rooty() + self.widget.winfo_height() + 5}")
        tk.Label(
            self.window, text=self.text, background="#fffce8", foreground="#222222",
            relief="solid", borderwidth=1, padx=7, pady=4, font=("Segoe UI", 9),
        ).pack()

    def hide(self, _event: Any = None) -> None:
        if self.show_job is not None:
            self.widget.after_cancel(self.show_job)
            self.show_job = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


class IconButton(ttk.Button):
    def __init__(
        self, parent: tk.Misc, icon_name: str, tooltip: str,
        command: Any, **options: Any,
    ):
        self.icons = getattr(parent.winfo_toplevel(), "icons", {})
        image = self.icons.get(icon_name)
        if image is not None:
            options.update(image=image, style="Icon.TButton")
        else:
            options.update(text=tooltip)
        super().__init__(parent, command=command, **options)
        self.tooltip = ToolTip(self, tooltip)

    def set_icon(self, icon_name: str, tooltip: str) -> None:
        image = self.icons.get(icon_name)
        self.configure(image=image, text="" if image is not None else tooltip)
        self.tooltip.set_text(tooltip)


def suggested_channel_folder(identifier: str) -> str:
    value = identifier.strip().split("?", 1)[0].rstrip("/")
    if "t.me/" in value.lower():
        value = value.rsplit("/", 1)[-1]
    value = value.lstrip("@").strip()
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")


class FileTypeDialog(tk.Toplevel):
    def __init__(
        self, parent: tk.Misc, file_types: list[tuple[str, int]],
        selected: tuple[str, ...], on_apply: Any,
    ):
        super().__init__(parent)
        self.parent_dialog = parent
        self.file_types = file_types
        self.on_apply = on_apply
        self.title("Select file types")
        self.resizable(False, True)
        self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", self.close)

        ttk.Label(
            self, text="Select one or more file types. Most common types appear first.",
        ).pack(anchor="w", padx=12, pady=(12, 7))
        self.listbox = tk.Listbox(
            self, selectmode="multiple", exportselection=False,
            width=38, height=min(12, max(4, len(file_types))),
        )
        self.listbox.pack(fill="both", expand=True, padx=12)
        selected_set = set(selected)
        for index, (extension, count) in enumerate(file_types):
            self.listbox.insert("end", f"{extension}    {count:,} files")
            if not selected_set or extension in selected_set:
                self.listbox.selection_set(index)

        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=12, pady=12)
        ttk.Button(actions, text="Cancel", command=self.close).pack(side="right", padx=(8, 0))
        ttk.Button(actions, text="Use selected", command=self.apply).pack(side="right")
        self.grab_set()
        self.listbox.focus_set()

    def apply(self) -> None:
        indexes = self.listbox.curselection()
        if not indexes:
            messagebox.showwarning("No file types", "Select at least one file type.", parent=self)
            return
        self.on_apply(tuple(self.file_types[index][0] for index in indexes))
        self.close()

    def close(self) -> None:
        self.grab_release()
        self.destroy()
        if self.parent_dialog.winfo_exists():
            self.parent_dialog.grab_set()


class AddChannelDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, engine: DownloaderEngine):
        super().__init__(parent)
        self.engine = engine
        self.title("Add channel")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.columnconfigure(1, weight=1)
        self.identifier = tk.StringVar()
        self.folder = tk.StringVar()
        self.duration = tk.StringVar(value="1")
        self.size = tk.StringVar(value="2")
        self.file_types_text = tk.StringVar(value="All file types")
        self.selected_file_types: tuple[str, ...] = ()
        self.available_file_types: list[tuple[str, int]] = []
        self.inspect_request_id: str | None = None
        self.folder_edited = False
        self.setting_folder = False

        ttk.Label(self, text="Channel username, link, or ID:").grid(row=0, column=0, sticky="w", padx=12, pady=7)
        self.identifier_entry = ttk.Entry(self, textvariable=self.identifier, width=42)
        self.identifier_entry.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(0, 12), pady=7)
        ttk.Label(self, text="Download folder:").grid(row=1, column=0, sticky="w", padx=12, pady=7)
        ttk.Entry(self, textvariable=self.folder, width=42).grid(
            row=1, column=1, columnspan=2, sticky="ew", padx=(0, 12), pady=7,
        )
        ttk.Label(self, text="File types:").grid(row=2, column=0, sticky="w", padx=12, pady=7)
        ttk.Entry(self, textvariable=self.file_types_text, state="readonly", width=36).grid(
            row=2, column=1, sticky="ew", pady=7,
        )
        self.refresh_types_button = IconButton(
            self, "refresh", "Fetch file types", command=self.refresh_file_types,
        )
        self.refresh_types_button.grid(row=2, column=2, padx=(6, 12), pady=7)
        ttk.Label(self, text="Minimum length (minutes):").grid(row=3, column=0, sticky="w", padx=12, pady=7)
        ttk.Combobox(
            self, textvariable=self.duration, values=("1", "2", "5", "10", "60"),
            state="readonly", width=12,
        ).grid(row=3, column=1, sticky="w", pady=7)
        ttk.Label(self, text="Minimum size (MB):").grid(row=4, column=0, sticky="w", padx=12, pady=7)
        ttk.Combobox(
            self, textvariable=self.size, values=("2", "10", "50", "100"),
            state="readonly", width=12,
        ).grid(row=4, column=1, sticky="w", pady=7)

        self.identifier.trace_add("write", self._identifier_changed)
        self.folder.trace_add("write", self._folder_changed)
        actions = ttk.Frame(self)
        actions.grid(row=5, column=0, columnspan=3, sticky="e", padx=12, pady=12)
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(actions, text="Add channel", command=self.submit).pack(side="right")
        self.bind("<Return>", lambda _event: self.submit())
        self.wait_visibility()
        self.identifier_entry.focus_set()

    def _identifier_changed(self, *_args: Any) -> None:
        if self.folder_edited:
            return
        self.setting_folder = True
        self.folder.set(suggested_channel_folder(self.identifier.get()))
        self.setting_folder = False

    def _folder_changed(self, *_args: Any) -> None:
        if not self.setting_folder:
            self.folder_edited = True

    def refresh_file_types(self) -> None:
        identifier = self.identifier.get().strip()
        if not identifier:
            messagebox.showwarning("Missing channel", "Enter a Telegram channel first.", parent=self)
            return
        request_id = f"{id(self)}-{time.monotonic_ns()}"
        self.inspect_request_id = request_id
        self.file_types_text.set("Scanning channel…")
        self.refresh_types_button.configure(state="disabled")
        if not self.engine.command("inspect_channel_types", identifier=identifier, request_id=request_id):
            self.refresh_types_button.configure(state="normal")
            self.file_types_text.set("All file types")
            messagebox.showwarning("Not connected", "The downloader is not connected yet.", parent=self)

    def receive_file_types(self, data: dict[str, Any]) -> None:
        if data.get("request_id") != self.inspect_request_id:
            return
        self.inspect_request_id = None
        self.refresh_types_button.configure(state="normal")
        if data.get("error"):
            self.file_types_text.set("All file types" if not self.selected_file_types else ", ".join(self.selected_file_types))
            messagebox.showwarning("File type scan failed", str(data["error"]), parent=self)
            return
        username = str(data.get("username", "")).strip()
        if username and not self.folder_edited:
            self.setting_folder = True
            self.folder.set(username)
            self.setting_folder = False
        self.available_file_types = [
            (str(item["extension"]), int(item["count"])) for item in data.get("file_types", [])
        ]
        if not self.available_file_types:
            self.file_types_text.set("No downloadable file types found")
            messagebox.showinfo("File types", "No downloadable files were found in this channel.", parent=self)
            return
        FileTypeDialog(
            self, self.available_file_types, self.selected_file_types, self.apply_file_types,
        )

    def apply_file_types(self, selected: tuple[str, ...]) -> None:
        self.selected_file_types = selected
        self.file_types_text.set(", ".join(selected))

    def submit(self) -> None:
        identifier = self.identifier.get().strip()
        if not identifier:
            messagebox.showwarning("Missing channel", "Enter a Telegram channel.", parent=self)
            return
        try:
            duration = max(0, round(float(self.duration.get()) * 60))
            size = max(0, round(float(self.size.get()) * 1024 * 1024))
        except ValueError:
            messagebox.showwarning("Invalid minimum", "Minimum length and size must be numbers.", parent=self)
            return
        # Let the engine use Telegram's resolved username unless the user chose
        # a custom folder (for example, by appending "_audio" or "_video").
        folder = self.folder.get().strip() if self.folder_edited else ""
        self.engine.command(
            "add_channel", identifier=identifier, folder=folder,
            min_duration_seconds=duration, min_size_bytes=size,
            media_types=self.selected_file_types,
        )
        self.destroy()


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, db: Database, config: Config):
        super().__init__(parent)
        self.db = db
        self.config = config
        self.original = load_mtproto_proxy(db)
        self.check_results: queue.Queue[tuple[bool, str]] = queue.Queue()
        self.title("Connection settings")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.enabled = tk.BooleanVar(value=self.original.enabled)
        self.link = tk.StringVar()
        self.host = tk.StringVar(value=self.original.host)
        self.port = tk.IntVar(value=self.original.port)
        self.secret = tk.StringVar(value=self.original.secret)
        self.show_secret = tk.BooleanVar()

        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text="Use an MTProto proxy for Telegram sign-in and media downloads.",
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Checkbutton(
            frame, text="Use MTProto proxy", variable=self.enabled, command=self.update_state,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 10))
        ttk.Label(frame, text="Proxy link:").grid(row=2, column=0, sticky="w", pady=5)
        ttk.Entry(frame, textvariable=self.link, width=48).grid(row=2, column=1, sticky="ew", pady=5)
        ttk.Button(frame, text="Import link", command=self.import_link).grid(row=2, column=2, padx=(8, 0), pady=5)
        ttk.Label(frame, text="Server:").grid(row=3, column=0, sticky="w", pady=5)
        self.host_entry = ttk.Entry(frame, textvariable=self.host, width=48)
        self.host_entry.grid(row=3, column=1, columnspan=2, sticky="ew", pady=5)
        ttk.Label(frame, text="Port:").grid(row=4, column=0, sticky="w", pady=5)
        self.port_entry = ttk.Spinbox(frame, textvariable=self.port, from_=1, to=65535, width=12)
        self.port_entry.grid(row=4, column=1, sticky="w", pady=5)
        ttk.Label(frame, text="Secret (hex or Base64):").grid(row=5, column=0, sticky="w", pady=5)
        secret_row = ttk.Frame(frame)
        secret_row.grid(row=5, column=1, columnspan=2, sticky="ew", pady=5)
        secret_row.columnconfigure(0, weight=1)
        self.secret_entry = ttk.Entry(secret_row, textvariable=self.secret, show="*")
        self.secret_entry.grid(row=0, column=0, sticky="ew")
        self.show_check = ttk.Checkbutton(
            secret_row, text="Show", variable=self.show_secret, command=self.toggle_secret,
        )
        self.show_check.grid(row=0, column=1, padx=(8, 0))
        self.check_status_text = tk.StringVar(value="Status has not been checked.")
        self.check_status_label = ttk.Label(frame, textvariable=self.check_status_text, wraplength=560)
        self.check_status_label.grid(row=6, column=0, columnspan=3, sticky="w", pady=(10, 2))
        ttk.Label(
            frame, text="Saved changes take effect after an application restart.",
            foreground="#8a5a00",
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(4, 4))
        actions = ttk.Frame(frame)
        actions.grid(row=8, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        self.check_button = ttk.Button(actions, text="Check status", command=self.check_status)
        self.check_button.pack(side="left")
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(actions, text="Save", command=self.save).pack(side="right")
        frame.columnconfigure(1, weight=1)
        self.update_state()

    def update_state(self) -> None:
        state = "normal" if self.enabled.get() else "disabled"
        for widget in (self.host_entry, self.port_entry, self.secret_entry, self.show_check, self.check_button):
            widget.configure(state=state)

    def toggle_secret(self) -> None:
        self.secret_entry.configure(show="" if self.show_secret.get() else "*")

    def import_link(self) -> None:
        try:
            proxy = parse_proxy_link(self.link.get())
        except ValueError as exc:
            messagebox.showwarning("Invalid proxy link", str(exc), parent=self)
            return
        self.enabled.set(True)
        self.host.set(proxy.host)
        self.port.set(proxy.port)
        self.secret.set(proxy.secret)
        self.update_state()

    def current_proxy(self) -> MtProtoProxy:
        proxy = MtProtoProxy(
            self.enabled.get(), self.host.get(), int(self.port.get()), self.secret.get(),
        ).validated()
        ensure_telethon_compatible(proxy)
        return proxy

    def check_status(self) -> None:
        try:
            proxy = self.current_proxy()
        except (ValueError, tk.TclError) as exc:
            self.check_status_text.set(f"Cannot check: {exc}")
            self.check_status_label.configure(foreground="#a00000")
            return
        self.check_button.configure(state="disabled")
        self.check_status_text.set(f"Checking {proxy.host}:{proxy.port} through Telegram…")
        self.check_status_label.configure(foreground="#555555")
        threading.Thread(target=self._run_status_check, args=(proxy,), daemon=True).start()
        self.after(100, self._poll_status_check)

    def _run_status_check(self, proxy: MtProtoProxy) -> None:
        try:
            result = asyncio.run(check_mtproto_proxy(self.config, proxy))
            milliseconds = max(1, round(result.elapsed_seconds * 1000))
            self.check_results.put((True, f"Working: connected to Telegram in {milliseconds} ms."))
        except Exception as exc:
            self.check_results.put((False, f"Not working: {sanitized_proxy_error(exc, proxy)}"))

    def _poll_status_check(self) -> None:
        try:
            success, text = self.check_results.get_nowait()
        except queue.Empty:
            if self.winfo_exists():
                self.after(100, self._poll_status_check)
            return
        self.check_status_text.set(text)
        self.check_status_label.configure(foreground="#167044" if success else "#a00000")
        if self.enabled.get():
            self.check_button.configure(state="normal")

    def save(self) -> None:
        try:
            proxy = self.current_proxy()
            save_mtproto_proxy(self.db, proxy)
        except (ValueError, tk.TclError) as exc:
            messagebox.showwarning("Invalid proxy settings", str(exc), parent=self)
            return
        changed = proxy != self.original
        self.destroy()
        if changed:
            messagebox.showinfo(
                "Connection settings saved",
                "Proxy settings were saved. Restart the application to use them.",
                parent=self.master,
            )


class MediaTable(ttk.Frame):
    COLUMN_DEFINITIONS = {
        "name": ("File name", 390),
        "duration": ("Duration", 85),
        "size": ("Size", 95),
        "status": ("Status", 145),
        "date_added": ("Date added", 155),
        "date_downloaded": ("Date downloaded", 155),
        "error": ("Error", 330),
    }

    def __init__(self, parent: tk.Misc, columns: tuple[str, ...]):
        super().__init__(parent)
        self.columns = columns
        self.tree = ttk.Treeview(self, columns=columns, show="headings", height=10, selectmode="extended")
        for column in columns:
            label, width = self.COLUMN_DEFINITIONS[column]
            self.tree.heading(column, text=label)
            self.tree.column(column, width=width, minwidth=60, stretch=column in {"name", "error"})
        yscroll = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.signature: tuple[Any, ...] | None = None

    def selected_ids(self) -> list[int]:
        return [int(item) for item in self.tree.selection()]

    def populate(self, rows: list[Any], positions: dict[int, int], channel_paused: bool = False) -> None:
        signature = tuple(
            (
                int(row["id"]), row["status"], int(row["bytes_downloaded"]), row["error"],
                row["message_date"], row["downloaded_at"],
                positions.get(int(row["id"])) if row["status"] == "queued" and not channel_paused else None,
                channel_paused,
            )
            for row in rows
        )
        if signature == self.signature:
            return
        self.signature = signature
        self.tree.delete(*self.tree.get_children())
        for row in rows:
            media_id = int(row["id"])
            status = row["status"]
            size = int(row["size_bytes"])
            done = int(row["bytes_downloaded"])
            duration = int(row["duration_seconds"])
            if status == "queued" and channel_paused:
                display_status = "Paused"
            elif status == "queued":
                display_status = f"Queued (position {positions.get(media_id, '?')})"
            elif status == "downloading":
                percent = min(100, round(done * 100 / size)) if size else 0
                display_status = f"Paused ({percent}%)" if channel_paused else f"Downloading ({percent}%)"
            else:
                display_status = status.capitalize()
            values = {
                "name": row["file_name"],
                "duration": f"{duration // 60:02d}:{duration % 60:02d}",
                "size": format_bytes(size),
                "status": display_status,
                "date_added": (row["message_date"] or "").replace("T", " ")[:19],
                "date_downloaded": (row["downloaded_at"] or "").replace("T", " ")[:19],
                "error": row["error"] or "",
            }
            self.tree.insert("", "end", iid=str(media_id), values=tuple(values[column] for column in self.columns))


class BlueProgressBar(tk.Canvas):
    """Compact determinate progress bar using the application's blue palette."""

    TRACK = "#eaf3fb"
    BORDER = "#bad7ee"
    FILL = "#82bdf0"

    def __init__(self, parent: tk.Misc):
        background = parent.winfo_toplevel().cget("background")
        super().__init__(
            parent, height=14, background=background,
            highlightthickness=0, borderwidth=0,
        )
        self.value = 0.0
        self.bind("<Configure>", self._redraw)

    def set_value(self, value: float) -> None:
        self.value = min(100.0, max(0.0, float(value)))
        self._redraw()

    def _redraw(self, _event: Any = None) -> None:
        width = max(2, self.winfo_width())
        height = max(4, self.winfo_height())
        self.delete("progress")
        self.create_rectangle(
            1, 1, width - 1, height - 1, fill=self.TRACK, outline=self.BORDER, tags="progress",
        )
        fill_right = 1 + round((width - 2) * self.value / 100)
        if fill_right <= 1:
            return
        self.create_rectangle(1, 1, fill_right, height - 1, fill=self.FILL, outline="", tags="progress")


class ChannelPanel(ttk.LabelFrame):
    PAGE_SIZES = {"queue": 200, "downloaded": 100, "failed": 100}

    def __init__(self, parent: tk.Misc, channel: Channel, db: Database, engine: DownloaderEngine):
        super().__init__(parent, text=channel.title, padding=10)
        self.channel, self.db, self.engine = channel, db, engine
        self.page_numbers = {"queue": 0, "downloaded": 0, "failed": 0}
        self.tables: dict[str, MediaTable] = {}
        self.page_labels: dict[str, tk.StringVar] = {}
        self.tab_indexes: dict[str, int] = {}
        self.table_revisions = {"queue": -1, "downloaded": -1, "failed": -1}
        self.count_revision = -1
        self.cached_counts = {"queue": 0, "downloaded": 0, "failed": 0}
        self.cached_positions: dict[int, int] = {}
        self.last_active_id: int | None = None
        self.last_bytes = 0
        self.last_time = time.monotonic()
        self.collapsed = False
        self.current = tk.StringVar(value="Current file: Waiting for next file")
        self.progress_text = tk.StringVar(value="0%")
        self.stats = tk.StringVar()
        self.icons = getattr(parent.winfo_toplevel(), "icons", {})

        header = ttk.Frame(self)
        header.pack(fill="x", pady=(0, 5))
        self.collapse_button = IconButton(
            header, "collapse", "Collapse details", command=self.toggle_details,
        )
        self.collapse_button.pack(side="right")
        self.channel_pause_button = IconButton(
            header, "play" if channel.paused else "pause",
            "Resume channel" if channel.paused else "Pause channel",
            command=self.toggle_channel_pause,
        )
        self.channel_pause_button.pack(side="right", padx=(0, 6))
        self.progress = BlueProgressBar(header)
        self.progress.pack(side="left", fill="x", expand=True, padx=(0, 8))

        self.details = ttk.Frame(self)
        self.details.pack(fill="both", expand=True)
        current_row = ttk.Frame(self.details)
        current_row.pack(fill="x", pady=(0, 5))
        current_row.columnconfigure(0, weight=1)
        ttk.Label(current_row, textvariable=self.current, width=1, anchor="w").grid(row=0, column=0, sticky="ew")
        ttk.Label(current_row, textvariable=self.progress_text, anchor="e").grid(
            row=0, column=1, sticky="e", padx=(12, 0),
        )
        ttk.Label(self.details, textvariable=self.stats).pack(fill="x", pady=(0, 7))
        self.notebook = ttk.Notebook(self.details)
        self.notebook.pack(fill="both", expand=True)
        self.make_tab("queue", ("name", "duration", "size", "status", "date_added"))
        self.make_tab("downloaded", ("name", "duration", "size", "date_downloaded"))
        self.make_tab("failed", ("name", "duration", "size", "date_added", "error"))
        self.notebook.bind("<<NotebookTabChanged>>", self.tab_changed)

    def make_tab(self, kind: str, columns: tuple[str, ...]) -> None:
        page = ttk.Frame(self.notebook, padding=5)
        self.tab_indexes[kind] = len(self.tab_indexes)
        icon_name = "success" if kind == "downloaded" else kind
        self.notebook.add(
            page, text=kind.capitalize(), image=self.icons.get(icon_name), compound="left",
        )
        table = MediaTable(page, columns)
        self.tables[kind] = table
        table.pack(fill="both", expand=True)
        controls = ttk.Frame(page)
        controls.pack(fill="x", pady=(6, 0))
        if kind == "downloaded":
            ttk.Button(controls, text="Redownload selected", command=self.redownload).pack(side="left")
        elif kind == "failed":
            ttk.Button(controls, text="Retry failed", command=self.retry_failed).pack(side="left")
        else:
            IconButton(
                controls, "refresh", "Check for new files", command=self.rescan,
            ).pack(side="left")
            IconButton(
                controls, "remove", "Remove channel", command=self.remove_channel,
            ).pack(side="left", padx=(6, 0))
        label = tk.StringVar(value="Page 1 of 1")
        self.page_labels[kind] = label
        IconButton(
            controls, "next", "Next page",
            command=lambda selected=kind: self.change_page(selected, 1),
        ).pack(side="right")
        ttk.Label(controls, textvariable=label).pack(side="right", padx=8)
        IconButton(
            controls, "previous", "Previous page",
            command=lambda selected=kind: self.change_page(selected, -1),
        ).pack(side="right")

    @staticmethod
    def pages(count: int, page_size: int) -> int:
        return max(1, math.ceil(count / page_size))

    def toggle_details(self) -> None:
        self.collapsed = not self.collapsed
        if self.collapsed:
            self.notebook.pack_forget()
            self.collapse_button.set_icon("expand", "Expand details")
        else:
            self.notebook.pack(fill="both", expand=True)
            self.collapse_button.set_icon("collapse", "Collapse details")
            self.table_revisions[self.selected_tab()] = -1
        self.refresh(self.cached_positions)

    def selected_tab(self) -> str:
        selected_index = self.notebook.index(self.notebook.select())
        return next(kind for kind, index in self.tab_indexes.items() if index == selected_index)

    def tab_changed(self, _event: Any = None) -> None:
        kind = self.selected_tab()
        self.table_revisions[kind] = -1
        self.refresh(self.cached_positions)

    def rescan(self) -> None:
        self.engine.command("rescan", channel_id=self.channel.id)

    def toggle_channel_pause(self) -> None:
        paused = not self.channel.paused
        self.db.set_channel_paused(self.channel.id, paused)
        self.channel = next(channel for channel in self.db.channels() if channel.id == self.channel.id)
        self.engine.command("set_channel_paused", channel_id=self.channel.id, paused=paused)
        self.channel_pause_button.set_icon(
            "play" if paused else "pause",
            "Resume channel" if paused else "Pause channel",
        )
        self.refresh(self.cached_positions)

    def remove_channel(self) -> None:
        if messagebox.askyesno(
            "Remove channel",
            f"Remove {self.channel.title} from the channel list?\n\n"
            "Its database record, history, queue, and completed files will remain. "
            "Any file currently downloading will be discarded and reset to 0%.\n\n"
            "Adding the channel again will restore it.",
            parent=self,
        ):
            self.db.set_channel_enabled(self.channel.id, False)
            self.engine.command("remove_channel", channel_id=self.channel.id)
            app = self.winfo_toplevel()
            if hasattr(app, "sync_cards"):
                app.sync_cards()

    def retry_failed(self) -> None:
        ids = self.tables["failed"].selected_ids() or self.db.media_ids_by_status(self.channel.id, "failed")
        self.db.retry(ids)
        self.refresh(self.cached_positions)

    def redownload(self) -> None:
        ids = self.tables["downloaded"].selected_ids()
        if not ids:
            messagebox.showinfo("Redownload", "Select one or more downloaded files first.", parent=self)
            return
        if messagebox.askyesno("Redownload", "Queue the selected files again?", parent=self):
            self.db.retry(ids, include_downloaded=True)
            self.refresh(self.cached_positions)

    def record_count(self, kind: str) -> int:
        return self.db.queue_count(self.channel.id) if kind == "queue" else self.db.status_count(self.channel.id, kind)

    def change_page(self, kind: str, amount: int) -> None:
        last_page = self.pages(self.record_count(kind), self.PAGE_SIZES[kind]) - 1
        self.page_numbers[kind] = min(last_page, max(0, self.page_numbers[kind] + amount))
        self.table_revisions[kind] = -1
        self.refresh(self.cached_positions)

    def refresh(self, positions: dict[int, int]) -> None:
        self.cached_positions = positions
        self.channel_pause_button.set_icon(
            "play" if self.channel.paused else "pause",
            "Resume channel" if self.channel.paused else "Pause channel",
        )
        revision = self.db.revision
        if revision != self.count_revision:
            self.cached_counts = {
                kind: self.record_count(kind) for kind in ("queue", "downloaded", "failed")
            }
            self.count_revision = revision
        counts = self.cached_counts
        title = f"{self.channel.title} (Paused)" if self.channel.paused else self.channel.title
        if self.collapsed:
            self.configure(
                text=(f"{title}    Queue: {counts['queue']}    "
                      f"Downloaded: {counts['downloaded']}    Failed: {counts['failed']}")
            )
        else:
            self.configure(text=title)
        minimum = f"{self.channel.min_duration_seconds // 60}:{self.channel.min_duration_seconds % 60:02d}"
        file_types = ", ".join(self.channel.media_types) if self.channel.media_types else "All file types"
        self.stats.set(
            f"Folder: {self.channel.folder}    "
            f"File types: {file_types}    "
            f"Minimum: {minimum} / {format_bytes(self.channel.min_size_bytes)}"
        )
        active = self.db.active_download(self.channel.id)
        if active:
            media_id = int(active["id"])
            size, done = int(active["size_bytes"]), int(active["bytes_downloaded"])
            now = time.monotonic()
            speed = max(0, done - self.last_bytes) / max(0.001, now - self.last_time) if media_id == self.last_active_id else 0
            self.last_active_id, self.last_bytes, self.last_time = media_id, done, now
            percent = min(100, round(done * 100 / size)) if size else 0
            prefix = "Current file (paused)" if self.channel.paused else "Current file"
            self.current.set(f"{prefix}: {active['file_name']}")
            self.progress.set_value(percent)
            if self.channel.paused:
                self.progress_text.set(f"{percent}%    {format_bytes(done)} / {format_bytes(size)}    Paused")
            else:
                self.progress_text.set(f"{percent}%    {format_bytes(done)} / {format_bytes(size)}    {format_bytes(round(speed))}/s")
        else:
            self.last_active_id = None
            self.current.set("Current file: Channel paused" if self.channel.paused else "Current file: Waiting for next file")
            self.progress.set_value(0)
            self.progress_text.set("0%")
        for kind, count in counts.items():
            self.notebook.tab(self.tab_indexes[kind], text=f"{kind.capitalize()} ({count})")
        if self.collapsed:
            return
        kind = self.selected_tab()
        if self.table_revisions[kind] == revision:
            return
        count = counts[kind]
        page_size = self.PAGE_SIZES[kind]
        page_count = self.pages(count, page_size)
        self.page_numbers[kind] = min(self.page_numbers[kind], page_count - 1)
        offset = self.page_numbers[kind] * page_size
        if kind == "queue":
            rows = self.db.queued_media_for_channel(self.channel.id, page_size, offset)
        elif kind == "downloaded":
            rows = self.db.downloaded_media_for_channel(self.channel.id, page_size, offset)
        else:
            rows = self.db.failed_media_for_channel(self.channel.id, page_size, offset)
        self.tables[kind].populate(rows, positions, self.channel.paused)
        self.page_labels[kind].set(f"Page {self.page_numbers[kind] + 1} of {page_count}")
        self.table_revisions[kind] = revision


class ConnectionPill(tk.Canvas):
    """Compact connection indicator with a health dot and rounded background."""

    COLORS = {
        "connected": ("#e6f4ea", "#137333"),
        "connecting": ("#eef1f4", "#5f6368"),
        "disconnected": ("#fce8e6", "#b3261e"),
    }

    def __init__(self, parent: tk.Misc):
        self.status_font = tkfont.Font(parent, family="Segoe UI", size=9, weight="bold")
        background = parent.winfo_toplevel().cget("background")
        super().__init__(
            parent, height=28, width=100, background=background,
            highlightthickness=0, borderwidth=0,
        )
        self.set_status("Connecting…", "connecting")

    def set_status(self, text: str, state: str) -> None:
        background, foreground = self.COLORS[state]
        label = f"●  {text}"
        width = self.status_font.measure(label) + 22
        height = 28
        radius = height // 2
        self.configure(width=width, height=height)
        self.delete("all")
        self.create_oval(1, 1, height - 1, height - 1, fill=background, outline="")
        self.create_oval(width - height + 1, 1, width - 1, height - 1, fill=background, outline="")
        self.create_rectangle(radius, 1, width - radius, height - 1, fill=background, outline="")
        self.create_arc(
            1, 1, height - 1, height - 1, start=90, extent=180,
            style="arc", outline=foreground, width=1,
        )
        self.create_arc(
            width - height + 1, 1, width - 1, height - 1, start=-90, extent=180,
            style="arc", outline=foreground, width=1,
        )
        self.create_line(radius, 1, width - radius, 1, fill=foreground, width=1)
        self.create_line(radius, height - 1, width - radius, height - 1, fill=foreground, width=1)
        self.create_text(width / 2, height / 2, text=label, fill=foreground, font=self.status_font)


class DownloaderApp(tk.Tk):
    def __init__(self, config: Config, db: Database):
        super().__init__()
        ttk.Style(self).configure("Icon.TButton", padding=(6, 5))
        self.icons = load_icons(self)
        self.db = db
        self.config = config
        self.events: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        self.cards: dict[int, ChannelPanel] = {}
        self.add_channel_dialog: AddChannelDialog | None = None
        self.paused = False
        self.closing = False
        self.poll_job: str | None = None
        self.position_revision = -1
        self.cached_positions: dict[int, int] = {}
        self.engine = DownloaderEngine(config, db, lambda name, payload: self.events.put((name, payload)))
        self.title("Channel Media Downloader")
        self.geometry("1280x820")
        self.minsize(900, 600)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.build_toolbar()
        self.build_scroller()
        self.sync_cards()
        self.poll_job = self.after(100, self.poll)
        self.engine.start()

    def build_toolbar(self) -> None:
        toolbar = ttk.Frame(self, padding=8)
        toolbar.pack(fill="x")
        IconButton(toolbar, "add", "Add channel", command=self.add_channel).pack(side="left")
        self.pause_button = IconButton(
            toolbar, "pause", "Pause downloads", command=self.toggle_pause,
        )
        self.pause_button.pack(side="left", padx=(6, 0))
        IconButton(
            toolbar, "settings", "Settings", command=self.open_settings,
        ).pack(side="left", padx=(6, 0))
        ttk.Label(toolbar, text="Speed limit:").pack(side="left", padx=(18, 5))
        self.speed = ttk.Combobox(
            toolbar, width=13, values=("Unlimited", "128 KB/s", "256 KB/s", "500 KB/s", "1024 KB/s", "2048 KB/s"),
        )
        value = self.db.setting_int("speed_limit_bytes_per_second", 0) / 1024
        self.speed.set("Unlimited" if value == 0 else f"{value:g} KB/s")
        self.speed.pack(side="left")
        self.speed.bind("<<ComboboxSelected>>", self.apply_speed)
        self.speed.bind("<Return>", self.apply_speed)
        self.connection = ConnectionPill(toolbar)
        self.connection.pack(side="right")
        self.error_message = tk.StringVar()
        self.error_banner = ttk.Label(
            self, textvariable=self.error_message, foreground="#a00000", padding=(10, 3),
            wraplength=1150,
        )
        self.error_banner.pack(fill="x")

    def build_scroller(self) -> None:
        container = ttk.Frame(self)
        container.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas, padding=(10, 4, 10, 10))
        self.body_window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.body.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.body_window, width=event.width))
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.canvas.bind_all("<MouseWheel>", lambda event: self.canvas.yview_scroll(int(-event.delta / 120), "units"))

    def sync_cards(self) -> None:
        enabled_channels = {channel.id: channel for channel in self.db.channels(True)}
        for channel_id in list(self.cards):
            if channel_id not in enabled_channels:
                self.cards.pop(channel_id).destroy()
        for channel in enabled_channels.values():
            if channel.id not in self.cards:
                card = ChannelPanel(self.body, channel, self.db, self.engine)
                card.pack(fill="x", expand=True, pady=(0, 10))
                self.cards[channel.id] = card
            else:
                self.cards[channel.id].channel = channel

    def add_channel(self) -> None:
        if self.add_channel_dialog is not None and self.add_channel_dialog.winfo_exists():
            self.add_channel_dialog.lift()
            self.add_channel_dialog.focus_set()
            return
        self.add_channel_dialog = AddChannelDialog(self, self.engine)

    def open_settings(self) -> None:
        SettingsDialog(self, self.db, self.config)

    def apply_speed(self, _event: Any = None) -> None:
        value = self.speed.get().strip().lower()
        try:
            kbps = 0.0 if value in {"", "unlimited", "0"} else float(value.replace("kb/s", "").strip())
        except ValueError:
            messagebox.showwarning("Invalid speed", "Use a number in KB/s or Unlimited.", parent=self)
            return
        if kbps < 0:
            return
        self.engine.command("set_speed_limit", bytes_per_second=round(kbps * 1024))

    def toggle_pause(self) -> None:
        target_paused = not self.paused
        changed = self.engine.pause() if target_paused else self.engine.resume()
        if not changed:
            self.error_message.set("The downloader is not running. Change Settings if needed, then restart the application.")
            return
        self.paused = target_paused
        if self.paused:
            self.pause_button.set_icon("play", "Resume downloads")
        else:
            self.pause_button.set_icon("pause", "Pause downloads")

    def handle_event(self, event: str, data: dict[str, Any]) -> None:
        if event == "fatal":
            self.connection.set_status("Not connected", "disconnected")
            self.error_message.set(
                f"Downloader stopped: {data.get('error', 'Unknown error')}  "
                "Change Settings if needed, then restart the application."
            )
        elif event == "error":
            self.error_message.set(str(data.get("error", "")))
        elif event == "connection":
            status = str(data.get("status", "disconnected"))
            if status == "connected":
                suffix = " through MTProto proxy" if data.get("mtproto_proxy") else ""
                self.connection.set_status(f"Connected{suffix}", "connected")
            elif status == "connecting":
                self.connection.set_status("Connecting…", "connecting")
            else:
                self.connection.set_status("Disconnected", "disconnected")
        elif event == "channel_added":
            self.sync_cards()
        elif event == "channel_removed":
            self.sync_cards()
        elif event == "channel_types":
            dialog = self.add_channel_dialog
            if dialog is not None and dialog.winfo_exists():
                dialog.receive_file_types(data)

    def poll(self) -> None:
        if self.closing:
            return
        try:
            while True:
                event, data = self.events.get_nowait()
                self.handle_event(event, data)
        except queue.Empty:
            pass
        self.sync_cards()
        revision = self.db.revision
        if revision != self.position_revision:
            self.cached_positions = self.db.queue_positions()
            self.position_revision = revision
        for card in self.cards.values():
            card.refresh(self.cached_positions)
        self.poll_job = self.after(1000, self.poll)

    def close(self) -> None:
        if self.closing:
            return
        self.closing = True
        if self.poll_job is not None:
            try:
                self.after_cancel(self.poll_job)
            except tk.TclError:
                pass
            self.poll_job = None
        try:
            self.engine.stop()
        finally:
            self.destroy()


def create_application(config: Config, db: Database) -> DownloaderApp:
    return DownloaderApp(config, db)
