from __future__ import annotations

import math
import random
from datetime import datetime, timezone

import discord
from melaniebot.core import bank
from melaniebot.core.bot import Melanie as Bot


class GiveawayError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message


class GiveawayExecError(GiveawayError):
    pass


class GiveawayEnterError(GiveawayError):
    pass


class Giveaway:
    def __init__(
        self,
        guildid: int,
        channelid: int,
        messageid: int,
        endtime: datetime,
        prize: str = None,
        emoji: str = "🎉",
        *,
        entrants=None,
        **kwargs,
    ) -> None:
        self.guildid = guildid
        self.channelid = channelid
        self.messageid = messageid
        self.endtime = endtime
        self.prize = prize
        self.entrants = entrants or []
        self.emoji = emoji
        self.kwargs = kwargs

    async def add_entrant(self, user: discord.Member, *, bot: Bot, session) -> tuple[bool, GiveawayError]:
        if user.id in self.entrants:
            return
        if self.kwargs.get("roles", []) and all(int(role) not in [x.id for x in user.roles] for role in self.kwargs.get("roles", [])):
            msg = "You do not have the required roles to join this giveaway."
            raise GiveawayEnterError(msg)

        if self.kwargs.get("blacklist", []) and any(int(role) in [x.id for x in user.roles] for role in self.kwargs.get("blacklist", [])):
            msg = "Your role is blacklisted from this giveaway."
            raise GiveawayEnterError(msg)

        if required_server := self.kwargs.get("server", None):
            required_server = int(required_server[0])
            partner_guild: discord.Guild = bot.get_guild(required_server)
            if not partner_guild:
                raise GiveawayExecError
            if not partner_guild.get_member(user.id):
                msg = f"You must be a member of {partner_guild} to participate in this giveaway!"
                raise GiveawayEnterError(msg)

        if (
            self.kwargs.get("joined", None) is not None
            and (datetime.now(timezone.utc) - user.joined_at.replace(tzinfo=timezone.utc)).days <= self.kwargs["joined"]
        ):
            msg = "Your account is too new to join this giveaway."
            raise GiveawayEnterError(msg)
        if (
            self.kwargs.get("created", None) is not None
            and (datetime.now(timezone.utc) - user.created_at.replace(tzinfo=timezone.utc)).days <= self.kwargs["created"]
        ):
            msg = "Your account is too new to join this giveaway."
            raise GiveawayEnterError(msg)
        if self.kwargs.get("cost", None) is not None:
            if not await bank.can_spend(user, self.kwargs["cost"]):
                msg = "You do not have enough credits to join this giveaway."
                raise GiveawayEnterError(msg)

            await bank.withdraw_credits(user, self.kwargs["cost"])
        if self.kwargs.get("levelreq", None) is not None:
            cog = bot.get_cog("Leveler")
            if cog is None:
                msg = "The Leveler cog is not installed."
                raise GiveawayExecError(msg)
            userinfo = await cog.db.users.find_one({"user_id": str(user.id)})
            lvl = userinfo.get("servers", {}).get(str(self.guildid), {}).get("level", 0)
            if lvl <= self.kwargs.get("levelreq", 0):
                msg = "You do not meet the required level to join this giveaway."
                raise GiveawayEnterError(msg)

        if self.kwargs.get("repreq", None) is not None:
            cog = bot.get_cog("Leveler")
            if cog is None:
                msg = "The Leveler cog is not installed."
                raise GiveawayExecError(msg)
            userinfo = await cog.db.users.find_one({"user_id": str(user.id)})
            lvl = userinfo.get("servers", {}).get(str(self.guildid), {}).get("rep", 0)
            if lvl <= self.kwargs.get("levelreq", 0):
                msg = "You do not meet the required rep to join this giveaway."
                raise GiveawayEnterError(msg)

        if self.kwargs.get("mee6_level", None) is not None:
            lb = await get_mee6lb(session, self.guildid)
            if lb is None:
                msg = "The MEE6 Leaderboard is not available."
                raise GiveawayExecError(msg)
            for user in lb:
                if user["id"] == str(user.id) and user["level"] < self.kwargs.get("mee6-level", 0):
                    msg = "You do not meet the required MEE6 level to join this giveaway."
                    raise GiveawayEnterError(msg)

        if self.kwargs.get("tatsu_level", None) is not None:
            token = await bot.get_shared_api_tokens("tatsumaki")
            if token.get("authorization") is None:
                msg = "The Tatsu token is not set."
                raise GiveawayExecError(msg)
            uinfo = await get_tatsuinfo(session, token.get("authorization"), user.id)
            if uinfo is None:
                msg = "The Tatsu API did not return any data therefore you have not been entered."
                raise GiveawayEnterError(msg)
            if int((1 / 278) * (9 + math.sqrt(81 + 1112 * uinfo["xp"]))) < self.kwargs.get("tatsu_level", 0):
                msg = "You do not meet the required Tatsu level to join this giveaway."
                raise GiveawayEnterError(msg)

        if self.kwargs.get("tatsu_rep", None) is not None:
            token = bot.get_shared_api_tokens("tatsumaki")
            if token.get("authorization") is None:
                msg = "The Tatsu token is not set."
                raise GiveawayExecError(msg)
            uinfo = await get_tatsuinfo(session, token.get("authorization"), user.id)
            if uinfo is None:
                msg = "The Tatsu API did not return any data therefore you have not been entered."
                raise GiveawayEnterError(msg)
            if uinfo["reputation"] < self.kwargs.get("tatsu_rep", 0):
                msg = "You do not meet the required Tatsu rep to join this giveaway."
                raise GiveawayEnterError(msg)

        if self.kwargs.get("amari_level", None) is not None:
            token = bot.get_shared_api_tokens("amari")
            if token.get("authorization") is None:
                msg = "The Amari token is not set."
                raise GiveawayExecError(msg)
            uinfo = await get_amari_info(session, token.get("authorization"), user.id, self.guildid)
            if uinfo is None:
                msg = "The Amari API did not return any data therefore you have not been entered."
                raise GiveawayEnterError(msg)
            if uinfo["level"] < self.kwargs.get("amari_level", 0):
                msg = "You do not meet the required Amari level to join this giveaway."
                raise GiveawayEnterError(msg)

        if self.kwargs.get("amari_weekly_xp", None) is not None:
            token = bot.get_shared_api_tokens("amari")
            if token.get("authorization") is None:
                msg = "The Amari token is not set."
                raise GiveawayExecError(msg)
            uinfo = await get_amari_info(session, token.get("authorization"), user.id, self.guildid)
            if uinfo is None:
                msg = "The Amari API did not return any data therefore you have not been entered."
                raise GiveawayEnterError(msg)
            if uinfo["level"] < self.kwargs.get("amari_weekly_xp", 0):
                msg = "You do not meet the required Amari weekly XP to join this giveaway."
                raise GiveawayEnterError(msg)

        self.entrants.append(user.id)
        if self.kwargs.get("multi", None) is not None and any(int(role) in [x.id for x in user.roles] for role in self.kwargs.get("multi-roles", [])):
            for _ in range(self.kwargs["multi"] - 1):
                self.entrants.append(user.id)
        return

    def remove_entrant(self, userid: int) -> None:
        self.entrants = [x for x in self.entrants if x != userid]

    def draw_winner(self) -> int:
        winners = self.kwargs.get("winners") or 1
        if len(self.entrants) < winners:
            return None
        winner = random.sample(self.entrants, winners)
        self.remove_entrant(winner)
        return winner

    def __str__(self) -> str:
        return f"{self.prize} - {self.endtime}"


async def get_mee6lb(session, guild):
    async with session.get(f"https://mee6.xyz/api/plugins/leaderboard/leaderboard?guild={guild}&limit=1000") as r:
        if r.status != 200:
            return None
        data = await r.json()
        return data["players"]


async def get_tatsuinfo(session, token, userid):
    async with session.get(f"https://api.tatsu.gg/v1/users/{userid}/profile", headers={"Authorization": token}) as r:
        return None if r.status != 200 else await r.json()


async def get_amari_info(session, token, userid, guildid):
    async with session.get(f"https://amaribot.com/api/v1/guild/{guildid}/member/{userid}", headers={"Authorization": token}) as r:
        return None if r.status != 200 else await r.json()
