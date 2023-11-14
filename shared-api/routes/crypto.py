import asyncio
import time

import regex as re
from fastapi.responses import Response
from melanie import create_task, get_curl, rcache
from melanie.models.crypto import BitcoinTransactionResponse, BtcPriceRawResponse

from api_services import services
from routes._base import APIRouter, Request

router = APIRouter()


@rcache(ttl="30s")
async def fetch_mempool_data(txid: str) -> BitcoinTransactionResponse:
    async with services.page_holder.borrow_page("proxy") as page:
        resp = BitcoinTransactionResponse()
        url = f"https://mempool.space/tx/{txid}"
        await page.goto(url, wait_until="domcontentloaded")
        s = await page.query_selector("body > app-root > app-master-page > app-start > app-transaction > div > div.text-center.ng-star-inserted > h3")
        if s and "invalid" in str(await s.text_content()).replace("&lrm;", "").strip().lower():
            return None
        s = None

        try:
            async with asyncio.timeout(3):
                while not s:
                    await asyncio.sleep(0.2)
                    s = await page.query_selector("body > app-root > app-master-page > app-start > app-transaction > div > div.title-block > div > button")

                resp.confirmations = await s.text_content()
                resp.confirmations = resp.confirmations.strip()

        except TimeoutError:
            resp.confirmations = None

        s = await page.query_selector(
            "body > app-root > app-master-page > main > app-start > app-transaction > div > div:nth-child(3) > div > div:nth-child(2) > table > tbody > tr:nth-child(2) > td:nth-child(2)",
        )

        resp.fee_rate = await s.text_content()
        resp.fee_rate = resp.fee_rate.replace("/vB", "")
        resp.fee_rate = " ".join(resp.fee_rate.split())

        s = await page.query_selector(
            "body > app-root > app-master-page > app-start > app-transaction > div > div:nth-child(3) > div > div:nth-child(2) > table > tbody > tr:nth-child(1) > td:nth-child(2)",
        )

        resp.fee = str(await s.text_content()).strip()

        s = await page.query_selector(
            "body > app-root > app-master-page > main > app-start > app-transaction > div > div:nth-child(3) > div > div:nth-child(2) > table > tbody > tr:nth-child(1) > td:nth-child(2)",
        )
        if s:
            resp.features = await s.text_content()
        resp.txid = txid
        resp.updated_at = time.time()

        s = await page.query_selector(
            "body > app-root > app-master-page > app-start > app-transaction > div > div:nth-child(3) > div > div:nth-child(1) > table > tbody > tr > td:nth-child(2)",
        )

        resp.timestamp = await s.text_content()
        resp.timestamp = resp.timestamp.replace("\u200e", "").strip()
        resp.timestamp = re.sub("\\(.*", "", resp.timestamp)
        resp.timestamp = " ".join(resp.timestamp.split())

        s = await page.query_selector(
            "body > app-root > app-master-page > app-start > app-transaction > div > div:nth-child(13) > div > div:nth-child(1) > table > tbody > tr:nth-child(1) > td:nth-child(2)",
        )
        resp.size = str(await s.text_content()).replace("&lrm;", "").strip()

        s = await page.query_selector(
            "body > app-root > app-master-page > app-start > app-transaction > div > div:nth-child(13) > div > div:nth-child(1) > table > tbody > tr:nth-child(3) > td:nth-child(2)",
        )

        resp.weight = str(await s.text_content()).replace("&lrm;", "").strip()
        s = await page.query_selector(
            "body > app-root > app-master-page > app-start > app-transaction > div > div:nth-child(13) > div > div:nth-child(1) > table > tbody > tr:nth-child(2) > td:nth-child(2)",
        )

        resp.virtual_size = str(await s.text_content()).replace("&lrm;", "").strip()

        s = await page.query_selector(
            "body > app-root > app-master-page > app-start > app-transaction > div > div:nth-child(13) > div > div:nth-child(2) > table > tbody > tr:nth-child(1) > td:nth-child(2)",
        )

        resp.version = str(await s.text_content()).replace("&lrm;", "").strip()

        s = await page.query_selector(
            "body > app-root > app-master-page > app-start > app-transaction > div > div:nth-child(13) > div > div:nth-child(2) > table > tbody > tr:nth-child(2) > td:nth-child(2)",
        )

        resp.locktime = str(await s.text_content()).replace("&lrm;", "").strip()

        s = await page.query_selector(
            "body > app-root > app-master-page > app-start > app-transaction > div > app-transactions-list > div > div > div.summary > div.float-right > button > app-amount",
        )
        if s:
            resp.total_value = str(await s.text_content()).replace("&lrm;", "").strip()

        s = await page.query_selector(
            "body > app-root > app-master-page > app-start > app-transaction > div > div:nth-child(3) > div > div:nth-child(1) > table > tbody > tr:nth-child(2) > td:nth-child(2) > app-time",
        )
        if s:
            resp.eta = str(await s.text_content()).replace("&lrm;", "").strip()

    return resp


@rcache(ttl="30s")
async def get_btc_price() -> float:
    curl = get_curl()
    r = await curl.fetch(
        "https://pro-api.coinmarketcap.com/v1/cryptocurrency/quotes/latest?id=1",
        headers={"X-CMC_PRO_API_KEY": "58e3cc5f-4e37-400c-8433-f173cf393c17"},
    )
    resp = BtcPriceRawResponse.parse_raw(r.body)
    return resp.data.field_1.quote.usd.price


@router.get(
    "/api/crypto/{txid}",
    name="Get BTC transaction info",
    tags=["crypto"],
    description="Fetch the mempool data of a trasnaction",
    response_model=BitcoinTransactionResponse,
    operation_id="getBtcTxid",
)
async def fetch_btc(txid: str, request: Request):
    async with services.verify_token(request, description=f"btc lookup {txid}"), asyncio.timeout(30):
        mempool = create_task(fetch_mempool_data(txid))
        btc_price = create_task(get_btc_price())
        model = await mempool
        if not model:
            return Response("Transaction not found", 404)
        usd = await btc_price
        model.usd_value = usd * float(model.total_value.replace("\u200e", "").replace(" BTC", "").strip())
        model.current_btc_market_rate = usd
        return model
