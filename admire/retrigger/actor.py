import regex as re
from boltons.cacheutils import LRI, cachedmethod


def search_regex2(regex, content):
    import regex as re

    return re.findall(regex, content)


class TriggerActor(object):
    def __init__(self) -> None:
        self.cache = LRI(500)

    @cachedmethod("cache")
    def search_regex(self, regex, content):
        return re.findall(regex, content)
