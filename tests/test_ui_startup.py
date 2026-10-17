from pathlib import Path
import asyncio
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from grabgram.config import Config
from grabgram.database import Database
from grabgram.ui import create_application


class UiStartupTests(unittest.TestCase):
    def test_populated_app_opens_and_renders_all_file_tabs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = Database(root / "ledger.sqlite3")
            channel_id = db.add_channel("test", 123, "Test", "Test", 0, 0)
            channel = db.channels()[0]
            for message_id in range(1, 5):
                db.add_media(
                    channel=channel, message_id=message_id, document_id=message_id,
                    file_name=f"clip-{message_id}.mp4", size_bytes=1_000,
                    duration_seconds=0, message_date="", priority=10,
                )
            rows = {row["message_id"]: row for row in db.media_for_channel(channel_id)}
            db.mark_downloading(rows[2]["id"])
            db.mark_downloaded(rows[2]["id"], str(root / "clip-2.mp4"), 1_000)
            db.mark_downloading(rows[3]["id"])
            db.mark_failed(rows[3]["id"], "Example failure")
            db.remove_media([rows[4]["id"]])
            config = Config(1, "test", root / "session", db.path, root / "downloads")
            app = None
            callback_errors = []
            try:
                with patch("grabgram.engine.DownloaderEngine.start"):
                    try:
                        app = create_application(config, db)
                    except tk.TclError as exc:
                        if "couldn't connect to display" in str(exc) or "no display name" in str(exc):
                            self.skipTest("A desktop display is required for the startup test")
                        raise
                    app.report_callback_exception = lambda *error: callback_errors.append(error)
                    app.withdraw()
                    panel = app.cards[channel_id]
                    for kind, table in panel.tables.items():
                        panel.notebook.select(panel.tab_indexes[kind])
                        app.update()
                        panel.refresh({})
                        self.assertEqual(1, len(table.tree.get_children()), kind)
                        self.assertIn("name_mode", table.tree["columns"])
                    item = db.media_for_ids(channel_id, [rows[1]["id"]])[0]
                    db.set_media_filename(
                        item, "message", "Attached title.mp4", None,
                        "clip-1.mp4", "Attached title.mp4",
                    )
                    panel.notebook.select(panel.tab_indexes["queue"])
                    panel.refresh({})
                    tree = panel.tables["queue"].tree
                    tree.selection_set(str(item["id"]))
                    with patch("grabgram.ui.simpledialog.askstring", return_value="Edited title.mp4") as prompt:
                        with patch.object(app.engine, "command", return_value=True) as command:
                            panel.set_selected_filename_mode("queue", "manual")
                    self.assertEqual("Attached title.mp4", prompt.call_args.kwargs["initialvalue"])
                    self.assertEqual("set_media_filename_mode", command.call_args.args[0])
                    asyncio.run(app.engine._set_media_filename_mode(**command.call_args.kwargs))
                    panel.refresh({})
                    self.assertEqual("C", tree.item(str(item["id"]), "values")[1])
                    with patch("grabgram.ui.simpledialog.askstring", return_value=None) as prompt:
                        with patch.object(app.engine, "command") as command:
                            panel.set_selected_filename_mode("queue", "manual")
                    self.assertEqual("Edited title.mp4", prompt.call_args.kwargs["initialvalue"])
                    command.assert_not_called()
                    self.assertFalse(callback_errors, callback_errors)
            finally:
                if app is not None:
                    app.close()
                elif tk._default_root is not None:
                    tk._default_root.destroy()
