from __future__ import annotations

import collections


class LRUDict:
    """Dict with LRU-eviction and max-size.

    This is intended for caching, it may not behave how you want
    otherwise

    This uses collections.OrderedDict under the hood, but does not
    directly expose all of it's methods (intentional)

    """

    def __init__(self, *keyval_pairs, size) -> None:
        self.size = size
        self._dict = collections.OrderedDict(*keyval_pairs)

    def __contains__(self, key) -> bool:
        if key in self._dict:
            self._dict.move_to_end(key, last=True)
            return True
        return False

    def __getitem__(self, key):
        ret = self._dict.__getitem__(key)
        self._dict.move_to_end(key, last=True)
        return ret

    def __setitem__(self, key, value) -> None:
        if key in self._dict:
            self._dict.move_to_end(key, last=True)
        self._dict[key] = value
        if len(self._dict) > self.size:
            self._dict.popitem(last=False)

    def __delitem__(self, key) -> None:
        return self._dict.__delitem__(key)

    def clear(self):
        return self._dict.clear()

    def pop(self, key):
        return self._dict.pop(key)

    # all of the below access all of the items, and therefore shouldn't modify the ordering for eviction
    def keys(self):
        return self._dict.keys()

    def items(self):
        return self._dict.items()

    def values(self):
        return self._dict.values()
