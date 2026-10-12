"""Image preparation like the frontend's `resize.js` (decisions 0027, 0028).

A photo over 2 MB, or with a long side over 2,000 px, becomes a JPEG with the long side
at most 2,000 px (quality 85) and its EXIF orientation applied; anything else is sent as
it is. So the model sees what it sees in the app. Pillow comes from the `eval` extra and
is imported only when an image is resized.
"""

import io
import sys
from pathlib import Path
from types import ModuleType

RESIZE_ABOVE_BYTES = 2 * 1024 * 1024
MAX_LONG_SIDE = 2000
JPEG_QUALITY = 85
MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
EXIT_SETUP = 2
INSTALL_HINT = (
    'Resizing needs Pillow: install the eval extra (pip install -e "./backend[dev,eval]"), '
    "or run with --no-resize."
)


def mime_type(path: Path) -> str:
    """The MIME type from the suffix (the dataset allows only these four)."""
    return MIME_TYPES[path.suffix.lower()]


def _pillow() -> tuple[ModuleType, ModuleType]:
    try:
        from PIL import Image, ImageOps
    except ImportError:
        print(INSTALL_HINT, file=sys.stderr)
        raise SystemExit(EXIT_SETUP) from None
    return Image, ImageOps


def prepare_image(path: Path, resize: bool = True) -> tuple[bytes, str]:
    """The bytes to send and their MIME type."""
    data = path.read_bytes()
    mime = mime_type(path)
    if not resize:
        return data, mime
    image_module, image_ops = _pillow()
    with image_module.open(io.BytesIO(data)) as opened:
        long_side = max(opened.size)
        if len(data) <= RESIZE_ABOVE_BYTES and long_side <= MAX_LONG_SIDE:
            return data, mime
        image = image_ops.exif_transpose(opened)
        scale = min(1.0, MAX_LONG_SIDE / max(image.size))
        size = tuple(max(1, round(side * scale)) for side in image.size)
        if size != image.size:
            image = image.resize(size, image_module.Resampling.LANCZOS)
        if image.mode != "RGB":
            # A transparent PNG or WebP would turn black in a JPEG: white, like resize.js.
            background = image_module.new("RGB", image.size, (255, 255, 255))
            rgba = image.convert("RGBA")
            background.paste(rgba, mask=rgba.getchannel("A"))
            image = background
        out = io.BytesIO()
        image.save(out, format="JPEG", quality=JPEG_QUALITY)
    return out.getvalue(), "image/jpeg"
