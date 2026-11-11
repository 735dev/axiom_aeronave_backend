from datetime import datetime

from pydantic import BaseModel


class ExchangeRateRead(BaseModel):
    """Official Bs/USD rate proposed by the system. The person can always overwrite it."""

    rate: float
    source: str
    effective_date: str | None
    fetched_at: datetime
