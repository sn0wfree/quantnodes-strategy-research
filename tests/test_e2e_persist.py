"""E2E test: send multiple messages and verify none disappear."""
import asyncio
import pytest
from playwright.async_api import async_playwright

BASE_URL = "http://127.0.0.1:8783"


async def _login(page):
    await page.goto(f"{BASE_URL}/login")
    await page.wait_for_load_state("networkidle")
    await page.locator('input[type="text"]').first.fill("admin")
    await page.locator('input[type="password"]').first.fill("admin")
    await page.locator('button:has-text("登录")').first.click()
    await page.wait_for_url("**/", timeout=10000)
    await page.wait_for_load_state("networkidle")
    await page.wait_for_timeout(2000)


async def _create_session(page):
    btn = page.locator("button").filter(has_text="+").first
    if await btn.is_visible(timeout=3000):
        await btn.click()
        await page.wait_for_timeout(1000)


async def _send(page, text):
    await page.locator("textarea").first.fill(text)
    await page.wait_for_timeout(300)
    await page.locator("button:has(svg.lucide-send)").first.click()


async def _wait_done(page, timeout_s=30):
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        body = await page.inner_text("body")
        if "Agent" in body and len(body) > 100:
            await page.wait_for_timeout(3000)
            return True
        agent_msgs = page.locator('[class*="assistant"], [class*="Agent"]')
        count = await agent_msgs.count()
        if count > 0:
            text = await agent_msgs.last.inner_text()
            if len(text.strip()) > 5:
                await page.wait_for_timeout(3000)
                return True
        await page.wait_for_timeout(500)
    return False


@pytest.mark.asyncio
async def test_messages_persist_after_new_send():
    """Send 3 messages, screenshot after each, verify none disappear."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1280, "height": 900})

        try:
            await _login(page)
            await _create_session(page)

            msgs = ["记住数字 11", "记住数字 22", "数字是什么？"]

            for i, msg in enumerate(msgs):
                print(f"--- Sending msg {i+1}: {msg}")
                await _send(page, msg)
                await page.wait_for_timeout(2000)
                ok = await _wait_done(page, timeout_s=60)
                assert ok, f"msg {i+1} no response"
                await page.wait_for_timeout(1000)

                # Screenshot
                await page.screenshot(path=f"/tmp/test_persist_{i+1}.png")

                # Count user bubbles (blue, right-aligned with rounded-2xl)
                user_bubbles = page.locator('.rounded-2xl.bg-primary-600')
                count = await user_bubbles.count()
                print(f"  User bubbles visible: {count}")
                assert count == i + 1, f"Expected {i+1} user bubbles, got {count}"

                # Check each user bubble text
                for j in range(count):
                    text = await user_bubbles.nth(j).inner_text()
                    print(f"  Bubble {j}: {text[:30]}")

            print("✅ All messages persisted — none disappeared")
        finally:
            await browser.close()
