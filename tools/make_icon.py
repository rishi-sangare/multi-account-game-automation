"""Render the app emblem into assets/icon.ico (multi-size)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from commander.ui import theme as T  # noqa: E402

app = QGuiApplication(sys.argv)
(ROOT / "assets").mkdir(exist_ok=True)
png = ROOT / "assets" / "icon.png"
T.emblem_pixmap(128, 2.0).save(str(png))
img = Image.open(png).convert("RGBA")
img.save(ROOT / "assets" / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("assets/icon.ico written")
