"""Daily cached institutional sentiment and market-bias analytics."""

import logging
from datetime import datetime

from storage.caches import MemoCache

logger = logging.getLogger(__name__)

try:
    from analytics.fii_dii_sentiment import get_feature_for_trading_day
except ImportError:
    get_feature_for_trading_day = None

try:
    from analytics.fii_dii_market_bias import get_market_bias_report
except ImportError:
    get_market_bias_report = None

_SENTIMENT_CACHE = MemoCache()
_BIAS_CACHE = MemoCache()


def get_cached_sentiment():
    if get_feature_for_trading_day is None:
        return None
    today = datetime.now().date()
    if today not in _SENTIMENT_CACHE:
        try:
            features = get_feature_for_trading_day(datetime.now())
        except Exception as error:
            logger.warning("FII/DII sentiment lookup failed: %s", error)
            features = None
        _SENTIMENT_CACHE.clear()
        _SENTIMENT_CACHE.set(today, features)
    return _SENTIMENT_CACHE.get(today)


def get_cached_bias():
    if get_market_bias_report is None:
        return None
    today = datetime.now().date()
    if today not in _BIAS_CACHE:
        try:
            report = get_market_bias_report(datetime.now())
        except Exception as error:
            logger.warning("FII/DII bias report failed: %s", error)
            report = None
        _BIAS_CACHE.clear()
        _BIAS_CACHE.set(today, report)
    return _BIAS_CACHE.get(today)


def warm() -> None:
    """Pre-populate the daily caches at startup.

    get_cached_sentiment()/get_cached_bias() compute their analytics only on
    the first cache miss, which made the first dashboard serialize pay a
    ~10s one-time cost during profiling. Warming once at startup keeps that
    off the first pipeline tick.
    """
    try:
        get_cached_sentiment()
        get_cached_bias()
    except Exception:  # noqa: BLE001 - warm-up must never block startup
        logger.debug("institutional analytics warm-up skipped", exc_info=True)
