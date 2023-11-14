import orjson
from discord_webhook.async_webhook import AsyncDiscordWebhook
from discord_webhook.webhook import DiscordEmbed
from melanie import capturetime
from melanie.models.sharedapi.stripe import StripeWebhookPayload

from routes._base import APIRouter

router = APIRouter()


@router.post("/hooks/stripe_evenrwr3r23", include_in_schema=False)
async def inbound_stripe_hook(payload: dict) -> dict[str, str] | None:
    with capturetime("new stripe hook"):
        data = StripeWebhookPayload.parse_obj(payload)
        if data.type.startswith("payout"):
            return None
        hook = AsyncDiscordWebhook(
            url="https://discord.com/api/webhooks/1025813868888469514/SdlxgVawd492f2KEen54iqkW0GvHvph5TV9iGd01fGSoXeOR6V5U928ssqb5uYWYiQma",
        )
        embed = DiscordEmbed(title="New Stripe event posted!")
        payload = orjson.loads(orjson.dumps(payload))
        hook.add_file(file=orjson.dumps(payload, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS), filename="stripe.json")
        hook.add_embed(embed)
        await hook.execute()
        return {"status": "ok"}
