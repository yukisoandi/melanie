from typing import Annotated, Optional

from melanie import BaseModel, Field, get_curl, url_concat


class Answer(BaseModel):
    name: Optional[str]
    type: Optional[int]
    ttl: Annotated[Optional[int], Field(alias="TTL")]
    data: Optional[str]


class Question(BaseModel):
    name: Optional[str]
    type: Optional[int]


class DnsQuery(BaseModel):
    status: Annotated[Optional[int], Field(alias="Status")]
    tc: Annotated[Optional[bool], Field(alias="TC")]
    rd: Annotated[Optional[bool], Field(alias="RD")]
    ra: Annotated[Optional[bool], Field(alias="RA")]
    ad: Annotated[Optional[bool], Field(alias="AD")]
    cd: Annotated[Optional[bool], Field(alias="CD")]
    question: Annotated[Optional[list[Question]], Field(alias="Question")]
    answer: Annotated[Optional[list[Answer]], Field(alias="Answer")]

    @property
    def address(self):
        if self.answer:
            return self.answer[-1].data

    @classmethod
    async def resolve(cls, hostname: str):
        curl = get_curl()
        headers = {
            "accept": "application/dns-json",
        }

        params = {
            "name": hostname,
            "type": "A",
        }
        r = await curl.fetch(url_concat("https://cloudflare-dns.com/dns-query", params), headers=headers)
        return cls.parse_raw(r.body)
