# Portal ToS may prohibit scraping. Output is for private research only.
# lamudi.com.mx uses AWS WAF "Human Verification" — blocks headless browsers.
# Marked as unreachable until a bypass is found.

from __future__ import annotations

import logging
from tracker.adapters.base import SourceAdapter, RawListing, RawPage

logger = logging.getLogger(__name__)


class LamudiAdapter(SourceAdapter):
    name = "lamudi"

    def __init__(self, config, run_date):
        super().__init__(config, run_date)
        self.reachable = False
        logger.info(f"[{self.name}] disabled — AWS WAF blocks headless browsers")

    def build_search_urls(self) -> list[str]:
        return []

    def parse_list(self, page: RawPage) -> list[RawListing]:
        return []
