"""Image preparation like the frontend's resize.js (`eval/images.py`)."""

import io
import sys
from pathlib import Path

import pytest
from PIL import Image

from eval import images
from eval.images import MAX_LONG_SIDE, prepare_image
from tests.eval.conftest import png

ORIENTATION = 0x0112


def test_a_small_photo_is_sent_as_it_is(tmp_path: Path) -> None:
    path = tmp_path / "small.PNG"
    path.write_bytes(png((1, 2, 3), (300, 400)))

    data, mime = prepare_image(path)

    assert (data, mime) == (path.read_bytes(), "image/png")


@pytest.mark.parametrize(("suffix", "mime"), [(".jpg", "image/jpeg"), (".webp", "image/webp")])
def test_without_resizing_the_original_bytes_are_sent(
    tmp_path: Path, suffix: str, mime: str
) -> None:
    path = tmp_path / f"photo{suffix}"
    path.write_bytes(b"not even an image")

    assert prepare_image(path, resize=False) == (b"not even an image", mime)


def test_a_large_photo_is_downsized_to_a_jpeg(tmp_path: Path) -> None:
    path = tmp_path / "big.png"
    path.write_bytes(png((10, 20, 30), (3000, 1500)))

    data, mime = prepare_image(path)

    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(data)) as image:
        assert image.format == "JPEG"
        assert image.size == (MAX_LONG_SIDE, 1000)


def test_a_heavy_photo_under_the_size_limit_is_reencoded_without_upscaling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(images, "RESIZE_ABOVE_BYTES", 10)
    path = tmp_path / "heavy.png"
    path.write_bytes(png((10, 20, 30), (40, 30)))

    data, mime = prepare_image(path)

    assert mime == "image/jpeg"
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (40, 30)


def test_the_exif_orientation_is_applied(tmp_path: Path) -> None:
    exif = Image.Exif()
    exif[ORIENTATION] = 6  # rotate 90 degrees clockwise to view
    out = io.BytesIO()
    Image.new("RGB", (2400, 1200), (200, 200, 200)).save(out, format="JPEG", exif=exif)
    path = tmp_path / "rotated.jpg"
    path.write_bytes(out.getvalue())

    data, _ = prepare_image(path)

    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (1000, MAX_LONG_SIDE)


def test_a_transparent_photo_gets_a_white_background(tmp_path: Path) -> None:
    out = io.BytesIO()
    Image.new("RGBA", (2200, 100), (0, 0, 0, 0)).save(out, format="PNG")
    path = tmp_path / "clear.png"
    path.write_bytes(out.getvalue())

    data, _ = prepare_image(path)

    with Image.open(io.BytesIO(data)) as image:
        assert image.mode == "RGB"
        pixel = image.getpixel((10, 10))
        assert isinstance(pixel, tuple) and min(pixel) > 240


def test_without_pillow_resizing_exits_with_an_install_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "photo.png"
    path.write_bytes(png((1, 1, 1)))
    monkeypatch.setitem(sys.modules, "PIL", None)

    with pytest.raises(SystemExit) as caught:
        prepare_image(path)

    assert caught.value.code == 2
    assert "install the eval extra" in capsys.readouterr().err
    assert prepare_image(path, resize=False)[1] == "image/png"
