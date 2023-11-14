import asyncio

from fastapi.responses import UJSONResponse
from melanie import BaseModel

from api_services import api_username_var, services
from routes._base import APIRouter, Request

router = APIRouter(tags=["admin"], prefix="/api/admin")


class CacheDelete(BaseModel):
    redis: int
    disk: int


@router.delete("/cache/{target:path}", name="Delete a media cache object", operation_id="deleteCache", response_model=CacheDelete)
async def delete_cache(target: str, request: Request):
    async with services.verify_token(request, description=f"cache delete {target}"):
        _user = api_username_var.get().lower()
        if _user not in ("melanie", "m@monteledwards.com"):
            return UJSONResponse("Unauthorized", 403)
        async with asyncio.timeout(10):
            _keys = await services.redis.keys(f"*{target}*")
            redis_deleted = await services.redis.delete(*_keys) if _keys else 0
            disk = await services.delete_cached_target(target)
            disk_deleted = 1 if disk else 0
            return CacheDelete(disk=disk_deleted, redis=redis_deleted)


@router.get("/rpc", name="Get RPC endpoint", description="Retrive the RPC endpoint that was set at startup")
async def get_rpc(request: Request):
    async with services.verify_token(request):
        return services.rpc_uri
