from __future__ import annotations


async def fetch_page(url):
    import asyncio

    from playwright.async_api import async_playwright

    from melanie import log

    async with async_playwright() as p:
        browser = await p.firefox.launch()
        c = await browser.new_context()

        await c.add_cookies(
            [
                {
                    "sameSite": "Lax",
                    "name": "csrftoken",
                    "value": "Zqb2emq3WLoLMAuAaj7l5eLZmafUbGt6Tk8DUvUFROlTSwzB2E4AWGc9xg1uVuvJ",
                    "domain": "logs.discord.website",
                    "path": "/",
                    "expires": 1685152195.493347,
                    "httpOnly": False,
                    "secure": False,
                },
                {
                    "sameSite": "Lax",
                    "name": "sessionid",
                    "value": "1uoghnt5dczbb9qc1dli9kk1eavirkto",
                    "domain": "logs.discord.website",
                    "path": "/",
                    "httpOnly": False,
                    "secure": False,
                },
            ],
        )
        page = await c.new_page()

        await page.goto(url, wait_until="networkidle")

        new_size = 0

        warning = 0
        while True:
            old_size = new_size

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            await asyncio.sleep(0.6)
            content = await page.content()
            new_size = len(content)
            log.warning(new_size)
            if new_size <= old_size:
                warning += 1
                if warning > 2:
                    log.warning("stopping")
                    break

        await page.goto(f"{url}/export")
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        content = await page.content()
        await browser.close()

    return content.encode("UTF-8")
