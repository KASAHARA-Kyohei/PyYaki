"""Convert the selected artwork into checked-in app assets; preserve the original."""

from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "assets/branding/pyyaki-icon.png"
ICNS = ROOT / "src/pyyaki/static/pyyaki.icns"
ICO = ROOT / "native/icons/pyyaki.ico"
DOCK = ROOT / "native/icons/pyyaki-dock.png"


def main():
    with Image.open(SOURCE) as original:
        if original.width != original.height or original.width < 1024:
            raise ValueError("Use a square source of at least 1024px")
        # PNG-compressed Windows icons require 32bpp RGBA, even for opaque artwork.
        image = original.convert("RGBA")
    ICNS.parent.mkdir(parents=True, exist_ok=True)
    ICO.parent.mkdir(parents=True, exist_ok=True)
    image.save(ICNS, format="ICNS")
    sizes = [(size, size) for size in (16, 24, 32, 48, 64, 128, 256)]
    image.save(ICO, format="ICO", sizes=sizes)
    image.resize((512, 512), Image.Resampling.LANCZOS).save(DOCK)
    print(f"Exported {ICNS.relative_to(ROOT)}, {ICO.relative_to(ROOT)}, {DOCK.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
