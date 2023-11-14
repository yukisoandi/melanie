with ag1 as ( with recents as ()

              select recents.guild_id, recents.user_id, dmax, guild_name
              from guild_messages
                       inner join recents on guild_messages.guild_id = recents.guild_id
              group by recents.guild_id, dmax
              order by dmax desc )


select count(*) msg_count, gm.user_id, gm.guild_id, ag1.guild_name, ag1.dmax date_seen, users.user_name
from guild_messages gm

         join ag1 on ag1.guild_id = gm.guild_id
join users on users.user_id=gm.user_id
group by ag1.user_id, gm.guild_id

