import pickle

import diskcache
from anyio import to_thread
from asyncer import asyncify
from asyncstdlib import any_iter

DEFAULT_SETTINGS = {
    "statistics": 0,
    "tag_index": 0,
    "eviction_policy": "least-recently-stored",
    "size_limit": 2**30,
    "cull_limit": 10,
    "sqlite_auto_vacuum": 1,
    "sqlite_cache_size": 2**13,
    "sqlite_journal_mode": "wal",
    "sqlite_mmap_size": 2**26,
    "sqlite_synchronous": 1,
    "disk_min_file_size": 2**15,
    "disk_pickle_protocol": pickle.HIGHEST_PROTOCOL,
}  # False  # False  # 1gb  # FULL  # 8,192 pages  # 64mb  # NORMAL  # 32kb


class MelanieCache(diskcache.Cache):
    @asyncify
    def contains(self, key):
        return super().__contains__(key)

    def __init__(
        self,
        directory=None,
        timeout=1,
        disk=diskcache.Disk,
        size_limit=107374182400,
        cull_limit=10,
        statistics=1,
        tag_index=1,
        eviction_policy="least-recently-stored",
        sqlite_auto_vacuum=1,
        sqlite_cache_size=3145728000,
        sqlite_journal_mode="wal",
        sqlite_mmap_size=3145728000,
        sqlite_synchronous=1,
        disk_min_file_size=2**15,
        disk_pickle_protocol=pickle.HIGHEST_PROTOCOL,
    ) -> None:
        if not directory.startswith("/"):
            directory = f"diskcache/{directory}"

        super().__init__(
            directory,
            timeout,
            disk,
            size_limit=size_limit,
            cull_limit=cull_limit,
            statistics=statistics,
            tag_index=tag_index,
            eviction_policy=eviction_policy,
            sqlite_auto_vacuum=sqlite_auto_vacuum,
            sqlite_cache_size=sqlite_cache_size,
            sqlite_journal_mode=sqlite_journal_mode,
            sqlite_mmap_size=sqlite_mmap_size,
            sqlite_synchronous=sqlite_synchronous,
            disk_min_file_size=disk_min_file_size,
            disk_pickle_protocol=disk_pickle_protocol,
        )
        self.reset("cull_limit", cull_limit)
        self.reset("size_limit", size_limit)

    def __iter__(self):
        """Iterate keys in cache including expired items."""
        msg = "Use the async variant MelanieCache.iter_keys"
        raise NotImplementedError(msg)

    def reset2(self):
        self.reset("sqlite_mmap_size", 3145728000)
        self.reset("sqlite_cache_size", 3145728000)

    def iter_keys(self):
        return any_iter(to_thread.run_sync(self._iter))

    @asyncify
    def set(self, key, value, expire=None, read=False, tag=None, retry=False):
        """Set `key` and `value` item in cache.

        When `read` is `True`, `value` should be a file-like object opened
        for reading in binary mode.

        If database timeout occurs then fails silently unless `retry` is set to
        `True` (default `False`).

        :param key: key for item
        :param value: value for item
        :param float expire: seconds until the key expires
            (default None, no expiry)
        :param bool read: read value as raw bytes from file (default False)
        :param str tag: text to associate with key (default None)
        :param bool retry: retry if database timeout occurs (default False)
        :return: True if item was set

        """
        with self:
            return super().set(key, value, expire, read, tag, retry)

    @asyncify
    def get(self, key, default=None, read=False, expire_time=False, tag=False, retry=False):
        """Retrieve value from cache. If `key` is missing, return `default`.

        If database timeout occurs then returns `default` unless `retry` is set
        to `True` (default `False`).

        :param key: key for item
        :param default: return value if key is missing (default None)
        :param bool read: if True, return file handle to value
            (default False)
        :param float expire_time: if True, return expire_time in tuple
            (default False)
        :param tag: if True, return tag in tuple (default False)
        :param bool retry: retry if database timeout occurs (default False)
        :return: value for item if key is found else default

        """
        with self:
            return super().get(key, default, read, expire_time, tag, retry)

    @asyncify
    def delete(self, key, retry=False):
        """Delete corresponding item for `key` from cache.

        Calls :func:`FanoutCache.delete` internally with `retry` set to `True`.

        :param key: key for item
        :raises KeyError: if key is not found

        """
        with self:
            return super().delete(key, retry)

    @asyncify
    def cull(self, retry=False):
        """Cull items from cache until volume is less than size limit.

        If database timeout occurs then fails silently unless `retry` is set to
        `True` (default `False`).

        :param bool retry: retry if database timeout occurs (default False)
        :return: count of items removed

        """
        with self:
            return super().cull(retry)
