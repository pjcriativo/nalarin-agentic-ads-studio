"""Tests for file upload functionality including video support."""
import pytest
from io import BytesIO
from unittest.mock import patch, MagicMock

# Minimal payloads carrying the real magic bytes the upload endpoint sniffs.
# (The endpoint rejects content whose signature doesn't match its extension.)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 32
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32
MOV = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 32
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 32
AVI = b"RIFF\x00\x00\x00\x00AVI LIST" + b"\x00" * 32


class TestFileUploads:
    """Tests for /api/v1/uploads endpoint."""

    def test_upload_image_success(self, client, auth_headers):
        """Test successful image upload."""
        # Create a fake image file
        image_content = JPEG
        files = {"file": ("test.jpg", BytesIO(image_content), "image/jpeg")}

        with patch('app.api.v1.uploads.settings') as mock_settings:
            mock_settings.r2_enabled = False

            response = client.post(
                "/api/v1/uploads/",
                files=files,
                headers=auth_headers
            )

        assert response.status_code == 200
        data = response.json()
        assert "url" in data
        assert data["media_type"] == "image"
        assert data["url"].endswith(".jpg")

    def test_upload_video_mp4_success(self, client, auth_headers):
        """Test successful MP4 video upload."""
        video_content = MP4
        files = {"file": ("test.mp4", BytesIO(video_content), "video/mp4")}

        with patch('app.api.v1.uploads.settings') as mock_settings:
            mock_settings.r2_enabled = False

            response = client.post(
                "/api/v1/uploads/",
                files=files,
                headers=auth_headers
            )

        assert response.status_code == 200
        data = response.json()
        assert data["media_type"] == "video"
        assert data["url"].endswith(".mp4")

    def test_upload_video_mov_success(self, client, auth_headers):
        """Test successful MOV video upload."""
        video_content = MOV
        files = {"file": ("test.mov", BytesIO(video_content), "video/quicktime")}

        with patch('app.api.v1.uploads.settings') as mock_settings:
            mock_settings.r2_enabled = False

            response = client.post(
                "/api/v1/uploads/",
                files=files,
                headers=auth_headers
            )

        assert response.status_code == 200
        data = response.json()
        assert data["media_type"] == "video"

    def test_upload_invalid_extension(self, client, auth_headers):
        """Test upload with invalid file extension."""
        file_content = b"some content"
        files = {"file": ("test.exe", BytesIO(file_content), "application/octet-stream")}

        response = client.post(
            "/api/v1/uploads/",
            files=files,
            headers=auth_headers
        )

        assert response.status_code == 400
        assert "Invalid file type" in response.json()["detail"]

    def test_upload_image_too_large(self, client, auth_headers):
        """Test image upload exceeding size limit."""
        # Create content larger than 10MB
        large_content = b"x" * (11 * 1024 * 1024)
        files = {"file": ("large.jpg", BytesIO(large_content), "image/jpeg")}

        with patch('app.api.v1.uploads.settings') as mock_settings:
            mock_settings.r2_enabled = False

            response = client.post(
                "/api/v1/uploads/",
                files=files,
                headers=auth_headers
            )

        assert response.status_code == 400
        assert "too large" in response.json()["detail"].lower()

    def test_upload_video_size_limit_different_from_image(self, client, auth_headers):
        """Test that video has different size limit than image."""
        # Create content larger than 10MB but less than 500MB
        # This should fail for images but pass for videos
        content_15mb = b"x" * (15 * 1024 * 1024)

        # Test as image - should fail
        image_files = {"file": ("large.jpg", BytesIO(content_15mb), "image/jpeg")}
        with patch('app.api.v1.uploads.settings') as mock_settings:
            mock_settings.r2_enabled = False
            response = client.post(
                "/api/v1/uploads/",
                files=image_files,
                headers=auth_headers
            )
        assert response.status_code == 400

        # Test as video - should succeed
        video_files = {"file": ("large.mp4", BytesIO(MP4 + content_15mb), "video/mp4")}
        with patch('app.api.v1.uploads.settings') as mock_settings:
            mock_settings.r2_enabled = False
            response = client.post(
                "/api/v1/uploads/",
                files=video_files,
                headers=auth_headers
            )
        assert response.status_code == 200

    def test_upload_to_r2_when_enabled(self, client, auth_headers):
        """Test upload goes to R2 when configured."""
        image_content = PNG
        files = {"file": ("test.png", BytesIO(image_content), "image/png")}

        with patch('app.api.v1.uploads.settings') as mock_settings:
            mock_settings.r2_enabled = True
            mock_settings.R2_PUBLIC_URL = "https://r2.example.com"

            with patch('app.api.v1.uploads.upload_to_r2') as mock_r2:
                mock_r2.return_value = "https://r2.example.com/uuid.png"

                response = client.post(
                    "/api/v1/uploads/",
                    files=files,
                    headers=auth_headers
                )

        assert response.status_code == 200
        assert "r2.example.com" in response.json()["url"]

    def test_upload_video_webm(self, client, auth_headers):
        """Test WebM video upload."""
        video_content = WEBM
        files = {"file": ("test.webm", BytesIO(video_content), "video/webm")}

        with patch('app.api.v1.uploads.settings') as mock_settings:
            mock_settings.r2_enabled = False

            response = client.post(
                "/api/v1/uploads/",
                files=files,
                headers=auth_headers
            )

        assert response.status_code == 200
        assert response.json()["media_type"] == "video"

    def test_upload_all_image_types(self, client, auth_headers):
        """Test all supported image types."""
        image_types = [
            ("test.jpg", "image/jpeg", JPEG),
            ("test.jpeg", "image/jpeg", JPEG),
            ("test.png", "image/png", PNG),
            ("test.gif", "image/gif", GIF),
            ("test.webp", "image/webp", WEBP),
        ]

        for filename, content_type, payload in image_types:
            files = {"file": (filename, BytesIO(payload), content_type)}

            with patch('app.api.v1.uploads.settings') as mock_settings:
                mock_settings.r2_enabled = False

                response = client.post(
                    "/api/v1/uploads/",
                    files=files,
                    headers=auth_headers
                )

            assert response.status_code == 200, f"Failed for {filename}"
            assert response.json()["media_type"] == "image"

    def test_upload_all_video_types(self, client, auth_headers):
        """Test all supported video types."""
        video_types = [
            ("test.mp4", "video/mp4", MP4),
            ("test.mov", "video/quicktime", MOV),
            ("test.avi", "video/x-msvideo", AVI),
            ("test.webm", "video/webm", WEBM),
        ]

        for filename, content_type, payload in video_types:
            files = {"file": (filename, BytesIO(payload), content_type)}

            with patch('app.api.v1.uploads.settings') as mock_settings:
                mock_settings.r2_enabled = False

                response = client.post(
                    "/api/v1/uploads/",
                    files=files,
                    headers=auth_headers
                )

            assert response.status_code == 200, f"Failed for {filename}"
            assert response.json()["media_type"] == "video"

    def test_upload_avi_success(self, client, auth_headers):
        files = {"file": ("test.avi", BytesIO(AVI), "video/x-msvideo")}
        with patch('app.api.v1.uploads.settings') as mock_settings:
            mock_settings.r2_enabled = False
            response = client.post("/api/v1/uploads/", files=files, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["media_type"] == "video"

    @pytest.mark.parametrize("filename,content_type", [
        ("evil.mp4", "video/mp4"),
        ("evil.mov", "video/quicktime"),
        ("evil.webm", "video/webm"),
        ("evil.avi", "video/x-msvideo"),
    ])
    def test_renamed_executable_as_video_is_rejected(self, client, auth_headers, filename, content_type):
        """Magic-byte sniffing must also cover videos, not only images."""
        files = {"file": (filename, BytesIO(b"MZ" + b"\x00" * 64), content_type)}
        response = client.post("/api/v1/uploads/", files=files, headers=auth_headers)
        assert response.status_code == 400
        assert "does not match" in response.json()["detail"]

    def test_video_signature_must_match_extension(self, client, auth_headers):
        """A valid WebM payload renamed to .mp4 is not an MP4."""
        files = {"file": ("mismatch.mp4", BytesIO(WEBM), "video/mp4")}
        response = client.post("/api/v1/uploads/", files=files, headers=auth_headers)
        assert response.status_code == 400
