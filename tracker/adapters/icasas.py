# Portal ToS may prohibit scraping. Output is for private research only.
# icasas.mx returns HTTP 410 on search URLs as of 2024 — site structure changed.
# Marked as unreachable until URL patterns are rediscovered.

from __future__ import annotations

import logging
from tracker.adapters.base import SourceAdapter, RawListing, RawPage

logger = logging.getLogger(__name__)


class IcasasAdapter(SourceAdapter):
    name = "icasas"

    def __init__(self, config, run_date):
        super().__init__(config, run_date)
        self.reachable = False
        logger.info(f"[{self.name}] disabled — site returns 410 on search URLs")

    def build_search_urls(self) -> list[str]:
        return []

    def parse_list(self, page: RawPage) -> list[RawListing]:
        return []
