from fastapi import APIRouter, HTTPException, status
from fastapi_restful.cbv import cbv

from app.config.settings import get_settings
from app.integrations.exchange_rate import ExchangeRateUnavailable, get_exchange_rate
from app.schemas.exchange_rate import ExchangeRateRead

router = APIRouter(prefix="/exchange-rate", tags=["Exchange rate"])


@cbv(router)
class ExchangeRateController:
    @router.get("/", response_model=ExchangeRateRead, summary="Official Bs/USD rate of the day")
    async def current(self):
        try:
            found = await get_exchange_rate(get_settings())
        except ExchangeRateUnavailable:
            # Generic to the client: the detail went to the log. The form asks for it by hand.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"message": "No se pudo consultar la tasa. Escríbela a mano."},
            )
        return ExchangeRateRead(
            rate=found.rate,
            source=found.source,
            effective_date=found.effective_date,
            fetched_at=found.fetched_at,
        )
