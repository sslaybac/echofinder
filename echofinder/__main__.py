import logging
import logging.handlers
import sys
from pathlib import Path

from platformdirs import user_log_dir
from PyQt6.QtWidgets import QApplication

from echofinder.ui.main_window import MainWindow


def _configure_logging() -> None:
    log_dir = Path(user_log_dir("echofinder"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "echofinder.log"

    formatter = logging.Formatter(
        "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=2_000_000, backupCount=3
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stderr)
    stream_handler.setLevel(logging.WARNING)
    stream_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    root_logger.addHandler(file_handler)
    root_logger.addHandler(stream_handler)


def main() -> None:
    _configure_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("Echofinder")
    app.setOrganizationName("Echofinder")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
