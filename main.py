import sys
import os

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(__file__))

from app.gui.main_window import MainWindow

if __name__ == "__main__":
    app = MainWindow()
    app.mainloop()
