#!/usr/bin/env python3
"""
Cron job script to run scheduled searches

Add to crontab to run every hour:
0 * * * * cd /path/to/backend && /path/to/venv/bin/python run_scheduled_searches.py

Or use Railway's cron job feature with this command:
python run_scheduled_searches.py
"""

import asyncio
import sys
import os

# Add the parent directory to the path so we can import app modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import SessionLocal, bind_context
from app.models import Organization
from app.services.scheduler_service import SchedulerService
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def run_for_organization(org_id: str) -> None:
    """Each organization runs in its OWN session bound to that organization
    (row level security hides everyone else's searches)."""
    db = SessionLocal()
    try:
        bind_context(db, org_id)
        await SchedulerService(db).run_scheduled_searches()
    finally:
        db.close()


async def main():
    """Run scheduled searches, one organization at a time"""
    directory = SessionLocal()  # organizations is the (non-RLS) control table
    try:
        org_ids = [o for (o,) in directory.query(Organization.id).filter(Organization.is_active.is_(True)).all()]
    finally:
        directory.close()

    failures = 0
    logger.info("Starting scheduled search job for %d organizations", len(org_ids))
    for org_id in org_ids:
        try:
            await run_for_organization(org_id)
        except Exception as e:
            failures += 1
            logger.error("Error running scheduled searches for organization %s: %s", org_id, e, exc_info=True)
    logger.info("Scheduled search job completed")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
