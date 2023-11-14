import os

import orjson
from melaniebot.core import commands
from melaniebot.core.bot import Melanie

from melanie import get_curl, get_redis
from melanie.curl import get_curl

redis = get_redis()
BASE_JSON = {
    "model": "gpt-3.5-turbo-16k",
    "messages": [
        {
            "role": "system",
            "content": (
                "I want you to act as my friend named Melanie. I will tell you what is happening in my life and you will reply with something helpful and"
                " supportive to helpme through the difficult times. Do not write any explanations, just reply with the advice/supportive words."
            ),
        },
    ],
    "temperature": 1,
    "max_tokens": 299,
    "presence_penalty": 0,
}


class ChatClient(object):
    def __init__(self) -> None:
        self.curl = get_curl()
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer " + os.getenv("OPENAI_API_KEY", ""),
        }

    async def get_session(self, user_id):
        key = f"chatsession:{user_id}"

        data = await redis.get(key)
        data = orjson.loads(data) if data else orjson.loads(orjson.dumps(BASE_JSON))
        return data

    async def delete_session(self, user_id):
        key = f"chatsession:{user_id}"
        return await redis.delete(key)

    async def chat(self, data, user_id, msg):
        key = f"chatsession:{user_id}"

        msg = {
            "role": "user",
            "content": msg,
        }

        data["messages"].append(msg)

        r = await self.curl.fetch("https://api.openai.com/v1/chat/completions", headers=self.headers, body=orjson.dumps(data), method="POST")

        data2 = orjson.loads(r.body)

        msg = data2["choices"][-1]["message"]

        data["messages"].append(msg)

        await redis.set(key, orjson.dumps(data), ex=300)
        return data


def get_reply(data):
    return data["choices"][-1]["message"]


class ChatGPT(commands.Cog):
    """Store images as commands!."""

    def __init__(self, bot: Melanie) -> None:
        self.bot: Melanie = bot
        self.chat = ChatClient()
