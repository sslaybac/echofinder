import sys

from PyQt6.QtWidgets import QApplication

from echofinder.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Echofinder")
    app.setOrganizationName("Echofinder")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
