from __future__ import annotations

import asyncio
import math
import queue
import re
import threading
import time
from collections import deque
import tkinter as tk
from pathlib import Path
from tkinter import font as tkfont
from tkinter import messagebox, simpledialog, ttk
from typing import Any, Callable

from PIL import Image, ImageDraw, ImageFont, ImageOps, ImageTk

try:
    from .config import Config
    from .database import Channel, Database, priority_name
    from .engine import DownloaderEngine
    from .media import format_bytes
    from .proxy import (
        MtProtoProxy, ensure_telethon_compatible, load_mtproto_proxy, parse_proxy_link,
        save_mtproto_proxy,
    )
    from .proxy_check import check_mtproto_proxy, sanitized_proxy_error
    from .ui_performance import HEARTBEAT_INTERVAL_MS, UiPerformanceRecorder
except ImportError:
    from config import Config
    from database import Channel, Database, priority_name
    from engine import DownloaderEngine
    from media import format_bytes
    from proxy import (
        MtProtoProxy, ensure_telethon_compatible, load_mtproto_proxy, parse_proxy_link,
        save_mtproto_proxy,
    )
    from proxy_check import check_mtproto_proxy, sanitized_proxy_error
    from ui_performance import HEARTBEAT_INTERVAL_MS, UiPerformanceRecorder


ICON_NAMES = (
    "add", "failed", "pause", "play", "queue", "refresh", "settings", "success",
    "previous", "next", "remove", "priority-up", "priority-down", "speed",
)

PRIORITY_COLORS = {"high": "#34a853", "medium": None, "low": "#d93025"}

APP_BACKGROUND = "#f3f5f7"
SIDEBAR_BACKGROUND = "#eef1f4"
CONTENT_BACKGROUND = "#ffffff"
TEXT_COLOR = "#20252b"
MUTED_TEXT = "#66717d"
ACCENT = "#1677ff"
BORDER_COLOR = "#dfe4e8"


def configure_application_theme(window: tk.Tk) -> None:
    """Apply the restrained desktop palette used by the main workspace."""
    window.configure(background=APP_BACKGROUND)
    style = ttk.Style(window)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", font=("Segoe UI", 9), foreground=TEXT_COLOR)
    # ttk widgets are not transparent. Give every generic surface widget the
    # content fill so the Clam theme does not show gray rectangles behind it.
    style.configure("TFrame", background=CONTENT_BACKGROUND)
    style.configure("TLabel", background=CONTENT_BACKGROUND, foreground=TEXT_COLOR)
    style.configure("TCheckbutton", background=CONTENT_BACKGROUND, foreground=TEXT_COLOR)
    style.map(
        "TCheckbutton",
        background=[("disabled", CONTENT_BACKGROUND), ("active", CONTENT_BACKGROUND)],
        foreground=[("disabled", MUTED_TEXT)],
    )
    style.configure(
        "TButton", background=CONTENT_BACKGROUND, foreground=TEXT_COLOR,
        bordercolor=BORDER_COLOR, lightcolor=CONTENT_BACKGROUND,
        darkcolor=CONTENT_BACKGROUND, padding=(8, 5), relief="flat",
    )
    style.map(
        "TButton",
        background=[
            ("disabled", CONTENT_BACKGROUND),
            ("pressed", "#e5edf5"),
            ("active", "#f0f5fa"),
        ],
        foreground=[("disabled", MUTED_TEXT)],
        bordercolor=[("focus", ACCENT), ("active", "#cbd5df")],
    )
    style.configure("App.TFrame", background=APP_BACKGROUND)
    style.configure("Topbar.TFrame", background=CONTENT_BACKGROUND)
    style.configure("Sidebar.TFrame", background=SIDEBAR_BACKGROUND)
    style.configure("Content.TFrame", background=CONTENT_BACKGROUND)
    style.configure("Topbar.TLabel", background=CONTENT_BACKGROUND, foreground=TEXT_COLOR)
    style.configure(
        "Brand.TLabel", background=CONTENT_BACKGROUND, foreground=TEXT_COLOR,
        font=("Segoe UI Semibold", 13),
    )
    style.configure(
        "SidebarTitle.TLabel", background=SIDEBAR_BACKGROUND, foreground=MUTED_TEXT,
        font=("Segoe UI Semibold", 9),
    )
    style.configure(
        "ChannelTitle.TLabel", background=CONTENT_BACKGROUND, foreground=TEXT_COLOR,
        font=("Segoe UI Semibold", 15),
    )
    style.configure("Content.TLabel", background=CONTENT_BACKGROUND, foreground=TEXT_COLOR)
    style.configure("Muted.TLabel", background=CONTENT_BACKGROUND, foreground=MUTED_TEXT)
    style.configure(
        "Error.TLabel", background="#fce8e6", foreground="#b3261e",
        font=("Segoe UI", 9),
    )
    style.configure(
        "Icon.TButton", background=CONTENT_BACKGROUND, bordercolor=BORDER_COLOR,
        lightcolor=CONTENT_BACKGROUND, darkcolor=CONTENT_BACKGROUND,
        padding=(7, 6), relief="flat",
    )
    style.map(
        "Icon.TButton",
        background=[
            ("disabled", CONTENT_BACKGROUND),
            ("pressed", "#e5edf5"),
            ("active", "#f0f5fa"),
        ],
        bordercolor=[("focus", ACCENT), ("active", "#cbd5df")],
    )
    style.configure(
        "Primary.TButton", background=ACCENT, foreground="#ffffff",
        bordercolor=ACCENT, padding=(12, 7), relief="flat",
    )
    style.map(
        "Primary.TButton",
        background=[
            ("disabled", ACCENT), ("pressed", "#0759c5"), ("active", "#0b68df"),
        ],
        foreground=[("disabled", "#d7e6fb")],
    )
    style.configure(
        "Media.Treeview", background=CONTENT_BACKGROUND, fieldbackground=CONTENT_BACKGROUND,
        foreground=TEXT_COLOR, borderwidth=0, relief="flat", rowheight=36,
    )
    style.configure(
        "Media.Treeview.Heading", background="#f7f8fa", foreground=MUTED_TEXT,
        bordercolor=BORDER_COLOR, borderwidth=1, relief="flat",
        font=("Segoe UI Semibold", 9), padding=(8, 7),
    )
    style.map(
        "Media.Treeview",
        background=[("selected", "#dcebff")], foreground=[("selected", TEXT_COLOR)],
    )
    style.map("Media.Treeview.Heading", background=[("active", "#edf2f7")])
    style.configure(
        "Workspace.TNotebook", background=CONTENT_BACKGROUND, borderwidth=0,
        tabmargins=(0, 0, 0, 0),
    )
    style.configure(
        "Workspace.TNotebook.Tab", background="#e8ecef", foreground="#59636e",
        bordercolor="#cfd6dd", lightcolor="#cfd6dd", darkcolor="#cfd6dd",
        borderwidth=1, relief="flat", padding=(14, 9), font=("Segoe UI Semibold", 9),
    )
    style.map(
        "Workspace.TNotebook.Tab",
        background=[("selected", CONTENT_BACKGROUND), ("active", "#dde4eb")],
        foreground=[("selected", ACCENT), ("active", TEXT_COLOR)],
        bordercolor=[("selected", "#c7d0d9"), ("active", "#c7d0d9")],
        expand=[("selected", (0, 0, 0, 0))],
    )


def load_icons(master: tk.Misc) -> dict[str, tk.PhotoImage]:
    icon_directory = Path(__file__).resolve().parent.parent / "assets" / "png"
    icons: dict[str, tk.PhotoImage] = {}
    for name in ICON_NAMES:
        try:
            icons[name] = tk.PhotoImage(master=master, file=str(icon_directory / f"{name}.png"))
        except tk.TclError:
            pass
    for priority, color in PRIORITY_COLORS.items():
        if color is None:
            continue
        marker = tk.PhotoImage(master=master, width=5, height=16)
        marker.put(color, to=(0, 0, 5, 16))
        icons[f"{priority}-priority-marker"] = marker
    return icons


class AvatarStore:
    """Load, crop, and retain channel avatars at the sizes used by the UI."""

    FALLBACK_COLORS = ("#557a95", "#6c70a8", "#4f8b78", "#8a6f9e", "#9a7158")

    def __init__(self, master: tk.Misc, directory: Path):
        self.master = master
        self.directory = directory
        self.cache: dict[tuple[int, int, int], tk.PhotoImage] = {}

    def invalidate(self, channel_id: int) -> None:
        self.cache = {
            key: image for key, image in self.cache.items() if key[0] != channel_id
        }

    def get(self, channel: Channel, size: int) -> tk.PhotoImage:
        path = self.directory / f"{channel.telegram_id}.jpg" if channel.telegram_id else None
        modified = path.stat().st_mtime_ns if path is not None and path.exists() else 0
        key = (channel.id, size, modified)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        image = self._render(path, channel.title, channel.telegram_id or channel.id, size)
        self.cache[key] = image
        return image

    def _render(self, path: Path | None, title: str, identity: int, size: int) -> tk.PhotoImage:
        scale = 4
        render_size = size * scale
        source = None
        if path is not None and path.exists():
            try:
                with Image.open(path) as opened:
                    source = ImageOps.fit(
                        ImageOps.exif_transpose(opened).convert("RGB"),
                        (render_size, render_size), method=Image.Resampling.LANCZOS,
                    )
            except (OSError, ValueError):
                source = None
        if source is None:
            source = self._initial_fallback(title, identity, render_size)
        mask = Image.new("L", (render_size, render_size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, render_size - 1, render_size - 1), fill=255)
        avatar = Image.new("RGBA", (render_size, render_size), (0, 0, 0, 0))
        avatar.paste(source, (0, 0), mask)
        avatar = avatar.resize((size, size), Image.Resampling.LANCZOS)
        return ImageTk.PhotoImage(avatar, master=self.master)

    def _initial_fallback(self, title: str, identity: int, size: int) -> Any:
        color = self.FALLBACK_COLORS[abs(int(identity)) % len(self.FALLBACK_COLORS)]
        image = Image.new("RGB", (size, size), color)
        initial = next((character.upper() for character in title.strip() if character.isalnum()), "?")
        try:
            font = ImageFont.truetype("segoeuib.ttf", max(12, round(size * 0.43)))
        except OSError:
            font = ImageFont.load_default(size=max(12, round(size * 0.43)))
        draw = ImageDraw.Draw(image)
        bounds = draw.textbbox((0, 0), initial, font=font)
        width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
        draw.text(
            ((size - width) / 2 - bounds[0], (size - height) / 2 - bounds[1] - 1),
            initial, fill="#ffffff", font=font,
        )
        return image

def set_windows_app_identity() -> None:
    """Give Windows a stable taskbar identity instead of grouping under Python."""
    try:
        import ctypes
        import sys

        if sys.platform == "win32":
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "TelegramDownloader.ChannelMediaDownloader"
            )
    except (AttributeError, OSError):
        # Other platforms and restricted Windows environments can use Tk's icon alone.
        pass


def apply_application_icon(window: tk.Tk) -> list[tk.PhotoImage]:
    icon_directory = Path(__file__).resolve().parent.parent / "assets" / "png"
    images: list[tk.PhotoImage] = []
    for name in ("app-icon-256.png", "app-icon-32.png"):
        try:
            images.append(tk.PhotoImage(master=window, file=str(icon_directory / name)))
        except tk.TclError:
            continue
    if images:
        window.iconphoto(True, *images)
    return images


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
        self.configure(background=CONTENT_BACKGROUND)
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
        self.configure(background=CONTENT_BACKGROUND)
        self.engine = engine
        self.title("Add channel")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.columnconfigure(1, weight=1)
        self.identifier = tk.StringVar()
        self.folder_prefix = tk.StringVar()
        self.folder_suffix = tk.StringVar()
        self.folder_separator = tk.StringVar()
        self.duration = tk.StringVar(value="1")
        self.size = tk.StringVar(value="2")
        self.file_types_text = tk.StringVar(value="All file types")
        self.selected_file_types: tuple[str, ...] = ()
        self.available_file_types: list[tuple[str, int]] = []
        self.inspect_request_id: str | None = None

        ttk.Label(self, text="Channel username, link, or ID:").grid(row=0, column=0, sticky="w", padx=12, pady=7)
        self.identifier_entry = ttk.Entry(self, textvariable=self.identifier, width=42)
        self.identifier_entry.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(0, 12), pady=7)
        ttk.Label(self, text="Download folder:").grid(row=1, column=0, sticky="w", padx=12, pady=7)
        folder_row = ttk.Frame(self)
        folder_row.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(0, 12), pady=7)
        folder_row.columnconfigure(2, weight=1)
        ttk.Entry(
            folder_row, textvariable=self.folder_prefix, state="readonly", width=24,
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(folder_row, textvariable=self.folder_separator).grid(
            row=0, column=1, padx=(3, 3),
        )
        ttk.Entry(folder_row, textvariable=self.folder_suffix, width=18).grid(
            row=0, column=2, sticky="ew",
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
            self, textvariable=self.duration, values=("0", "1", "2", "5", "10", "60"),
            state="readonly", width=12,
        ).grid(row=3, column=1, sticky="w", pady=7)
        ttk.Label(self, text="Minimum size (MB):").grid(row=4, column=0, sticky="w", padx=12, pady=7)
        ttk.Combobox(
            self, textvariable=self.size, values=("0", "2", "10", "50", "100"),
            state="readonly", width=12,
        ).grid(row=4, column=1, sticky="w", pady=7)

        self.identifier.trace_add("write", self._identifier_changed)
        self.folder_suffix.trace_add("write", self._folder_suffix_changed)
        actions = ttk.Frame(self)
        actions.grid(row=5, column=0, columnspan=3, sticky="e", padx=12, pady=12)
        ttk.Button(actions, text="Cancel", command=self.destroy).pack(side="right", padx=(8, 0))
        ttk.Button(actions, text="Add channel", command=self.submit).pack(side="right")
        self.bind("<Return>", lambda _event: self.submit())
        self.wait_visibility()
        self.identifier_entry.focus_set()

    def _identifier_changed(self, *_args: Any) -> None:
        self.folder_prefix.set(suggested_channel_folder(self.identifier.get()))

    def _folder_suffix_changed(self, *_args: Any) -> None:
        suffix = self.folder_suffix.get()
        without_separator = suffix.lstrip("_")
        if without_separator != suffix:
            self.folder_suffix.set(without_separator)
            return
        self.folder_separator.set("_" if suffix.strip() else "")

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

    def receive_file_type_progress(self, data: dict[str, Any]) -> None:
        if data.get("request_id") != self.inspect_request_id:
            return
        count = max(0, int(data.get("files_fetched", 0)))
        noun = "file" if count == 1 else "files"
        self.file_types_text.set(f"Scanning channel… {count:,} {noun} fetched")

    def receive_file_types(self, data: dict[str, Any]) -> None:
        if data.get("request_id") != self.inspect_request_id:
            return
        self.inspect_request_id = None
        self.refresh_types_button.configure(state="normal")
        if data.get("error"):
            self.file_types_text.set("All file types" if not self.selected_file_types else ", ".join(self.selected_file_types))
            messagebox.showwarning("File type scan failed", str(data["error"]), parent=self)
            return
        folder_prefix = str(data.get("folder_prefix") or data.get("username") or "").strip()
        if folder_prefix:
            self.folder_prefix.set(folder_prefix)
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
        folder_suffix = self.folder_suffix.get().strip()
        if re.search(r'[<>:"/\\|?*\x00-\x1f]', folder_suffix):
            messagebox.showwarning(
                "Invalid download folder",
                "The folder suffix cannot contain path separators or Windows-invalid characters.",
                parent=self,
            )
            return
        self.engine.command(
            "add_channel", identifier=identifier, folder="", folder_suffix=folder_suffix,
            min_duration_seconds=duration, min_size_bytes=size,
            media_types=self.selected_file_types,
        )
        self.destroy()


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, db: Database, config: Config):
        super().__init__(parent)
        self.configure(background=CONTENT_BACKGROUND)
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


class AutoScrollbar(ttk.Scrollbar):
    """Hide table scrollbars when the full data range is already visible."""

    def set(self, first: str, last: str) -> None:
        if float(first) <= 0.0 and float(last) >= 1.0:
            self.grid_remove()
        else:
            self.grid()
        super().set(first, last)


class RoundedNotebook(ttk.Frame):
    """Notebook-like container with fixed-height, genuinely rounded tabs."""

    TAB_HEIGHT = 34
    TAB_GAP = 5
    TAB_RADIUS = 7

    def __init__(self, parent: tk.Misc):
        super().__init__(parent, style="Content.TFrame")
        self.tabs: list[dict[str, Any]] = []
        self.selected_index = -1
        self.tab_bounds: list[tuple[int, int]] = []
        self.rendered_tabs: list[Any] = []
        self.tab_backgrounds: dict[tuple[int, bool], tk.PhotoImage] = {}
        self.tab_font = tkfont.Font(parent, family="Segoe UI", size=9, weight="bold")
        self.tab_bar = tk.Canvas(
            self, height=self.TAB_HEIGHT + 8, background=CONTENT_BACKGROUND,
            highlightthickness=0, borderwidth=0,
        )
        self.tab_bar.pack(fill="x")
        self.tab_bar.bind("<Button-1>", self._clicked)
        self.tab_bar.bind("<Configure>", lambda _event: self._redraw())

    def add(self, page: tk.Widget, **options: Any) -> None:
        self.tabs.append({
            "page": page,
            "text": str(options.get("text", "")),
            "image": options.get("image"),
        })
        if self.selected_index < 0:
            self._show(0, notify=False)
        else:
            page.pack_forget()
        self._redraw()

    def select(self, tab_id: Any = None) -> int:
        if tab_id is None:
            return self.selected_index
        index = self.index(tab_id)
        self._show(index)
        return index

    def index(self, tab_id: Any) -> int:
        if isinstance(tab_id, int):
            return tab_id
        for index, tab in enumerate(self.tabs):
            if str(tab["page"]) == str(tab_id):
                return index
        raise tk.TclError(f"Unknown tab {tab_id}")

    def tab(self, tab_id: Any, **options: Any) -> dict[str, Any] | None:
        tab = self.tabs[self.index(tab_id)]
        if not options:
            return dict(tab)
        changed = False
        if "text" in options:
            text = str(options["text"])
            changed = changed or text != tab["text"]
            tab["text"] = text
        if "image" in options:
            image = options["image"]
            changed = changed or image is not tab["image"]
            tab["image"] = image
        if changed:
            self._redraw()
        return None

    def set_texts(self, texts: dict[int, str]) -> None:
        changed = False
        for index, text in texts.items():
            normalized = str(text)
            if self.tabs[index]["text"] != normalized:
                self.tabs[index]["text"] = normalized
                changed = True
        if changed:
            self._redraw()

    def _show(self, index: int, notify: bool = True) -> None:
        if not 0 <= index < len(self.tabs):
            return
        changed = index != self.selected_index
        if not changed:
            return
        if self.selected_index >= 0:
            self.tabs[self.selected_index]["page"].pack_forget()
        self.selected_index = index
        self.tabs[index]["page"].pack(fill="both", expand=True)
        self._redraw()
        if changed and notify:
            self.event_generate("<<NotebookTabChanged>>")

    def _clicked(self, event: tk.Event) -> None:
        for index, (left, right) in enumerate(self.tab_bounds):
            if left <= event.x <= right:
                self._show(index)
                return

    def _redraw(self) -> None:
        self.tab_bar.delete("all")
        self.tab_bounds = []
        self.rendered_tabs = []
        left = 0
        top = 4
        bottom = top + self.TAB_HEIGHT
        for index, tab in enumerate(self.tabs):
            image = tab.get("image")
            image_width = image.width() if image is not None else 0
            text_width = self.tab_font.measure(tab["text"])
            width = 28 + text_width + image_width + (7 if image is not None else 0)
            right = left + width
            selected = index == self.selected_index
            fill = CONTENT_BACKGROUND if selected else "#e3e7eb"
            outline = "#c5ced7" if selected else "#d2d8de"
            cache_key = (width, selected)
            tab_image = self.tab_backgrounds.get(cache_key)
            if tab_image is None:
                scale = 4
                rendered = Image.new(
                    "RGBA", (width * scale, self.TAB_HEIGHT * scale), (0, 0, 0, 0),
                )
                ImageDraw.Draw(rendered).rounded_rectangle(
                    (scale, scale, width * scale - scale - 1, self.TAB_HEIGHT * scale - scale - 1),
                    radius=self.TAB_RADIUS * scale, fill=fill, outline=outline, width=scale,
                )
                rendered = rendered.resize(
                    (width, self.TAB_HEIGHT), Image.Resampling.LANCZOS,
                )
                tab_image = ImageTk.PhotoImage(rendered, master=self.tab_bar)
                self.tab_backgrounds[cache_key] = tab_image
            self.rendered_tabs.append(tab_image)
            self.tab_bar.create_image(left, top, image=tab_image, anchor="nw")
            content_left = left + 14
            if image is not None:
                self.tab_bar.create_image(
                    content_left, (top + bottom) / 2, image=image, anchor="w",
                )
                content_left += image_width + 7
            self.tab_bar.create_text(
                content_left, (top + bottom) / 2, text=tab["text"], anchor="w",
                fill=ACCENT if selected else "#59636e", font=self.tab_font,
            )
            self.tab_bounds.append((left, right))
            left = right + self.TAB_GAP


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

    def __init__(self, parent: tk.Misc, columns: tuple[str, ...],
                 on_sort: Callable[[str], None] | None = None):
        super().__init__(parent)
        self.columns = columns
        self.icons = getattr(parent.winfo_toplevel(), "icons", {})
        self.tree = ttk.Treeview(
            self, columns=columns, show=("tree", "headings"), height=10,
            selectmode="extended", style="Media.Treeview",
        )
        self.tree.tag_configure("even", background=CONTENT_BACKGROUND)
        self.tree.tag_configure("odd", background="#fafbfc")
        self.tree.heading("#0", text="")
        self.tree.column("#0", width=22, minwidth=18, stretch=False, anchor="center")
        for column in columns:
            label, width = self.COLUMN_DEFINITIONS[column]
            self.tree.heading(
                column, text=label,
                command=(lambda selected=column: on_sort(selected)) if on_sort else None,
            )
            self.tree.column(column, width=width, minwidth=60, stretch=column in {"name", "error"})
        yscroll = AutoScrollbar(self, orient="vertical", command=self.tree.yview)
        xscroll = AutoScrollbar(self, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.row_signatures: dict[str, tuple[Any, ...]] = {}

    def set_sort(self, sort_column: str | None, descending: bool) -> None:
        for column in self.columns:
            label = self.COLUMN_DEFINITIONS[column][0]
            if column == sort_column:
                label += " ▼" if descending else " ▲"
            self.tree.heading(column, text=label)

    def selected_ids(self) -> list[int]:
        return [int(item) for item in self.tree.selection()]

    def populate(self, rows: list[Any], positions: dict[int, int], channel_paused: bool = False) -> None:
        desired_ids = [str(int(row["id"])) for row in rows]
        desired_set = set(desired_ids)
        obsolete = [item_id for item_id in self.tree.get_children() if item_id not in desired_set]
        if obsolete:
            self.tree.delete(*obsolete)
            for item_id in obsolete:
                self.row_signatures.pop(item_id, None)
        for row_index, row in enumerate(rows):
            media_id = int(row["id"])
            item_id = str(media_id)
            status = row["status"]
            size = int(row["size_bytes"])
            duration = int(row["duration_seconds"])
            if status == "queued" and channel_paused:
                display_status = "Paused"
            elif status == "queued":
                display_status = f"Queued (position {positions.get(media_id, '?')})"
            elif status == "downloading":
                display_status = "Paused" if channel_paused else "Downloading"
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
            item_priority = priority_name(int(row["priority_level"]))
            image = self.icons.get(f"{item_priority}-priority-marker")
            options: dict[str, Any] = {
                "text": "",
                "values": tuple(values[column] for column in self.columns),
                "tags": ("even" if row_index % 2 == 0 else "odd",),
                "image": image or "",
            }
            signature = (options["values"], options["tags"], item_priority)
            if item_id not in self.row_signatures:
                self.tree.insert("", row_index, iid=item_id, **options)
            else:
                if self.row_signatures[item_id] != signature:
                    self.tree.item(item_id, **options)
                if self.tree.index(item_id) != row_index:
                    self.tree.move(item_id, "", row_index)
            self.row_signatures[item_id] = signature


class BlueProgressBar(tk.Canvas):
    """Compact determinate progress bar using the application's blue palette."""

    TRACK = "#eaf3fb"
    BORDER = "#bad7ee"
    FILL = "#82bdf0"

    def __init__(self, parent: tk.Misc):
        super().__init__(
            parent, height=14, background=CONTENT_BACKGROUND,
            highlightthickness=0, borderwidth=0,
        )
        self.value = 0.0
        self.bind("<Configure>", self._redraw)

    def set_value(self, value: float) -> None:
        value = min(100.0, max(0.0, float(value)))
        if value == self.value:
            return
        self.value = value
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


class ActiveDownloadIndicator(tk.Canvas):
    """Show a smooth green pulse for an active channel."""

    WIDTH = 20
    HEIGHT = 20
    COLOR = "#16a34a"
    FRAME_MS = 33
    CYCLE_SECONDS = 1.6
    FRAME_COUNT = round(CYCLE_SECONDS * 1000 / FRAME_MS)
    RENDER_SCALE = 4

    def __init__(self, parent: tk.Misc):
        super().__init__(
            parent, width=self.WIDTH, height=self.HEIGHT,
            background=SIDEBAR_BACKGROUND, highlightthickness=0, borderwidth=0,
        )
        self.active = False
        self.started_at = 0.0
        self.animation_job: str | None = None
        self.frame_cache: dict[str, list[tk.PhotoImage]] = {}
        self.rendered_frame: tk.PhotoImage | None = None

    @staticmethod
    def _blend(foreground: str, background: str, opacity: float) -> str:
        foreground_rgb = tuple(int(foreground[index:index + 2], 16) for index in (1, 3, 5))
        background_rgb = tuple(int(background[index:index + 2], 16) for index in (1, 3, 5))
        mixed = (
            round(front * opacity + back * (1.0 - opacity))
            for front, back in zip(foreground_rgb, background_rgb)
        )
        return "#" + "".join(f"{component:02x}" for component in mixed)

    def set_background(self, color: str) -> None:
        self.configure(background=color)

    def _frames_for_background(self, background: str) -> list[tk.PhotoImage]:
        cached = self.frame_cache.get(background)
        if cached is not None:
            return cached
        scale = self.RENDER_SCALE
        frames: list[tk.PhotoImage] = []
        for frame_index in range(self.FRAME_COUNT):
            elapsed = frame_index * self.CYCLE_SECONDS / self.FRAME_COUNT
            rendered = Image.new(
                "RGB", (self.WIDTH * scale, self.HEIGHT * scale), background,
            )
            draw = ImageDraw.Draw(rendered)
            center_x = self.WIDTH * scale / 2
            center_y = self.HEIGHT * scale / 2
            for delay in (0.0, self.CYCLE_SECONDS / 2):
                phase = ((elapsed - delay) % self.CYCLE_SECONDS) / self.CYCLE_SECONDS
                radius = (1.5 + (8.4 - 1.5) * phase) * scale
                color = self._blend(self.COLOR, background, 0.9 * (1.0 - phase))
                draw.ellipse(
                    (
                        center_x - radius, center_y - radius,
                        center_x + radius, center_y + radius,
                    ),
                    fill=color,
                )
            dot_radius = 2.6 * scale
            draw.ellipse(
                (
                    center_x - dot_radius, center_y - dot_radius,
                    center_x + dot_radius, center_y + dot_radius,
                ),
                fill=self.COLOR,
            )
            rendered = rendered.resize(
                (self.WIDTH, self.HEIGHT), Image.Resampling.LANCZOS,
            )
            frames.append(ImageTk.PhotoImage(rendered, master=self))
        self.frame_cache[background] = frames
        return frames

    def set_active(self, active: bool) -> None:
        active = bool(active)
        if active == self.active:
            return
        self.active = active
        if active:
            self.started_at = time.monotonic()
            self.pack(side="left", padx=(5, 0))
            self._draw_frame()
        else:
            if self.animation_job is not None:
                self.after_cancel(self.animation_job)
                self.animation_job = None
            self.delete("all")
            self.pack_forget()

    def _draw_frame(self) -> None:
        if not self.active or not self.winfo_exists():
            return
        elapsed = time.monotonic() - self.started_at
        background = str(self.cget("background"))
        frames = self._frames_for_background(background)
        frame_index = int(elapsed / self.CYCLE_SECONDS * self.FRAME_COUNT) % self.FRAME_COUNT
        self.rendered_frame = frames[frame_index]
        self.delete("all")
        self.create_image(0, 0, image=self.rendered_frame, anchor="nw")
        self.animation_job = self.after(self.FRAME_MS, self._draw_frame)

    def destroy(self) -> None:
        if self.animation_job is not None:
            self.after_cancel(self.animation_job)
            self.animation_job = None
        super().destroy()


class ChannelPanel(ttk.Frame):
    PAGE_SIZES = {"queue": 200, "downloaded": 100, "failed": 100, "removed": 100}
    SPEED_WINDOW_SECONDS = 5.0

    def __init__(
        self, parent: tk.Misc, channel: Channel, db: Database,
        engine: DownloaderEngine, avatar_store: AvatarStore,
    ):
        super().__init__(parent, padding=(22, 18, 22, 18), style="Content.TFrame")
        self.channel, self.db, self.engine = channel, db, engine
        self.avatar_store = avatar_store
        self.title_text = tk.StringVar(value=channel.title)
        self.page_numbers = {kind: 0 for kind in self.PAGE_SIZES}
        self.tables: dict[str, MediaTable] = {}
        self.page_labels: dict[str, tk.StringVar] = {}
        self.tab_indexes: dict[str, int] = {}
        self.table_revisions = {kind: -1 for kind in self.PAGE_SIZES}
        self.count_revision = -1
        self.cached_counts = {kind: 0 for kind in self.PAGE_SIZES}
        self.sort_columns: dict[str, str | None] = {kind: None for kind in self.PAGE_SIZES}
        self.sort_descending = {kind: False for kind in self.PAGE_SIZES}
        self.search_query = ""
        self.cached_positions: dict[int, int] = {}
        self.last_active_id: int | None = None
        self.speed_samples: deque[tuple[float, int]] = deque()
        self.current = tk.StringVar(value="Current file: Waiting for next file")
        self.progress_text = tk.StringVar(value="0%")
        self.speed_text = tk.StringVar(value="0 B/s")
        self.stats = tk.StringVar()
        self.icons = getattr(parent.winfo_toplevel(), "icons", {})

        summary = ttk.Frame(self, style="Content.TFrame")
        summary.pack(fill="x", pady=(0, 12))
        summary.columnconfigure(1, weight=1)
        self.avatar_label = ttk.Label(summary, style="Content.TLabel")
        self.avatar_label.grid(row=0, column=0, rowspan=4, sticky="nw", padx=(0, 16))
        self.set_avatar()

        title_row = ttk.Frame(summary, style="Content.TFrame")
        title_row.grid(row=0, column=1, sticky="ew")
        ttk.Label(
            title_row, textvariable=self.title_text, style="ChannelTitle.TLabel",
        ).pack(side="left", padx=(0, 18))
        self.channel_pause_button = IconButton(
            title_row, "play" if channel.paused else "pause",
            "Resume channel" if channel.paused else "Pause channel",
            command=self.toggle_channel_pause,
        )
        self.channel_pause_button.pack(side="right", padx=(0, 6))
        self.remove_channel_button = IconButton(
            title_row, "remove", "Remove channel", command=self.remove_channel,
        )
        self.remove_channel_button.pack(side="right", padx=(0, 6))
        self.rescan_button = IconButton(
            title_row, "refresh", "Check for new files", command=self.rescan,
        )
        self.rescan_button.pack(side="right", padx=(0, 6))
        self.channel_priority_down_button = IconButton(
            title_row, "priority-down", "Lower channel priority",
            command=lambda: self.shift_channel_priority(1),
        )
        self.channel_priority_down_button.pack(side="right", padx=(0, 6))
        self.channel_priority_up_button = IconButton(
            title_row, "priority-up", "Raise channel priority",
            command=lambda: self.shift_channel_priority(-1),
        )
        self.channel_priority_up_button.pack(side="right", padx=(0, 6))

        current_row = ttk.Frame(summary, style="Content.TFrame")
        current_row.grid(row=1, column=1, sticky="ew", pady=(7, 0))
        current_row.columnconfigure(0, weight=1)
        ttk.Label(
            current_row, textvariable=self.current, width=1, anchor="w", style="Content.TLabel",
        ).grid(row=0, column=0, sticky="ew")
        ttk.Label(
            current_row, textvariable=self.progress_text, anchor="e", style="Muted.TLabel",
        ).grid(
            row=0, column=1, sticky="e", padx=(12, 0),
        )
        ttk.Label(
            current_row, image=self.icons.get("speed"), style="Muted.TLabel",
        ).grid(row=0, column=2, padx=(14, 5))
        ttk.Label(
            current_row, textvariable=self.speed_text, width=11,
            anchor="w", style="Muted.TLabel",
        ).grid(row=0, column=3, sticky="w")
        self.progress = BlueProgressBar(summary)
        self.progress.grid(row=2, column=1, sticky="ew", pady=(7, 0))
        ttk.Label(
            summary, textvariable=self.stats, style="Muted.TLabel",
        ).grid(row=3, column=1, sticky="ew", pady=(7, 0))

        self.details = ttk.Frame(self, style="Content.TFrame")
        self.details.pack(fill="both", expand=True)
        self.notebook = RoundedNotebook(self.details)
        self.notebook.pack(fill="both", expand=True)
        self.make_tab("queue", ("name", "duration", "size", "status", "date_added"))
        self.make_tab("downloaded", ("name", "duration", "size", "date_downloaded"))
        self.make_tab("failed", ("name", "duration", "size", "date_added", "error"))
        self.make_tab("removed", ("name", "duration", "size", "date_added"))
        self.notebook.bind("<<NotebookTabChanged>>", self.tab_changed)

    def set_search_query(self, search_query: str) -> None:
        normalized = search_query.strip()
        if normalized == self.search_query:
            return
        self.search_query = normalized
        self.page_numbers = {kind: 0 for kind in self.PAGE_SIZES}
        self.table_revisions = {kind: -1 for kind in self.PAGE_SIZES}
        self.count_revision = -1

    def set_avatar(self) -> None:
        self.avatar_image = self.avatar_store.get(self.channel, 104)
        self.avatar_label.configure(image=self.avatar_image)

    def make_tab(self, kind: str, columns: tuple[str, ...]) -> None:
        page = ttk.Frame(self.notebook, padding=(0, 8, 0, 0), style="Content.TFrame")
        self.tab_indexes[kind] = len(self.tab_indexes)
        icon_name = "success" if kind == "downloaded" else "remove" if kind == "removed" else kind
        self.notebook.add(
            page, text=kind.capitalize(), image=self.icons.get(icon_name), compound="left",
        )
        table = MediaTable(
            page, columns,
            lambda column, selected=kind: self.sort_list(selected, column),
        )
        self.tables[kind] = table
        table.pack(fill="both", expand=True)
        controls = ttk.Frame(page, style="Content.TFrame")
        controls.pack(fill="x", pady=(6, 0))
        if kind == "downloaded":
            ttk.Button(controls, text="Redownload selected", command=self.redownload).pack(side="left")
        elif kind == "failed":
            ttk.Button(controls, text="Retry failed", command=self.retry_failed).pack(side="left")
        if kind in {"queue", "failed"}:
            ttk.Button(
                controls, text="Remove selected",
                command=lambda selected=kind: self.remove_selected_files(selected),
            ).pack(side="left", padx=(0 if kind == "queue" else 6, 0))
        elif kind == "removed":
            ttk.Button(
                controls, text="Return to queue", command=self.restore_selected_files,
            ).pack(side="left")
        if kind != "removed":
            IconButton(
                controls, "priority-up", "Raise selected files priority",
                command=lambda selected=kind: self.shift_selected_file_priority(selected, -1),
            ).pack(side="left", padx=(0 if kind == "queue" else 6, 0))
            IconButton(
                controls, "priority-down", "Lower selected files priority",
                command=lambda selected=kind: self.shift_selected_file_priority(selected, 1),
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

    def selected_tab(self) -> str:
        selected_index = self.notebook.index(self.notebook.select())
        return next(kind for kind, index in self.tab_indexes.items() if index == selected_index)

    def tab_changed(self, _event: Any = None) -> None:
        kind = self.selected_tab()
        self.table_revisions[kind] = -1
        self.refresh(self.cached_positions)

    def sort_list(self, kind: str, column: str) -> None:
        if self.sort_columns[kind] == column:
            self.sort_descending[kind] = not self.sort_descending[kind]
        else:
            self.sort_columns[kind] = column
            self.sort_descending[kind] = False
        self.page_numbers[kind] = 0
        self.table_revisions[kind] = -1
        self.tables[kind].set_sort(column, self.sort_descending[kind])
        self.refresh(self.cached_positions)

    def rescan(self) -> None:
        self.engine.command("rescan", channel_id=self.channel.id)

    def shift_channel_priority(self, amount: int) -> None:
        self.db.shift_channel_priority(self.channel.id, amount)
        self.channel = next(channel for channel in self.db.channels() if channel.id == self.channel.id)
        self.refresh(self.db.queue_positions())

    def shift_selected_file_priority(self, kind: str, amount: int) -> None:
        ids = self.tables[kind].selected_ids()
        if not ids:
            messagebox.showinfo(
                "Set priority", "Select one or more files first.", parent=self,
            )
            return
        self.db.shift_media_priority(ids, amount)
        self.table_revisions[kind] = -1
        self.refresh(self.db.queue_positions())

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
        ids = self.tables["failed"].selected_ids() or self.db.media_ids_by_status(
            self.channel.id, "failed", self.search_query,
        )
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

    def remove_selected_files(self, kind: str) -> None:
        ids = self.tables[kind].selected_ids()
        if not ids:
            messagebox.showinfo("Remove files", "Select one or more files first.", parent=self)
            return
        if messagebox.askyesno(
            "Remove files",
            "Move the selected files to Removed? They will not be downloaded unless returned to the queue.",
            parent=self,
        ):
            # Persist first so a nearly-complete active download cannot win the race.
            self.db.remove_media(ids)
            self.engine.command("remove_media", channel_id=self.channel.id, media_ids=ids)
            self.refresh(self.db.queue_positions())

    def restore_selected_files(self) -> None:
        ids = self.tables["removed"].selected_ids()
        if not ids:
            messagebox.showinfo("Return files", "Select one or more removed files first.", parent=self)
            return
        self.engine.command("restore_media", channel_id=self.channel.id, media_ids=ids)

    def record_count(self, kind: str) -> int:
        if kind == "queue":
            return self.db.queue_count(self.channel.id, self.search_query)
        return self.db.status_count(self.channel.id, kind, self.search_query)

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
        priority = priority_name(self.channel.priority_level)
        self.channel_priority_up_button.configure(
            state="disabled" if priority == "high" else "normal",
        )
        self.channel_priority_down_button.configure(
            state="disabled" if priority == "low" else "normal",
        )
        revision = self.db.revision
        if revision != self.count_revision:
            self.cached_counts = self.db.tab_counts(self.channel.id, self.search_query)
            self.count_revision = revision
        counts = self.cached_counts
        title = f"{self.channel.title} (Paused)" if self.channel.paused else self.channel.title
        self.title_text.set(title)
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
            if media_id != self.last_active_id or (
                self.speed_samples and done < self.speed_samples[-1][1]
            ):
                self.speed_samples.clear()
            self.last_active_id = media_id
            if self.channel.paused:
                self.speed_samples.clear()
                self.speed_samples.append((now, done))
                speed = 0.0
            else:
                self.speed_samples.append((now, done))
                cutoff = now - self.SPEED_WINDOW_SECONDS
                while len(self.speed_samples) > 1 and self.speed_samples[1][0] <= cutoff:
                    self.speed_samples.popleft()
                sample_time, sample_bytes = self.speed_samples[0]
                elapsed = now - sample_time
                speed = max(0, done - sample_bytes) / elapsed if elapsed > 0 else 0.0
            percent = min(100, round(done * 100 / size)) if size else 0
            prefix = "Current file (paused)" if self.channel.paused else "Current file"
            self.current.set(f"{prefix}: {active['file_name']}")
            self.progress.set_value(percent)
            self.progress_text.set(f"{percent}%    {format_bytes(done)} / {format_bytes(size)}")
            if self.channel.paused:
                self.speed_text.set("Paused")
            else:
                self.speed_text.set(f"{format_bytes(round(speed))}/s")
        else:
            self.last_active_id = None
            self.speed_samples.clear()
            self.current.set("Current file: Channel paused" if self.channel.paused else "Current file: Waiting for next file")
            self.progress.set_value(0)
            self.progress_text.set("0%")
            self.speed_text.set("Paused" if self.channel.paused else "0 B/s")
        self.notebook.set_texts({
            self.tab_indexes[kind]: f"{kind.capitalize()} ({count})"
            for kind, count in counts.items()
        })
        kind = self.selected_tab()
        if self.table_revisions[kind] == revision:
            return
        count = counts[kind]
        page_size = self.PAGE_SIZES[kind]
        page_count = self.pages(count, page_size)
        self.page_numbers[kind] = min(self.page_numbers[kind], page_count - 1)
        offset = self.page_numbers[kind] * page_size
        if kind == "queue":
            rows = self.db.queued_media_for_channel(
                self.channel.id, page_size, offset, self.search_query,
                self.sort_columns[kind], self.sort_descending[kind],
            )
        elif kind == "downloaded":
            rows = self.db.downloaded_media_for_channel(
                self.channel.id, page_size, offset, self.search_query,
                self.sort_columns[kind], self.sort_descending[kind],
            )
        elif kind == "removed":
            rows = self.db.removed_media_for_channel(
                self.channel.id, page_size, offset, self.search_query,
                self.sort_columns[kind], self.sort_descending[kind],
            )
        else:
            rows = self.db.failed_media_for_channel(
                self.channel.id, page_size, offset, self.search_query,
                self.sort_columns[kind], self.sort_descending[kind],
            )
        self.tables[kind].populate(rows, positions, self.channel.paused)
        self.page_labels[kind].set(f"Page {self.page_numbers[kind] + 1} of {page_count}")
        self.table_revisions[kind] = revision


class ConnectionPill(tk.Canvas):
    """Supersampled connection pill with smooth curves and a health dot."""

    COLORS = {
        "connected": ("#e6f4ea", "#137333"),
        "connecting": ("#eef1f4", "#5f6368"),
        "disconnected": ("#fce8e6", "#b3261e"),
    }

    def __init__(self, parent: tk.Misc):
        super().__init__(
            parent, height=28, width=100, background=CONTENT_BACKGROUND,
            highlightthickness=0, borderwidth=0,
        )
        self.rendered_image: Any = None
        self.set_status("Connecting…", "connecting")

    def set_status(self, text: str, state: str) -> None:
        background, foreground = self.COLORS[state]
        scale = 4
        height = 28
        try:
            font = ImageFont.truetype("segoeuib.ttf", 12 * scale)
        except OSError:
            font = ImageFont.load_default(size=12 * scale)
        measuring = Image.new("RGBA", (1, 1))
        text_bounds = ImageDraw.Draw(measuring).textbbox((0, 0), text, font=font)
        text_width = text_bounds[2] - text_bounds[0]
        text_height = text_bounds[3] - text_bounds[1]
        width = round(text_width / scale) + 38
        rendered = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
        draw = ImageDraw.Draw(rendered)
        inset = scale
        draw.rounded_rectangle(
            (inset, inset, width * scale - inset - 1, height * scale - inset - 1),
            radius=(height // 2 - 1) * scale,
            fill=background, outline=foreground, width=scale,
        )
        dot_x, dot_y, dot_radius = 13 * scale, height * scale // 2, 3 * scale
        draw.ellipse(
            (dot_x - dot_radius, dot_y - dot_radius, dot_x + dot_radius, dot_y + dot_radius),
            fill=foreground,
        )
        text_x = 22 * scale
        text_y = (height * scale - text_height) / 2 - text_bounds[1]
        draw.text((text_x, text_y), text, fill=foreground, font=font)
        rendered = rendered.resize((width, height), Image.Resampling.LANCZOS)
        self.rendered_image = ImageTk.PhotoImage(rendered, master=self)
        self.configure(width=width, height=height)
        self.delete("all")
        self.create_image(0, 0, image=self.rendered_image, anchor="nw")


class SidebarChannelItem(tk.Frame):
    """A compact navigation row for selecting the active channel workspace."""

    def __init__(
        self, parent: tk.Misc, channel_id: int, command: Any,
        avatar_store: AvatarStore,
    ):
        super().__init__(parent, background=SIDEBAR_BACKGROUND, cursor="hand2")
        self.channel_id = channel_id
        self.command = command
        self.avatar_store = avatar_store
        self.priority = "medium"
        self.selected = False
        self.summary_signature: tuple[Any, ...] | None = None
        self.paint_signature: tuple[str, str, str, str] | None = None
        self.marker = tk.Frame(self, width=7, background=SIDEBAR_BACKGROUND)
        self.marker.pack(side="left", fill="y")
        self.avatar_label = tk.Label(
            self, background=SIDEBAR_BACKGROUND, borderwidth=0,
        )
        self.avatar_label.pack(side="left", padx=(10, 0))
        self.text_area = tk.Frame(self, background=SIDEBAR_BACKGROUND)
        self.text_area.pack(side="left", fill="both", expand=True, padx=(9, 8), pady=10)
        self.title_row = tk.Frame(self.text_area, background=SIDEBAR_BACKGROUND)
        self.title_row.pack(fill="x")
        self.title_label = tk.Label(
            self.title_row, anchor="w", background=SIDEBAR_BACKGROUND,
            foreground=TEXT_COLOR, font=("Segoe UI Semibold", 10),
        )
        self.title_label.pack(side="left")
        self.active_indicator = ActiveDownloadIndicator(self.title_row)
        self.detail_label = tk.Label(
            self.text_area, anchor="w", background=SIDEBAR_BACKGROUND,
            foreground=MUTED_TEXT, font=("Segoe UI", 8),
        )
        self.detail_label.pack(fill="x", pady=(2, 0))
        for widget in (
            self, self.marker, self.avatar_label, self.text_area, self.title_row,
            self.title_label, self.active_indicator, self.detail_label,
        ):
            widget.bind("<Button-1>", self._activate, add="+")
            widget.bind("<Enter>", self._hover_on, add="+")
            widget.bind("<Leave>", self._hover_off, add="+")

    def _activate(self, _event: Any = None) -> None:
        self.command(self.channel_id)

    def _hover_on(self, _event: Any = None) -> None:
        if not self.selected:
            self._paint("#e2e7ec", TEXT_COLOR, MUTED_TEXT)

    def _hover_off(self, _event: Any = None) -> None:
        self.set_selected(self.selected)

    def _paint(self, background: str, title_color: str, detail_color: str) -> None:
        signature = (background, title_color, detail_color, self.priority)
        if signature == self.paint_signature:
            return
        self.paint_signature = signature
        self.configure(background=background)
        self.avatar_label.configure(background=background)
        self.text_area.configure(background=background)
        self.title_row.configure(background=background)
        self.title_label.configure(background=background, foreground=title_color)
        self.active_indicator.set_background(background)
        self.detail_label.configure(background=background, foreground=detail_color)
        self.marker.configure(background=PRIORITY_COLORS[self.priority] or background)

    def update_channel(
        self, channel: Channel, queued: int, downloading: bool, force: bool = False,
        match_count: int | None = None,
    ) -> None:
        signature = (channel, int(queued), bool(downloading), match_count)
        if not force and signature == self.summary_signature:
            return
        self.summary_signature = signature
        self.priority = priority_name(channel.priority_level)
        self.channel = channel
        self.avatar_image = self.avatar_store.get(channel, 34)
        self.avatar_label.configure(image=self.avatar_image)
        title = channel.title if match_count is None else f"{channel.title} ({match_count:,})"
        self.title_label.configure(text=title)
        self.detail_label.configure(
            text="Paused" if channel.paused else f"{queued:,} queued",
        )
        self.active_indicator.set_active(downloading)
        self.set_selected(self.selected)

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        if selected:
            self._paint(ACCENT, "#ffffff", "#dceaff")
        else:
            self._paint(SIDEBAR_BACKGROUND, TEXT_COLOR, MUTED_TEXT)


class DownloaderApp(tk.Tk):
    EVENT_BUDGET_SECONDS = 0.008
    MAX_EVENTS_PER_POLL = 100

    def __init__(
        self, config: Config, db: Database, performance_report: Path | None = None,
        performance_label: str = "",
    ):
        super().__init__()
        configure_application_theme(self)
        self.app_icon_images = apply_application_icon(self)
        self.icons = load_icons(self)
        self.db = db
        self.avatar_store = AvatarStore(self, db.path.parent / "avatars")
        self.config = config
        self.events: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue()
        self.cards: dict[int, ChannelPanel] = {}
        self.sidebar_items: dict[int, SidebarChannelItem] = {}
        self.selected_channel_id: int | None = None
        self.add_channel_dialog: AddChannelDialog | None = None
        self.paused = False
        self.authenticated = False
        self.authentication_busy = True
        self.closing = False
        self.poll_job: str | None = None
        self.performance_job: str | None = None
        self.performance: UiPerformanceRecorder | None = None
        self.cards_revision = -1
        self.position_revision = -1
        self.cached_positions: dict[int, int] = {}
        self.search_query = ""
        self.search_job: str | None = None
        self.search_text = tk.StringVar()
        self.engine = DownloaderEngine(config, db, self._enqueue_event)
        self.title("Channel Media Downloader")
        self.geometry("1280x820")
        self.minsize(900, 600)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.build_toolbar()
        self.build_scroller()
        self.sync_cards()
        if performance_report is not None:
            self.performance = UiPerformanceRecorder(performance_report, performance_label)
        self.poll_job = self.after(100, self.poll)
        if self.performance is not None:
            self.performance_job = self.after(HEARTBEAT_INTERVAL_MS, self._performance_heartbeat)
        self.engine.start()

    def _performance_heartbeat(self) -> None:
        if self.closing or self.performance is None:
            return
        self.performance.record_heartbeat()
        self.performance_job = self.after(HEARTBEAT_INTERVAL_MS, self._performance_heartbeat)

    def _enqueue_event(self, name: str, payload: dict[str, Any]) -> None:
        # Progress and queue changes are already represented in SQLite and are
        # sampled by poll(). Queueing one notification per chunk or scanned row
        # only makes the Tk thread repeat work it was going to do anyway.
        if name in {"progress", "queue_changed"}:
            return
        self.events.put((name, payload))

    def build_toolbar(self) -> None:
        toolbar = ttk.Frame(self, padding=(16, 10), style="Topbar.TFrame")
        self.toolbar = toolbar
        toolbar.pack(fill="x")
        if self.app_icon_images:
            ttk.Label(
                toolbar, image=self.app_icon_images[-1], style="Topbar.TLabel",
            ).pack(side="left", padx=(0, 9))
        ttk.Label(
            toolbar, text="Telegram Downloader", style="Brand.TLabel",
        ).pack(side="left", padx=(0, 22))
        self.add_button = IconButton(toolbar, "add", "Add channel", command=self.add_channel)
        self.add_button.pack(side="left")
        self.pause_button = IconButton(
            toolbar, "pause", "Pause downloads", command=self.toggle_pause,
        )
        self.pause_button.pack(side="left", padx=(6, 0))
        IconButton(
            toolbar, "settings", "Settings", command=self.open_settings,
        ).pack(side="left", padx=(6, 0))
        ttk.Label(toolbar, text="Speed limit", style="Topbar.TLabel").pack(side="left", padx=(18, 6))
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
        self.auth_button = ttk.Button(
            toolbar, text="Sign in", command=self.toggle_authentication, state="disabled",
        )
        self.auth_button.pack(side="right", padx=(6, 10))
        self.account_status = tk.StringVar(value="Checking account…")
        ttk.Label(toolbar, textvariable=self.account_status, style="Topbar.TLabel").pack(side="right")
        self.error_message = tk.StringVar()
        self.error_banner = ttk.Label(
            self, textvariable=self.error_message, padding=(16, 7),
            wraplength=1150, style="Error.TLabel",
        )
        self.error_message.trace_add("write", self._update_error_banner)

    def _update_error_banner(self, *_args: Any) -> None:
        if self.error_message.get().strip():
            if not self.error_banner.winfo_manager():
                self.error_banner.pack(fill="x", after=self.toolbar)
        elif self.error_banner.winfo_manager():
            self.error_banner.pack_forget()

    def search_changed(self, *_args: Any) -> None:
        if self.search_job is not None:
            self.after_cancel(self.search_job)
        self.search_job = self.after(250, self.apply_scheduled_search)

    def apply_scheduled_search(self) -> None:
        self.search_job = None
        self.apply_search()

    def apply_search(self, _event: Any = None) -> None:
        if self.search_job is not None:
            self.after_cancel(self.search_job)
            self.search_job = None
        query = self.search_text.get().strip()
        self.clear_search_button.configure(state="normal" if query else "disabled")
        if query == self.search_query:
            return
        self.search_query = query
        self.sync_cards(force=True)
        if self.selected_channel_id in self.cards:
            self.cards[self.selected_channel_id].refresh(self.cached_positions)

    def clear_search(self, _event: Any = None) -> str:
        self.search_text.set("")
        self.apply_search()
        self.search_entry.focus_set()
        return "break"

    def build_scroller(self) -> None:
        container = ttk.Frame(self, style="App.TFrame")
        container.pack(fill="both", expand=True)
        sidebar = ttk.Frame(container, width=248, style="Sidebar.TFrame")
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)
        sidebar_heading = ttk.Frame(sidebar, padding=(16, 17, 12, 8), style="Sidebar.TFrame")
        sidebar_heading.pack(fill="x")
        ttk.Label(
            sidebar_heading, text="CHANNELS", style="SidebarTitle.TLabel",
        ).pack(side="left")
        self.channel_count = ttk.Label(
            sidebar_heading, text="0", style="SidebarTitle.TLabel",
        )
        self.channel_count.pack(side="right")
        search_area = ttk.Frame(sidebar, padding=(16, 0, 12, 10), style="Sidebar.TFrame")
        search_area.pack(fill="x")
        ttk.Label(
            search_area, text="SEARCH FILE NAMES", style="SidebarTitle.TLabel",
        ).pack(anchor="w", pady=(0, 5))
        search_row = ttk.Frame(search_area, style="Sidebar.TFrame")
        search_row.pack(fill="x")
        self.search_entry = ttk.Entry(search_row, textvariable=self.search_text)
        self.search_entry.pack(side="left", fill="x", expand=True)
        self.search_entry.bind("<Return>", self.apply_search)
        self.search_entry.bind("<Escape>", self.clear_search)
        self.search_text.trace_add("write", self.search_changed)
        self.clear_search_button = ttk.Button(
            search_row, text="X", width=3, command=self.clear_search, state="disabled",
        )
        self.clear_search_button.pack(side="left", padx=(3, 0))
        self.sidebar_canvas = tk.Canvas(
            sidebar, background=SIDEBAR_BACKGROUND, highlightthickness=0, borderwidth=0,
        )
        sidebar_scrollbar = ttk.Scrollbar(
            sidebar, orient="vertical", command=self.sidebar_canvas.yview,
        )
        self.sidebar_list = tk.Frame(self.sidebar_canvas, background=SIDEBAR_BACKGROUND)
        self.sidebar_window = self.sidebar_canvas.create_window(
            (0, 0), window=self.sidebar_list, anchor="nw",
        )
        self.sidebar_list.bind(
            "<Configure>",
            lambda _event: self.sidebar_canvas.configure(scrollregion=self.sidebar_canvas.bbox("all")),
        )
        self.sidebar_canvas.bind(
            "<Configure>",
            lambda event: self.sidebar_canvas.itemconfigure(self.sidebar_window, width=event.width),
        )
        self.sidebar_canvas.configure(yscrollcommand=sidebar_scrollbar.set)
        self.sidebar_canvas.pack(side="left", fill="both", expand=True)
        sidebar_scrollbar.pack(side="right", fill="y")

        tk.Frame(container, width=1, background=BORDER_COLOR).pack(side="left", fill="y")
        self.body = ttk.Frame(container, style="Content.TFrame")
        self.body.pack(side="left", fill="both", expand=True)
        self.empty_state = ttk.Frame(self.body, padding=40, style="Content.TFrame")
        ttk.Label(
            self.empty_state, text="No channels yet", style="ChannelTitle.TLabel",
        ).pack(pady=(0, 7))
        ttk.Label(
            self.empty_state,
            text="Add a Telegram channel to inspect its files and begin downloading.",
            style="Muted.TLabel",
        ).pack(pady=(0, 18))
        ttk.Button(
            self.empty_state, text="Add channel", command=self.add_channel,
            style="Primary.TButton",
        ).pack()

    def sync_cards(self, force: bool = False) -> None:
        revision = self.db.revision
        if not force and revision == self.cards_revision:
            return
        overviews = self.db.channel_overviews(True)
        match_counts = (
            self.db.channel_match_counts(self.search_query, enabled_only=True)
            if self.search_query else {}
        )
        enabled_channels = {channel.id: channel for channel, _, _ in overviews}
        for channel_id in list(self.cards):
            if channel_id not in enabled_channels:
                self.cards.pop(channel_id).destroy()
                item = self.sidebar_items.pop(channel_id, None)
                if item is not None:
                    item.destroy()
        for channel, queued, downloading in overviews:
            if channel.id not in self.cards:
                card = ChannelPanel(
                    self.body, channel, self.db, self.engine, self.avatar_store,
                )
                self.cards[channel.id] = card
                item = SidebarChannelItem(
                    self.sidebar_list, channel.id, self.select_channel, self.avatar_store,
                )
                item.pack(fill="x", padx=8, pady=2)
                self.sidebar_items[channel.id] = item
            else:
                card = self.cards[channel.id]
                if card.channel != channel:
                    card.channel = channel
                    card.set_avatar()
            card.set_search_query(self.search_query)
            self.sidebar_items[channel.id].update_channel(
                channel, queued, downloading,
                match_count=match_counts.get(channel.id, 0) if self.search_query else None,
            )
        self.channel_count.configure(text=str(len(overviews)))
        if self.selected_channel_id not in enabled_channels:
            self.selected_channel_id = overviews[0][0].id if overviews else None
        self._show_selected_channel()
        self.cards_revision = revision

    def select_channel(self, channel_id: int) -> None:
        if channel_id not in self.cards:
            return
        self.selected_channel_id = channel_id
        self._show_selected_channel()
        self.cards[channel_id].refresh(self.cached_positions)

    def _show_selected_channel(self) -> None:
        for channel_id, card in self.cards.items():
            if channel_id == self.selected_channel_id:
                if not card.winfo_manager():
                    card.pack(fill="both", expand=True)
            elif card.winfo_manager():
                card.pack_forget()
        for channel_id, item in self.sidebar_items.items():
            item.set_selected(channel_id == self.selected_channel_id)
        if self.selected_channel_id is None:
            if not self.empty_state.winfo_manager():
                self.empty_state.place(relx=0.5, rely=0.42, anchor="center")
        else:
            self.empty_state.place_forget()

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

    def toggle_authentication(self) -> None:
        if self.authentication_busy:
            return
        if self.authenticated:
            if not messagebox.askyesno(
                "Sign out",
                "Sign out of Telegram on this computer?\n\n"
                "Active downloads will stop and remain queued. Your channel history and files will remain.",
                parent=self,
            ):
                return
            self._set_authentication_ui("signing_out")
            if not self.engine.command("sign_out"):
                self._authentication_command_failed()
            return
        phone = simpledialog.askstring(
            "Sign in to Telegram",
            "Enter your phone number in international format, for example +15551234567:",
            parent=self,
        )
        if phone is None:
            return
        self._set_authentication_ui("signing_in")
        if not self.engine.command("request_sign_in", phone=phone):
            self._authentication_command_failed()

    def _prompt_sign_in_code(self) -> None:
        code = simpledialog.askstring(
            "Telegram verification code",
            "Enter the login code Telegram sent to your account:",
            parent=self,
        )
        if code is None:
            self.engine.command("cancel_sign_in")
            return
        if not self.engine.command("submit_sign_in_code", code=code):
            self._authentication_command_failed()

    def _prompt_sign_in_password(self) -> None:
        password = simpledialog.askstring(
            "Telegram two-step verification",
            "Enter your Telegram two-step verification password:",
            show="*", parent=self,
        )
        if password is None:
            self.engine.command("cancel_sign_in")
            return
        if not self.engine.command("submit_sign_in_password", password=password):
            self._authentication_command_failed()

    def _authentication_command_failed(self) -> None:
        self._set_authentication_ui("signed_out")
        self.error_message.set("The Telegram worker is not ready. Try again or restart the application.")

    def _set_authentication_ui(self, status: str, account: str = "") -> None:
        self.authenticated = status == "signed_in"
        self.authentication_busy = status in {"starting", "signing_in", "signing_out"}
        self.auth_button.configure(
            text="Sign out" if self.authenticated else "Sign in",
            state="disabled" if self.authentication_busy else "normal",
        )
        self.add_button.configure(state="normal" if self.authenticated else "disabled")
        self.pause_button.configure(state="normal" if self.authenticated else "disabled")
        if status == "signed_in":
            self.account_status.set(f"Signed in as {account}" if account else "Signed in")
        elif status == "signing_in":
            self.account_status.set("Signing in…")
        elif status == "signing_out":
            self.account_status.set("Signing out…")
        elif status == "starting":
            self.account_status.set("Checking account…")
        else:
            self.account_status.set("Signed out")

    def handle_event(self, event: str, data: dict[str, Any]) -> None:
        if event == "fatal":
            self.connection.set_status("Not connected", "disconnected")
            self._set_authentication_ui("signed_out")
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
        elif event == "authentication":
            status = str(data.get("status", "signed_out"))
            username = str(data.get("username", "") or "")
            display_name = str(data.get("display_name", "") or "")
            account = f"@{username}" if username else display_name
            self._set_authentication_ui(status, account)
            if status == "signed_in":
                self.error_message.set("")
        elif event == "authentication_code_requested":
            self._prompt_sign_in_code()
        elif event == "authentication_password_required":
            self._prompt_sign_in_password()
        elif event == "authentication_error":
            if str(data.get("stage", "")) not in {"sign_out", "already_signed_in"}:
                self._set_authentication_ui("signed_out")
            messagebox.showerror(
                "Telegram authentication",
                str(data.get("error", "Authentication failed.")),
                parent=self,
            )
        elif event == "channel_added":
            self.sync_cards()
        elif event == "channel_removed":
            self.sync_cards()
        elif event == "avatar_ready":
            channel_id = int(data.get("channel_id", 0) or 0)
            self.avatar_store.invalidate(channel_id)
            card = self.cards.get(channel_id)
            item = self.sidebar_items.get(channel_id)
            if card is not None:
                card.set_avatar()
            if item is not None and hasattr(item, "channel"):
                item.update_channel(
                    item.channel, self.db.queue_count(channel_id),
                    self.db.active_download(channel_id) is not None, force=True,
                )
        elif event == "channel_types_progress":
            dialog = self.add_channel_dialog
            if dialog is not None and dialog.winfo_exists():
                dialog.receive_file_type_progress(data)
        elif event == "channel_types":
            dialog = self.add_channel_dialog
            if dialog is not None and dialog.winfo_exists():
                dialog.receive_file_types(data)

    def poll(self) -> None:
        if self.closing:
            return
        started = time.perf_counter()
        queue_before = self.events.qsize()
        events_handled = 0
        while events_handled < self.MAX_EVENTS_PER_POLL:
            if time.perf_counter() - started >= self.EVENT_BUDGET_SECONDS:
                break
            try:
                event, data = self.events.get_nowait()
            except queue.Empty:
                break
            self.handle_event(event, data)
            events_handled += 1
        events_finished = time.perf_counter()
        self.sync_cards()
        cards_finished = time.perf_counter()
        revision = self.db.revision
        if revision != self.position_revision:
            self.cached_positions = self.db.queue_positions()
            self.position_revision = revision
        positions_finished = time.perf_counter()
        if self.selected_channel_id in self.cards:
            self.cards[self.selected_channel_id].refresh(self.cached_positions)
        finished = time.perf_counter()
        if self.performance is not None:
            self.performance.record_poll(
                total_ms=(finished - started) * 1000,
                events_ms=(events_finished - started) * 1000,
                cards_ms=(cards_finished - events_finished) * 1000,
                positions_ms=(positions_finished - cards_finished) * 1000,
                panel_ms=(finished - positions_finished) * 1000,
                events_handled=events_handled,
                queue_before=queue_before,
                queue_after=self.events.qsize(),
            )
        self.poll_job = self.after(1 if not self.events.empty() else 1000, self.poll)

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
        if self.performance_job is not None:
            try:
                self.after_cancel(self.performance_job)
            except tk.TclError:
                pass
            self.performance_job = None
        try:
            self.engine.stop()
        finally:
            try:
                if self.performance is not None:
                    self.performance.write_report()
            finally:
                self.destroy()


def create_application(
    config: Config, db: Database, performance_report: Path | None = None,
    performance_label: str = "",
) -> DownloaderApp:
    set_windows_app_identity()
    return DownloaderApp(config, db, performance_report, performance_label)
