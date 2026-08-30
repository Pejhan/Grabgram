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
    parser = argparse.ArgumentParser(description="Channel Audio Downloader desktop application")
    parser.add_argument("--config", type=Path, default=Path("config.json"))
    args = parser.parse_args()
    if __package__:
        from .ui import create_application
    else:
        from ui import create_application

    instance_lock = None
    try:
        config = load_config(args.config.resolve())
        instance_lock = InstanceLock(config.database_file.with_suffix(".instance.lock"))
        database = Database(config.database_file)
        application = create_application(config, database)
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
