"""Copy generation unit tests."""
import pytest
from fastapi import status
from unittest.mock import MagicMock, patch
import json


def make_brand_payload(name, **kwargs):
    """Create a brand payload with required colors."""
    return {
        "name": name,
        "colors": {
            "primary": kwargs.get("primary", "#FF0000"),
            "secondary": kwargs.get("secondary", "#00FF00"),
            "highlight": kwargs.get("highlight", "#0000FF")
        },
        "voice": kwargs.get("voice", "Professional"),
        "products": kwargs.get("products", []),
        "profileIds": kwargs.get("profileIds", [])
    }


class TestCopyGeneration:
    """Tests for AI copy generation."""

    @pytest.fixture
    def test_brand(self, client, auth_headers):
        """Create a test brand."""
        response = client.post(
            "/api/v1/brands/",
            json=make_brand_payload("Copy Gen Test Brand", voice="Professional and friendly"),
            headers=auth_headers
        )
        return response.json()

    @pytest.fixture
    def test_product(self, client, auth_headers, test_brand):
        """Create a test product."""
        brand_id = test_brand.get("id")
        if not brand_id:
            pytest.skip("Brand creation failed, cannot create product")
        response = client.post(
            "/api/v1/products/",
            json={
                "name": "Copy Gen Test Product",
                "description": "A revolutionary product that solves problems",
                "brand_id": brand_id
            },
            headers=auth_headers
        )
        return response.json()

    @pytest.fixture
    def test_profile(self, client, auth_headers):
        """Create a test profile."""
        response = client.post(
            "/api/v1/profiles/",
            json={
                "name": "Copy Gen Test Profile",
                "demographics": "Adults 25-45",
                "painPoints": "Time constraints, budget concerns",
                "goals": "Efficiency, cost savings"
            },
            headers=auth_headers
        )
        return response.json()

    def test_generate_copy_endpoint_exists(self, client, auth_headers):
        """Test that copy generation endpoint exists."""
        response = client.post(
            "/api/v1/copy-generation/generate",
            json={},
            headers=auth_headers
        )
        # Should return validation error, not 404
        assert response.status_code != status.HTTP_404_NOT_FOUND

    def test_generate_copy_validation(self, client, auth_headers):
        """Test copy generation with missing data."""
        response = client.post(
            "/api/v1/copy-generation/generate",
            json={
                "brand_id": "nonexistent"
            },
            headers=auth_headers
        )
        assert response.status_code in [
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            status.HTTP_404_NOT_FOUND
        ]

    def test_regenerate_field_endpoint_exists(self, client, auth_headers):
        """Test that field regeneration endpoint exists."""
        response = client.post(
            "/api/v1/copy-generation/regenerate-field",
            json={},
            headers=auth_headers
        )
        # Should return validation error, not 404
        assert response.status_code != status.HTTP_404_NOT_FOUND


# The Gemini-backed behaviour (mocked provider, markdown fences, invalid JSON, errors) is now covered
# against the AI Gateway in tests/unit/test_ai_gateway_regression.py.
