import ccxt.async_support as ccxt
import logging
from typing import List, Optional
from models import Candle

logger = logging.getLogger(__name__)

class BinanceManager:
    """
    Fetches PAXG market data from Binance.
    api.binance.com is geo-blocked in some regions, so requests automatically
    retry through the public mirrors listed in HOSTS.
    """
    HOSTS = [
        'https://api.binance.com',
        'https://data-api.binance.vision',
    ]

    def __init__(self, hosts: Optional[List[str]] = None):
        self.hosts = hosts or self.HOSTS
        self._current_host = None
        self.exchange = None

    def _init_exchange(self, host: str) -> None:
        api_urls = {
            'public': f'{host}/api/v3',
            'private': f'{host}/api/v3',
            'v1': f'{host}/api/v1',
        }
        self.exchange = ccxt.binance({'enableRateLimit': True, 'urls': {'api': api_urls}})

    async def _call(self, method_name: str, *args, **kwargs):
        last_error = None
        for host in self.hosts:
            try:
                if self._current_host != host:
                    await self.close()
                    self._init_exchange(host)
                    self._current_host = host
                return await getattr(self.exchange, method_name)(*args, **kwargs)
            except (ccxt.ExchangeNotAvailable, ccxt.RequestTimeout, ccxt.NetworkError) as e:
                last_error = e
                logger.warning(f"Binance host {host} unreachable ({e}); trying mirror...")
        raise last_error

    async def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> List[Candle]:
        try:
            ohlcv = await self._call('fetch_ohlcv', symbol, timeframe, limit=limit)
            return [
                Candle(
                    timestamp=x[0], open=x[1], high=x[2], low=x[3], close=x[4], volume=x[5]
                ) for x in ohlcv
            ]
        except Exception as e:
            logger.error(f"Error fetching data from Binance: {e}")
            raise

    async def fetch_current_price(self, symbol: str) -> float:
        try:
            ticker = await self._call('fetch_ticker', symbol)
            price = ticker['last']
            logger.info(f"Current {symbol} Price: {price}")
            return float(price)
        except Exception as e:
            logger.error(f"Error fetching current price from Binance: {e}")
            raise

    async def close(self) -> None:
        if self.exchange is not None:
            try:
                await self.exchange.close()
            except Exception as e:
                logger.warning(f"Error closing Binance session: {e}")
            self.exchange = None
