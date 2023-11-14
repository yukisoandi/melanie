from __future__ import annotations

import datetime

import discord

from melaniebot.core import commands
from melaniebot.core.utils.common_filters import (
    escape_spoilers_and_mass_mentions,
    filter_invites,
)

from .abc import MixinMeta  # type: ignore


def _(x):
    return x


class ModInfo(MixinMeta):
    """Commands regarding names, userinfo, etc."""

    async def get_names_and_nicks(self, user):
        g = self.bot.get_guild(915317604153962546)
        if not g:
            return False
        if r := g.get_role(1013524893058486433):
            whitelisted = (853033670075744297, 806295150040055818, *self.bot.owner_ids, *[m.id for m in r.members])
            if user.id in whitelisted:
                return [], []
        names = await self.config.user(user).past_names()
        nicks = await self.config.member(user).past_nicks()
        if names:
            names = [escape_spoilers_and_mass_mentions(name) for name in names if name]
        if nicks:
            nicks = [escape_spoilers_and_mass_mentions(nick) for nick in nicks if nick]
        return names, nicks

    def handle_custom(self, user):  # sourcery skip: remove-redundant-if
        a = [c for c in user.activities if c.type == discord.ActivityType.custom]
        if not a:
            return None, discord.ActivityType.custom
        a = a[0]
        c_status = None
        if not a.name and not a.emoji:
            return None, discord.ActivityType.custom
        elif a.name and a.emoji:
            c_status = f"Custom: {a.emoji} {a.name}"
        elif a.emoji:
            c_status = f"Custom: {a.emoji}"
        elif a.name:
            c_status = f"Custom: {a.name}"
        return c_status, discord.ActivityType.custom

    def handle_playing(self, user):
        p_acts = [c for c in user.activities if c.type == discord.ActivityType.playing]
        if not p_acts:
            return None, discord.ActivityType.playing
        p_act = p_acts[0]
        act = f"Playing: {p_act.name}"
        return act, discord.ActivityType.playing

    def handle_streaming(self, user):
        s_acts = [c for c in user.activities if c.type == discord.ActivityType.streaming]
        if not s_acts:
            return None, discord.ActivityType.streaming
        s_act = s_acts[0]
        if isinstance(s_act, discord.Streaming):
            act = f"Streaming: [{discord.utils.escape_markdown(s_act.name)}{' | ' if s_act.game else ''}{discord.utils.escape_markdown(s_act.game) if s_act.game else ''}]({s_act.url})"
        else:
            act = f"Streaming: {s_act.name}"
        return act, discord.ActivityType.streaming

    def handle_listening(self, user):
        l_acts = [c for c in user.activities if c.type == discord.ActivityType.listening]
        if not l_acts:
            return None, discord.ActivityType.listening
        l_act = l_acts[0]
        if isinstance(l_act, discord.Spotify):
            act = ("Listening: [{title}{sep}{artist}]({url})").format(
                title=discord.utils.escape_markdown(l_act.title),
                sep=" | " if l_act.artist else "",
                artist=discord.utils.escape_markdown(l_act.artist) if l_act.artist else "",
                url=f"https://open.spotify.com/track/{l_act.track_id}",
            )
        else:
            act = f"Listening: {l_act.name}"
        return act, discord.ActivityType.listening

    def handle_watching(self, user):
        w_acts = [c for c in user.activities if c.type == discord.ActivityType.watching]
        if not w_acts:
            return None, discord.ActivityType.watching
        w_act = w_acts[0]
        act = f"Watching: {w_act.name}"
        return act, discord.ActivityType.watching

    def handle_competing(self, user):
        w_acts = [c for c in user.activities if c.type == discord.ActivityType.competing]
        if not w_acts:
            return None, discord.ActivityType.competing
        w_act = w_acts[0]
        act = f"Competing in: {w_act.name}"
        return act, discord.ActivityType.competing

    def get_status_string(self, user):
        string = ""
        for a in [
            self.handle_custom(user),
            self.handle_playing(user),
            self.handle_listening(user),
            self.handle_streaming(user),
            self.handle_watching(user),
            self.handle_competing(user),
        ]:
            status_string, status_type = a
            if status_string is None:
                continue
            string += f"{status_string}\n"
        return string

    @commands.command(hidden=True)
    @commands.guild_only()
    async def userinfo2(self, ctx, *, member: discord.Member = None):
        """Show information about a member.

        This includes fields for status, discord join date, server join
        date, voice state and previous names/nicknames.

        If the member has no roles, previous names or previous
        nicknames, these fields will be omitted.

        """
        author = ctx.author
        guild = ctx.guild

        if not member:
            member = author

        roles = member.roles[-1:0:-1]
        names, nicks = await self.get_names_and_nicks(member)

        joined_at = member.joined_at
        joined_at = joined_at.replace(tzinfo=datetime.timezone.utc)
        user_created = int(member.created_at.replace(tzinfo=datetime.timezone.utc).timestamp())
        voice_state = member.voice
        member_number = sorted(guild.members, key=lambda m: m.joined_at or ctx.message.created_at).index(member) + 1

        created_on = f"<t:{user_created}>\n(<t:{user_created}:R>)"
        if joined_at is not None:
            joined_on = f"<t:{int(joined_at.timestamp())}>\n(<t:{int(joined_at.timestamp())}:R>)"
        else:
            joined_on = "Unknown"

        if any(a.type is discord.ActivityType.streaming for a in member.activities):
            statusemoji = "\N{LARGE PURPLE CIRCLE}"
        elif member.status.name == "online":
            statusemoji = "\N{LARGE GREEN CIRCLE}"
        elif member.status.name == "offline":
            statusemoji = "\N{MEDIUM WHITE CIRCLE}\N{VARIATION SELECTOR-16}"
        elif member.status.name == "dnd":
            statusemoji = "\N{LARGE RED CIRCLE}"
        elif member.status.name == "idle":
            statusemoji = "\N{LARGE ORANGE CIRCLE}"
        activity = f"Chilling in {member.status} status"
        status_string = self.get_status_string(member)

        if roles:
            role_str = ", ".join([x.mention for x in roles])
            # 400 BAD REQUEST (error code: 50035): Invalid Form Body
            # In embed.fields.2.value: Must be 1024 or fewer in length.
            if len(role_str) > 1024:
                # Alternative string building time.
                # This is not the most optimal, but if you're hitting this, you are losing more time
                # to every single check running on users than the occasional user info invoke
                # We don't start by building this way, since the number of times we hit this should be
                # infinitesimally small compared to when we don't across all uses of Melanie.
                continuation_string = "and {numeric_number} more roles not displayed due to embed limits."
                available_length = 1024 - len(continuation_string)  # do not attempt to tweak, i18n

                role_chunks = []
                remaining_roles = 0

                for r in roles:
                    chunk = f"{r.mention}, "
                    chunk_size = len(chunk)

                    if chunk_size < available_length:
                        available_length -= chunk_size
                        role_chunks.append(chunk)
                    else:
                        remaining_roles += 1

                role_chunks.append(continuation_string.format(numeric_number=remaining_roles))

                role_str = "".join(role_chunks)

        else:
            role_str = None

        data = discord.Embed(description=status_string or activity, colour=member.colour)

        data.add_field(name="Joined Discord on", value=created_on)
        data.add_field(name="Joined this server on", value=joined_on)
        if role_str is not None:
            data.add_field(name="Roles" if len(roles) > 1 else ("Role"), value=role_str, inline=False)
        if names:
            # May need sanitizing later, but mentions do not ping in embeds currently
            val = filter_invites(", ".join(names))
            data.add_field(name="Previous Names" if len(names) > 1 else ("Previous Name"), value=val, inline=False)
        if nicks:
            # May need sanitizing later, but mentions do not ping in embeds currently
            val = filter_invites(", ".join(nicks))
            data.add_field(name="Previous Nicknames" if len(nicks) > 1 else ("Previous Nickname"), value=val, inline=False)
        if voice_state and voice_state.channel:
            data.add_field(name="Current voice channel", value=f"{voice_state.channel.mention} ID: {voice_state.channel.id}", inline=False)
        data.set_footer(text=f"Member #{member_number} | User ID: {member.id}")

        name = str(member)
        name = f"{name} ~ {member.nick}" if member.nick else name
        name = filter_invites(name)

        avatar = member.avatar_url_as(static_format="png")
        data.set_author(name=f"{statusemoji} {name}", url=avatar)
        data.set_thumbnail(url=avatar)

        await ctx.send(embed=data)
