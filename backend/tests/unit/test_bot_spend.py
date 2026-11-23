import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.anyio
async def test_bot_spend_endpoint(anyio_backend):
    """build_overview returns {"campaigns": [...], "errors": {...}} (NOT a bare
    list); the endpoint must aggregate the campaign rows."""
    with patch("app.api.v1.bot.build_overview", new_callable=AsyncMock) as mock_overview:
        mock_overview.return_value = {
            "campaigns": [
                {"spend": 100.50, "status": "ENABLED"},
                {"spend": 50.25, "status": "PAUSED"},
            ],
            "errors": {"tiktok": "No connected TikTok Ads advertiser."},
        }
        from app.api.v1.bot import read_spend

        mock_api_key = MagicMock()
        mock_api_key.organization_id = "org_123"

        res = await read_spend(date_preset="last_7d", api_key=mock_api_key, db=MagicMock())
        assert res["total_spend"] == 150.75
        assert res["active_campaigns"] == 1
        assert res["platform_errors"] == {"tiktok": "No connected TikTok Ads advertiser."}
        mock_overview.assert_awaited_once()
        assert mock_overview.await_args.args[1] == "org_123"


@pytest.mark.anyio
async def test_bot_spend_rejects_unbound_key(anyio_backend):
    from fastapi import HTTPException
    from app.api.v1.bot import read_spend

    unbound = MagicMock()
    unbound.organization_id = None
    with pytest.raises(HTTPException) as exc:
        await read_spend(date_preset="last_7d", api_key=unbound, db=MagicMock())
    assert exc.value.status_code == 403
