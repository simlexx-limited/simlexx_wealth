from io import BytesIO
from typing import Optional

import pytest
from fastapi import HTTPException, UploadFile

from app.routers.more_about_user.check_image_ext import (
    check_file_size,
    check_image_extension,
    detect_file_kind,
)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32
PDF = b"%PDF-1.7\n" + b"\x00" * 32
TEXT = b"this is definitely not an image, just text"


def make_upload(data: bytes, filename: Optional[str], size: Optional[int] = -1) -> UploadFile:
    return UploadFile(
        file=BytesIO(data),
        filename=filename,
        size=len(data) if size == -1 else size,
    )


class TestDetectFileKind:
    @pytest.mark.parametrize("data", [PNG, JPEG, GIF, WEBP])
    def test_images_are_detected(self, data):
        assert detect_file_kind(data[:12]) == "image"

    def test_pdf_is_detected(self):
        assert detect_file_kind(PDF[:12]) == "pdf"

    @pytest.mark.parametrize("data", [TEXT[:12], b"", b"MZ\x90\x00"])
    def test_unknown_content_is_none(self, data):
        assert detect_file_kind(data) is None


class TestCheckImageExtension:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "data,name",
        [(PNG, "me.png"), (JPEG, "me.jpg"), (JPEG, "me.jpeg"), (GIF, "me.gif"), (WEBP, "me.webp")],
    )
    async def test_valid_images_pass(self, data, name):
        await check_image_extension(make_upload(data, name))

    @pytest.mark.asyncio
    async def test_extension_check_is_case_insensitive(self):
        await check_image_extension(make_upload(JPEG, "PHOTO.JPG"))

    @pytest.mark.asyncio
    async def test_unsupported_extension_is_415(self):
        with pytest.raises(HTTPException) as exc:
            await check_image_extension(make_upload(PNG, "me.exe"))
        assert exc.value.status_code == 415

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", [None, "", "noextension"])
    async def test_missing_extension_is_415(self, name):
        with pytest.raises(HTTPException) as exc:
            await check_image_extension(make_upload(PNG, name))
        assert exc.value.status_code == 415

    @pytest.mark.asyncio
    async def test_renamed_non_image_is_rejected_by_content(self):
        """A text file named .jpg has the right extension but the wrong bytes."""
        with pytest.raises(HTTPException) as exc:
            await check_image_extension(make_upload(TEXT, "fake.jpg"))
        assert exc.value.status_code == 415
        assert "content" in exc.value.detail.lower()

    @pytest.mark.asyncio
    async def test_pdf_rejected_for_images_but_allowed_for_documents(self):
        with pytest.raises(HTTPException):
            await check_image_extension(make_upload(PDF, "id.pdf"))
        await check_image_extension(make_upload(PDF, "id.pdf"), allow_pdf=True)

    @pytest.mark.asyncio
    async def test_pdf_extension_with_image_bytes_is_still_checked_by_kind(self):
        await check_image_extension(make_upload(PNG, "id.pdf"), allow_pdf=True)

    @pytest.mark.asyncio
    async def test_oversize_file_is_413(self):
        big = PNG + b"\x00" * (2 * 1024 * 1024)
        with pytest.raises(HTTPException) as exc:
            await check_image_extension(make_upload(big, "me.png"), max_size_mb=1)
        assert exc.value.status_code == 413

    @pytest.mark.asyncio
    async def test_file_at_the_limit_passes(self):
        exact = PNG + b"\x00" * (1024 * 1024 - len(PNG))
        await check_image_extension(make_upload(exact, "me.png"), max_size_mb=1)

    @pytest.mark.asyncio
    async def test_stream_is_rewound_so_the_file_can_still_be_read(self):
        upload = make_upload(PNG, "me.png")
        await check_image_extension(upload)
        assert await upload.read() == PNG


class TestCheckFileSize:
    @pytest.mark.asyncio
    async def test_empty_file_is_400(self):
        with pytest.raises(HTTPException) as exc:
            await check_file_size(make_upload(b"", "me.png"))
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_size_is_measured_when_the_parser_did_not_set_it(self):
        big = b"\x00" * (2 * 1024 * 1024)
        with pytest.raises(HTTPException) as exc:
            await check_file_size(make_upload(big, "x.bin", size=None), max_size_mb=1)
        assert exc.value.status_code == 413

    @pytest.mark.asyncio
    async def test_small_file_passes(self):
        await check_file_size(make_upload(PNG, "me.png"))
