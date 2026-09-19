from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from .config import load_config
    from .database import Database
    from .instance_lock import InstanceLock
except ImportError:
    from config import load_config
    from database import Database
    from instance_lock import InstanceLock


def main() -> None:
    parser = argparse.ArgumentParser(description="Channel Media Downloader desktop application")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    parser.add_argument(
        "--ui-performance-report", type=Path,
        help="write UI responsiveness measurements to this JSON file when the app closes",
    )
    parser.add_argument(
        "--ui-performance-label", default="",
        help="label stored in the UI performance report (for example: baseline or optimized)",
    )
    args = parser.parse_args()
    try:
        if __package__:
            from .ui import create_application
        else:
            from ui import create_application
    except ModuleNotFoundError as exc:
        if exc.name == "PIL":
            print(
                "Cannot start: Pillow is not installed for this Python interpreter.\n"
                f'Run: "{sys.executable}" -m pip install -r requirements.txt',
                file=sys.stderr,
            )
            raise SystemExit(2) from exc
        raise

    instance_lock = None
    try:
        config = load_config(args.config.resolve())
        instance_lock = InstanceLock(config.database_file.with_suffix(".instance.lock"))
        database = Database(config.database_file)
        application = create_application(
            config, database, args.ui_performance_report, args.ui_performance_label,
        )
        application.mainloop()
        exit_code = 0
    except Exception as exc:
        print(f"Cannot start: {exc}", file=sys.stderr)
        exit_code = 2
    finally:
        if instance_lock is not None:
            instance_lock.close()
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
