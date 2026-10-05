"""Shared helpers for the receipt and expense API tests."""

import httpx
from fastapi.testclient import TestClient

# The smallest byte strings that pass the upload's type check (first bytes only).
JPEG = b"\xff\xd8\xff\xe0fake-jpeg-bytes\x00\x01"
PNG = b"\x89PNG\r\n\x1a\nfake-png"
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 fake"


def upload(
    client: TestClient,
    data: bytes = JPEG,
    filename: str = "receipt.jpg",
    content_type: str = "image/jpeg",
) -> httpx.Response:
    return client.post("/api/receipts", files={"file": (filename, data, content_type)})
