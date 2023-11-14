from __future__ import annotations

# QUERY = f"""
# with ag1 as (select count(*) msg_count, user_id from guild_messages  where guild_id = {GUILD_ID} group by user_id)
# select * from ag1 where msg_count > {MIN_MSG_CNT}
# """


# if confirmed:
#         for x in to_kick:

#             if not await redis.ratelimited(f"fed_kicks:{GUILD_ID}", 1, 2):
