"""
Playwright Client — 浏览器自动化引擎。

通过 Playwright 操控 creator.douyin.com 实现发布、登录、数据看板等功能。
参考: dreammis/social-auto-upload, withwz/douyin_upload
"""
from __future__ import annotations

import asyncio
import os
import random
from datetime import datetime

from dy_cli.utils import config


class PlaywrightError(Exception):
    """Playwright 操作错误。"""


def _run_async(coro):
    """在同步上下文中运行异步函数。"""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(asyncio.run, coro).result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


class PlaywrightClient:
    """
    抖音 Playwright 自动化客户端。

    功能:
    - 扫码登录 / Cookie 管理
    - 视频发布 / 图文发布
    - 数据看板抓取
    - 通知获取
    """

    CREATOR_URL = "https://creator.douyin.com"
    UPLOAD_URL = "https://creator.douyin.com/creator-micro/content/upload"
    ANALYTICS_URL = "https://creator.douyin.com/creator-micro/data/stats/self-content"
    DOUYIN_URL = "https://www.douyin.com"

    def __init__(
        self,
        account: str | None = None,
        headless: bool = False,
        slow_mo: int = 0,
    ):
        self.account = account or "default"
        self.headless = headless
        self.slow_mo = slow_mo
        self.cookie_file = config.get_cookie_file(self.account)

    # ------------------------------------------------------------------
    # Cookie management
    # ------------------------------------------------------------------

    def cookie_exists(self) -> bool:
        """检查 Cookie 文件是否存在。"""
        return os.path.isfile(self.cookie_file)

    def check_login(self) -> bool:
        """验证 Cookie 是否有效。"""
        if not self.cookie_exists():
            return False
        return _run_async(self._check_login_async())

    async def _check_login_async(self) -> bool:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context(storage_state=self.cookie_file)
                page = await context.new_page()
                await page.goto(self.UPLOAD_URL, wait_until="domcontentloaded")
                try:
                    await page.wait_for_url(
                        "**/creator-micro/content/upload**",
                        timeout=8000,
                    )
                except Exception:
                    return False

                # Check if redirected to login page
                if await page.get_by_text("手机号登录").count() > 0:
                    return False
                if await page.get_by_text("扫码登录").count() > 0:
                    return False

                return True
            finally:
                await browser.close()

    # ------------------------------------------------------------------
    # Login
    # ------------------------------------------------------------------

    def login(self) -> bool:
        """打开浏览器扫码登录，保存 Cookie。"""
        return _run_async(self._login_async())

    async def _login_async(self) -> bool:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=False, slow_mo=self.slow_mo)
            context = await browser.new_context()
            page = await context.new_page()
            await page.goto(self.CREATOR_URL, wait_until="domcontentloaded")

            print("[dy] 请使用抖音 App 扫码登录...")
            print("[dy] 登录成功后，浏览器会自动关闭")

            # Wait for user to login — detect navigation to creator dashboard
            try:
                await page.wait_for_url(
                    "**/creator-micro/**",
                    timeout=120000,  # 2 minutes
                )
                await page.wait_for_timeout(3000)
            except Exception:
                print("[dy] 登录超时")
                await browser.close()
                return False

            # Visit multiple pages to collect ALL cookies
            print("[dy] 正在收集完整 Cookie...")
            for url in [
                "https://www.douyin.com/",
                "https://creator.douyin.com/creator-micro/content/manage",
            ]:
                try:
                    await page.goto(url, wait_until="domcontentloaded")
                    await page.wait_for_timeout(2000)
                except Exception:
                    pass

            # Save cookies
            os.makedirs(os.path.dirname(self.cookie_file), exist_ok=True)
            await context.storage_state(path=self.cookie_file)

            cookies = await context.cookies()
            douyin_count = len([c for c in cookies if "douyin" in c.get("domain", "")])
            print(f"[dy] Cookie 已保存: {douyin_count} 个 ({self.cookie_file})")
            await browser.close()
            return True

    def logout(self) -> bool:
        """删除 Cookie 文件。"""
        if os.path.isfile(self.cookie_file):
            os.remove(self.cookie_file)
            return True
        return False

    # ------------------------------------------------------------------
    # Publish helpers (登录断言 / 可见范围 / 正文填充 / 结果判定)
    # ------------------------------------------------------------------

    async def _assert_logged_in(self, page) -> None:
        """发布前断言登录态；若被重定向到登录页则抛错（#7 发布前真实校验）。"""
        url = page.url
        if "passport" in url or "login" in url:
            raise PlaywrightError("Cookie 已失效，请重新登录: dy login")
        for txt in ("扫码登录", "手机号登录", "账号登录"):
            if await page.get_by_text(txt).count() > 0:
                raise PlaywrightError("Cookie 已失效，请重新登录: dy login")

    async def _set_visibility(self, page, visibility: str) -> None:
        """设置可见范围：公开(默认) / 好友可见 / 仅自己可见（#2 三档全覆盖，去重）。"""
        mapping = {
            "公开": "公开",
            "好友可见": "好友可见",
            "仅自己可见": "仅自己可见",
            "私密": "仅自己可见",
        }
        target = mapping.get((visibility or "公开").strip(), "公开")
        if target == "公开":
            return
        try:
            trigger = page.locator("text=谁可以看").first
            if await trigger.count() == 0:
                return
            await trigger.click()
            await page.wait_for_timeout(600)
            opt = page.locator(f"text={target}").first
            if await opt.count() > 0:
                await opt.click()
                await page.wait_for_timeout(400)
        except Exception:
            print(f"[dy] 可见范围设置失败（{visibility}），保持默认公开")

    async def _fill_content(self, page, content: str, tags=None, mentions=None) -> None:
        """填写正文，并可靠插入话题(#)与 @好友 chip（#3）。"""
        editor = page.locator('[contenteditable="true"]').first
        try:
            await editor.wait_for(timeout=6000)
        except Exception:
            return
        await editor.click()
        if content:
            await page.keyboard.type(content, delay=40)
        for tag in (tags or []):
            await page.keyboard.type(f"#{tag}", delay=40)
            await self._commit_chip(page)
        for m in (mentions or []):
            await page.keyboard.type(f"@{m}", delay=40)
            await self._commit_chip(page)

    async def _commit_chip(self, page) -> None:
        """话题/@ 输入后等待下拉建议，回车选中；无建议则空格分隔（#3 兜底）。"""
        try:
            await page.wait_for_timeout(700)
            sugg = page.locator(
                '[class*=suggest], [class*=mention], [class*=topic] li, [class*=user] li'
            ).first
            if await sugg.count() > 0:
                await page.keyboard.press("Enter")
            else:
                await page.keyboard.press(" ")
        except Exception:
            try:
                await page.keyboard.press(" ")
            except Exception:
                pass

    async def _try_capture_work_url(self, page):
        """尽力从页面抓取新发布作品的链接（best-effort）。"""
        try:
            return await page.evaluate("""() => {
                const links = Array.from(document.querySelectorAll('a[href]'));
                for (const a of links) {
                    const h = a.href || '';
                    if (/\\/(video|note|aweme)\\//.test(h) && !h.includes('creator')) return h;
                }
                return null;
            }""")
        except Exception:
            return None

    async def _wait_publish_result(self, page):
        """点击发布后判定真实结果，返回 (status, url, message)（#1 真实成功判定）。"""
        await page.wait_for_timeout(4000)
        fail_kw = ["失败", "错误", "频繁", "审核", "请完善", "已存在", "违规", "不能", "无法", "不正确"]
        success_kw = ["成功", "已发布", "提交成功"]
        toast = await page.evaluate(
            '()=>Array.from(document.querySelectorAll("[class*=toast]"))'
            '.map(t=>t.textContent.trim()).filter(Boolean)'
        )
        toast_str = " ".join(toast) if toast else ""
        if any(k in toast_str for k in fail_kw):
            reason = next((t for t in toast if any(k in t for k in fail_kw)), toast_str)
            return ("failed", None, reason)
        if any(k in toast_str for k in success_kw):
            return ("published", await self._try_capture_work_url(page), "成功")
        if "manage" in page.url:
            return ("published", await self._try_capture_work_url(page), "跳转作品管理页")
        for _ in range(10):
            await page.wait_for_timeout(2000)
            if "manage" in page.url:
                return ("published", await self._try_capture_work_url(page), "跳转作品管理页")
            t2 = await page.evaluate(
                '()=>Array.from(document.querySelectorAll("[class*=toast]"))'
                '.map(t=>t.textContent.trim()).filter(Boolean)'
            )
            t2s = " ".join(t2) if t2 else ""
            if any(k in t2s for k in success_kw):
                return ("published", await self._try_capture_work_url(page), "成功")
            if any(k in t2s for k in fail_kw):
                return ("failed", None, " ".join(t2))
        return ("submitted", None, "未检测到明确结果，请到创作者中心确认")

    # ------------------------------------------------------------------
    # Publish video
    # ------------------------------------------------------------------

    def publish_video(
        self,
        title: str,
        content: str,
        video_path: str,
        tags: list[str] | None = None,
        visibility: str = "公开",
        schedule_at: str | None = None,
        thumbnail_path: str | None = None,
        mentions: list[str] | None = None,
    ) -> dict:
        """发布视频到抖音。"""
        if not os.path.isfile(video_path):
            raise PlaywrightError(f"视频文件不存在: {video_path}")
        if not self.cookie_exists():
            raise PlaywrightError("未登录，请先运行: dy login")

        return _run_async(
            self._publish_video_async(
                title, content, video_path, tags, visibility, schedule_at, thumbnail_path, mentions
            )
        )

    async def _publish_video_async(
        self,
        title: str,
        content: str,
        video_path: str,
        tags: list[str] | None,
        visibility: str,
        schedule_at: str | None,
        thumbnail_path: str | None,
        mentions: list[str] | None,
    ) -> dict:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self.headless,
                slow_mo=self.slow_mo,
            )
            context = await browser.new_context(storage_state=self.cookie_file)
            page = await context.new_page()

            try:
                # Navigate to upload page
                await page.goto(self.UPLOAD_URL, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)

                # Check login (发布前断言登录态)
                await self._assert_logged_in(page)

                # Upload video file
                upload_input = page.locator('input[type="file"]').first
                await upload_input.set_input_files(video_path)
                print(f"[dy] 正在上传视频: {os.path.basename(video_path)}")

                # Wait for upload to complete (look for editor/title input)
                await page.wait_for_timeout(5000)

                # Wait for upload progress to finish
                for _ in range(120):  # max 10 minutes
                    # Check if upload is complete
                    ready = await page.locator('[class*="title"] input, [class*="title"] textarea, [contenteditable="true"]').count()
                    if ready > 0:
                        break
                    await page.wait_for_timeout(5000)

                # Fill title — find the title input
                title_input = page.locator('[class*="title"] input, [class*="title"] textarea').first
                try:
                    await title_input.wait_for(timeout=5000)
                    await title_input.clear()
                    await title_input.fill(title)
                except Exception:
                    # Try contenteditable
                    pass

                # Fill description/content + 话题/@ (可靠插入 chip)
                await self._fill_content(page, content, tags, mentions)

                # 可见范围留待封面上传后统一设置
                # (实际设置见下方 _set_visibility 调用)

                # Handle schedule
                if schedule_at:
                    await self._set_schedule_time(page, schedule_at)

                # Set thumbnail if provided
                if thumbnail_path and os.path.isfile(thumbnail_path):
                    try:
                        cover_btn = page.locator('text=选择封面').first
                        if await cover_btn.count() > 0:
                            await cover_btn.click()
                            await page.wait_for_timeout(1000)
                            cover_upload = page.locator('input[type="file"]').last
                            await cover_upload.set_input_files(thumbnail_path)
                            await page.wait_for_timeout(2000)
                    except Exception:
                        pass

                # Handle cover (required by Douyin)
                await self._select_cover(page)

                # 设置可见范围 (覆盖 公开/好友可见/仅自己可见)
                await self._set_visibility(page, visibility)

                # Click the EXACT "发布" button (not "高清发布")
                await page.wait_for_timeout(2000)
                clicked = await page.evaluate("""() => {
                    const btns = document.querySelectorAll('button');
                    for (const b of btns) {
                        if (b.textContent.trim() === '发布' && !b.disabled) {
                            b.click();
                            return true;
                        }
                    }
                    return false;
                }""")

                if not clicked:
                    print("[dy] 未找到发布按钮，内容已填写，请手动确认")
                    if not self.headless:
                        await page.wait_for_timeout(30000)
                    return {"status": "submitted", "title": title,
                            "message": "未找到发布按钮，请手动确认"}

                status, url, message = await self._wait_publish_result(page)
                print(f"[dy] 发布结果: {status} - {message}")
                if url:
                    print(f"[dy] 作品链接: {url}")
                return {"status": status, "title": title, "url": url, "message": message}

            finally:
                await context.storage_state(path=self.cookie_file)
                await browser.close()

    async def _select_cover(self, page):
        """选择视频封面（必填项）。"""
        try:
            # Dismiss any overlay guides
            await page.evaluate(
                '()=>document.querySelectorAll("[class*=shepherd]").forEach(e=>e.remove())'
            )

            # Wait for AI cover generation
            for _ in range(15):
                await page.wait_for_timeout(1000)
                if await page.locator('text=生成中').count() == 0:
                    break

            # Click the cover area to open cover editor
            cover_divs = await page.evaluate("""() => {
                const els = document.querySelectorAll('[class*="cover"]');
                for (const el of els) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 100 && r.height > 80 && r.width < 300 &&
                        el.textContent.includes('选择封面') && el.onclick !== undefined) {
                        return {x: r.x + r.width/2, y: r.y + r.height/2};
                    }
                }
                // Fallback: find by text
                const all = document.querySelectorAll('div');
                for (const el of all) {
                    const r = el.getBoundingClientRect();
                    if (el.textContent.trim() === '选择封面' && r.width > 50 && r.height > 50) {
                        return {x: r.x + r.width/2, y: r.y + r.height/2};
                    }
                }
                return null;
            }""")

            if cover_divs:
                await page.mouse.click(int(cover_divs["x"]), int(cover_divs["y"]))
                await page.wait_for_timeout(5000)

                # Click "完成" in cover editor to accept default frame
                done_btn = page.get_by_role("button", name="完成")
                if await done_btn.count() > 0:
                    await done_btn.last.click(force=True)
                    await page.wait_for_timeout(2000)
                    print("[dy] 封面已设置")
            else:
                print("[dy] 未找到封面选择区域")
        except Exception as e:
            print(f"[dy] 封面设置跳过: {e}")

    # ------------------------------------------------------------------
    # Publish image/text
    # ------------------------------------------------------------------

    async def _apply_default_bgm(self, page) -> None:
        """图文发布前自动选择系统默认/随机 BGM（best-effort，失败静默）。

        抖音图文发布页存在"添加音乐"入口；若当前未配置 BGM，自动从
        系统推荐音乐中随机选一首，避免因缺少/无效 BGM 导致发布报错。
        任何步骤失败都只打印提示，绝不抛出异常、绝不阻断发布。
        """
        try:
            # 1) 查找"添加音乐"入口（文案优先，class 兜底）
            add_music = None
            for sel in (
                "text=添加音乐",
                "text=添加背景音乐",
                "text=选择音乐",
                '[class*="addMusic"]',
                '[class*="add-music"]',
                '[class*="music-btn"]',
                '[class*="bgm"] button',
                '[class*="music"] button',
            ):
                loc = page.locator(sel).first
                try:
                    if await loc.count() > 0 and await loc.is_visible():
                        add_music = loc
                        break
                except Exception:
                    continue
            if add_music is None:
                return  # 页面无 BGM 功能，无需处理

            await add_music.click()
            await page.wait_for_timeout(1200)

            # 2) 优先切换到"推荐 / 系统推荐 / 热门"分类
            for tab_sel in ("text=推荐", "text=系统推荐", "text=热门", "text=为你推荐"):
                tab = page.locator(tab_sel).first
                try:
                    if await tab.count() > 0 and await tab.is_visible():
                        await tab.click()
                        await page.wait_for_timeout(800)
                        break
                except Exception:
                    continue

            # 3) 从可见音乐列表中随机选一首
            items = page.locator(
                '[class*="music-item"], [class*="song-item"], [class*="music-list"] li, '
                '[class*="recommend"] [class*="item"], [class*="audio"] [class*="item"], '
                '[class*="music"] li'
            )
            n = await items.count()
            if n == 0:
                # 面板没有可选音乐，直接关闭
                await page.keyboard.press("Escape")
                return
            idx = random.randint(0, n - 1)
            await items.nth(idx).click()
            await page.wait_for_timeout(1000)

            # 4) 确认使用（部分版本需要点"使用 / 确定 / 完成"）
            for ok_sel in ("text=使用", "text=确定", "text=完成", "text=保存"):
                ok_btn = page.locator(ok_sel).first
                try:
                    if await ok_btn.count() > 0 and await ok_btn.is_visible():
                        await ok_btn.click()
                        await page.wait_for_timeout(600)
                        break
                except Exception:
                    continue
            print("[dy] 已选择系统默认 BGM（随机）")
        except Exception as e:
            print(f"[dy] BGM 自动选择跳过: {e}")

    def publish_image_text(
        self,
        title: str,
        content: str,
        images: list[str],
        tags: list[str] | None = None,
        visibility: str = "公开",
        schedule_at: str | None = None,
        mentions: list[str] | None = None,
    ) -> dict:
        """发布图文到抖音。"""
        for img in images:
            if not img.startswith("http") and not os.path.isfile(img):
                raise PlaywrightError(f"图片文件不存在: {img}")
        if not self.cookie_exists():
            raise PlaywrightError("未登录，请先运行: dy login")

        return _run_async(
            self._publish_image_text_async(
                title, content, images, tags, visibility, schedule_at, mentions
            )
        )

    async def _publish_image_text_async(
        self,
        title: str,
        content: str,
        images: list[str],
        tags: list[str] | None,
        visibility: str,
        schedule_at: str | None,
        mentions: list[str] | None,
    ) -> dict:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=self.headless,
                slow_mo=self.slow_mo,
            )
            context = await browser.new_context(storage_state=self.cookie_file)
            page = await context.new_page()

            try:
                # Navigate to image publish page
                await page.goto(self.UPLOAD_URL, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)

                # Check login (发布前断言登录态)
                await self._assert_logged_in(page)

                # Switch to image tab if present
                try:
                    img_tab = page.locator('text=图文').first
                    if await img_tab.count() > 0:
                        await img_tab.click()
                        await page.wait_for_timeout(1000)
                except Exception:
                    pass

                # Upload images — only local files
                local_images = [img for img in images if not img.startswith("http")]
                if local_images:
                    upload_input = page.locator('input[type="file"][accept*="image"]').first
                    try:
                        await upload_input.wait_for(timeout=5000)
                        await upload_input.set_input_files(local_images)
                        print(f"[dy] 正在上传 {len(local_images)} 张图片")
                        await page.wait_for_timeout(3000)
                    except Exception:
                        # Try generic file input
                        upload_input = page.locator('input[type="file"]').first
                        await upload_input.set_input_files(local_images)
                        await page.wait_for_timeout(3000)

                # Fill title
                title_input = page.locator('[class*="title"] input, [class*="title"] textarea').first
                try:
                    await title_input.wait_for(timeout=5000)
                    await title_input.clear()
                    await title_input.fill(title)
                except Exception:
                    pass

                # Fill content + 话题/@ (可靠插入 chip)
                await self._fill_content(page, content, tags, mentions)

                # 设置可见范围
                await self._set_visibility(page, visibility)

                # 自动选择系统默认 BGM（best-effort，失败不影响发布）
                await self._apply_default_bgm(page)

                # Handle schedule
                if schedule_at:
                    await self._set_schedule_time(page, schedule_at)

                # Click the EXACT "发布" button (not "高清发布")
                await page.wait_for_timeout(2000)
                clicked = await page.evaluate("""() => {
                    const btns = document.querySelectorAll('button');
                    for (const b of btns) {
                        if (b.textContent.trim() === '发布' && !b.disabled) {
                            b.click();
                            return true;
                        }
                    }
                    return false;
                }""")

                if not clicked:
                    print("[dy] 未找到发布按钮，内容已填写，请手动确认")
                    if not self.headless:
                        await page.wait_for_timeout(30000)
                    return {"status": "submitted", "title": title,
                            "message": "未找到发布按钮，请手动确认"}

                status, url, message = await self._wait_publish_result(page)
                print(f"[dy] 发布结果: {status} - {message}")
                if url:
                    print(f"[dy] 作品链接: {url}")
                return {"status": status, "title": title, "url": url, "message": message}

            finally:
                await context.storage_state(path=self.cookie_file)
                await browser.close()

    # ------------------------------------------------------------------
    # Schedule helper
    # ------------------------------------------------------------------

    async def _set_schedule_time(self, page, schedule_at: str):
        """设置定时发布时间。"""
        try:
            # Parse datetime
            dt = datetime.fromisoformat(schedule_at)
            date_str = dt.strftime("%Y年%m月%d日 %H:%M")

            # Find schedule checkbox/toggle
            schedule_toggle = page.locator('text=定时发布').first
            if await schedule_toggle.count() > 0:
                await schedule_toggle.click()
                await page.wait_for_timeout(1000)

                # Find and fill the datetime picker
                time_input = page.locator('[class*="schedule"] input, [class*="time"] input').first
                if await time_input.count() > 0:
                    await time_input.clear()
                    await time_input.fill(date_str)
                    await page.keyboard.press("Enter")
        except Exception:
            print("[dy] 定时发布设置失败，将立即发布")

    # ------------------------------------------------------------------
    # Analytics
    # ------------------------------------------------------------------

    def get_analytics(self, page_size: int = 10) -> dict:
        """获取创作者数据看板。"""
        if not self.cookie_exists():
            raise PlaywrightError("未登录")
        return _run_async(self._get_analytics_async(page_size))

    async def _get_analytics_async(self, page_size: int) -> dict:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=self.cookie_file)
            page = await context.new_page()

            try:
                # Intercept XHR responses to capture analytics API data
                api_data = {}

                async def on_response(response):
                    url = response.url
                    if "content/data" in url or "item/list" in url or "data/stats" in url:
                        try:
                            body = await response.json()
                            api_data[url.split("?")[0].split("/")[-1]] = body
                        except Exception:
                            pass

                page.on("response", on_response)

                # Navigate to creator center
                await page.goto(self.CREATOR_URL, wait_until="domcontentloaded")
                await page.wait_for_timeout(3000)

                if await page.get_by_text("扫码登录").count() > 0:
                    raise PlaywrightError("Cookie 已失效")

                # Navigate to content analytics
                await page.goto(self.ANALYTICS_URL, wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)

                # Try clicking "作品数据" tab
                for tab_name in ["作品数据", "作品管理"]:
                    try:
                        tab = page.locator(f"text={tab_name}").first
                        if await tab.count() > 0:
                            await tab.click()
                            await page.wait_for_timeout(3000)
                            break
                    except Exception:
                        pass

                # If we captured API data, use it directly
                if api_data:
                    return {"rows": [], "api_data": api_data, "url": page.url}

                # Fallback: scrape page content as structured text
                page_data = await page.evaluate("""() => {
                    const result = {rows: [], summary: {}, url: window.location.href};

                    // Get page text in structured blocks
                    const blocks = [];
                    document.querySelectorAll('main, [class*="content"]').forEach(el => {
                        if (el.offsetHeight > 100 && el.innerText.length > 20) {
                            blocks.push(el.innerText.substring(0, 2000));
                        }
                    });
                    result.page_content = blocks.slice(0, 3).join('\\n---\\n');

                    // Extract any visible metrics
                    document.querySelectorAll('[class*="metric"], [class*="stat"], [class*="overview"] > div').forEach(el => {
                        const text = el.innerText.trim();
                        if (text && text.length < 100) {
                            const parts = text.split('\\n');
                            if (parts.length >= 2) {
                                result.summary[parts[0]] = parts[1];
                            }
                        }
                    });

                    return result;
                }""")

                return page_data

            finally:
                await browser.close()

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------

    def get_notifications(self) -> dict:
        """获取消息通知。"""
        if not self.cookie_exists():
            raise PlaywrightError("未登录")
        return _run_async(self._get_notifications_async())

    async def _get_notifications_async(self) -> dict:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(storage_state=self.cookie_file)
            page = await context.new_page()

            try:
                await page.goto(
                    "https://creator.douyin.com/creator-micro/message",
                    wait_until="domcontentloaded",
                )
                await page.wait_for_timeout(5000)

                data = await page.evaluate("""() => {
                    const notifications = [];
                    const items = document.querySelectorAll('[class*="message-item"], [class*="notification-item"]');
                    items.forEach(item => {
                        notifications.push({
                            type: item.querySelector('[class*="type"]')?.textContent?.trim() || '-',
                            user: item.querySelector('[class*="name"]')?.textContent?.trim() || '-',
                            content: item.querySelector('[class*="content"]')?.textContent?.trim() || '-',
                            time: item.querySelector('[class*="time"]')?.textContent?.trim() || '-',
                        });
                    });
                    return { mentions: notifications };
                }""")

                return data

            finally:
                await browser.close()

    # ------------------------------------------------------------------
    # Comments (Playwright scraping — API needs a-bogus signature)
    # ------------------------------------------------------------------

    def get_comments(self, aweme_id: str, count: int = 20) -> list[dict]:
        """从视频页面抓取评论。"""
        if not self.cookie_exists():
            raise PlaywrightError("未登录")
        try:
            return _run_async(self._get_comments_async(aweme_id, count))
        except Exception as e:
            raise PlaywrightError(f"抓取评论失败: {e}") from e

    async def _get_comments_async(self, aweme_id: str, count: int) -> list[dict]:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                storage_state=self.cookie_file,
                viewport={"width": 1920, "height": 1080},
            )
            page = await context.new_page()

            try:
                await page.goto(
                    f"https://www.douyin.com/video/{aweme_id}",
                    wait_until="domcontentloaded",
                    timeout=15_000,
                )
                await page.wait_for_timeout(6000)

                # Scroll to load more comments
                for _ in range(max(0, count // 10 - 1)):
                    await page.evaluate("window.scrollBy(0, 800)")
                    await page.wait_for_timeout(1500)

                comments = await page.evaluate("""() => {
                    const items = document.querySelectorAll('[data-e2e="comment-item"]');
                    const results = [];
                    items.forEach(item => {
                        const lines = (item.innerText || '').split('\\n').filter(l => l.trim());
                        if (lines.length < 2) return;

                        const nickname = lines[0] || '';
                        const isAuthor = lines.includes('作者');

                        // Find the main comment text (skip '作者', '...' etc)
                        let text = '';
                        for (let i = 1; i < lines.length; i++) {
                            const l = lines[i];
                            if (l === '作者' || l === '...' || l === '展开' || l.length < 2) continue;
                            if (/^\\d+[天时分秒]前/.test(l) || /^\\d{4}/.test(l) || /·/.test(l)) break;
                            text = l;
                            break;
                        }

                        // Find likes (last numeric item)
                        let digg = 0;
                        const last = lines[lines.length - 1];
                        if (/^\\d+$/.test(last)) digg = parseInt(last);

                        if (nickname && text) {
                            results.push({
                                user: {nickname: nickname},
                                text: text,
                                digg_count: digg,
                                is_author: isAuthor,
                            });
                        }
                    });
                    return results;
                }""")

                return comments[:count]

            finally:
                await browser.close()

    # ------------------------------------------------------------------
    # Interactions (like / comment / favorite / follow)
    # ------------------------------------------------------------------

    def interact(self, aweme_id: str, action: str, **kwargs) -> dict:
        """
        在 douyin.com 视频页面执行互动操作。

        action: "like" | "unlike" | "favorite" | "unfavorite" | "comment" | "follow" | "unfollow"
        kwargs: content (for comment), sec_user_id (for follow)
        """
        if not self.cookie_exists():
            raise PlaywrightError("未登录，请先运行: dy login")
        return _run_async(self._interact_async(aweme_id, action, **kwargs))

    async def _interact_async(self, aweme_id: str, action: str, **kwargs) -> dict:
        from playwright.async_api import async_playwright
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            context = await browser.new_context(
                storage_state=self.cookie_file,
                viewport={"width": 1920, "height": 1080},
            )
            page = await context.new_page()

            try:
                if action in ("follow", "unfollow"):
                    return await self._do_follow(page, kwargs.get("sec_user_id", aweme_id), action)

                # Navigate to video page
                url = f"https://www.douyin.com/video/{aweme_id}"
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(5000)
                # Wait for action buttons to load
                for _ in range(10):
                    if await page.locator('[data-e2e="video-player-digg"]').count() > 0:
                        break
                    await page.wait_for_timeout(1000)

                if action == "like":
                    return await self._do_like(page, aweme_id)
                elif action == "unlike":
                    return await self._do_like(page, aweme_id, undo=True)
                elif action == "favorite":
                    return await self._do_favorite(page, aweme_id)
                elif action == "unfavorite":
                    return await self._do_favorite(page, aweme_id, undo=True)
                elif action == "comment":
                    return await self._do_comment(page, aweme_id, kwargs.get("content", ""))
                else:
                    raise PlaywrightError(f"未知操作: {action}")

            finally:
                await context.storage_state(path=self.cookie_file)
                await browser.close()

    async def _do_like(self, page, aweme_id: str, undo: bool = False) -> dict:
        """点赞/取消点赞 — JS 直接点击，绕过可见性检查。"""
        clicked = await page.evaluate("""() => {
            const el = document.querySelector('[data-e2e="video-player-digg"]');
            if (el) { el.click(); return true; }
            return false;
        }""")
        await page.wait_for_timeout(1500)
        return {"action": "unlike" if undo else "like", "aweme_id": aweme_id, "success": clicked}

    async def _do_favorite(self, page, aweme_id: str, undo: bool = False) -> dict:
        """收藏/取消收藏 — JS 直接点击。"""
        clicked = await page.evaluate("""() => {
            const el = document.querySelector('[data-e2e="video-player-collect"]');
            if (el) { el.click(); return true; }
            return false;
        }""")
        await page.wait_for_timeout(1500)
        return {"action": "unfavorite" if undo else "favorite", "aweme_id": aweme_id, "success": clicked}

    async def _do_comment(self, page, aweme_id: str, content: str) -> dict:
        """发表评论。"""
        if not content:
            raise PlaywrightError("评论内容不能为空")

        commented = False
        # Click the visible comment input container to open the editor.
        clicked = await page.evaluate("""() => {
            const el = document.querySelector('.comment-input-inner-container')
                || Array.from(document.querySelectorAll('*')).find(node =>
                    (node.innerText || node.textContent || '').trim() === '留下你的精彩评论吧'
                );
            if (el) { el.click(); return true; }
            return false;
        }""")
        if clicked:
            await page.wait_for_timeout(800)

        # Find comment input (contenteditable or textarea)
        input_sel = page.locator(
            '[data-e2e="comment-input"], '
            '[class*="comment"] [contenteditable="true"], '
            '.public-DraftEditor-content[contenteditable="true"], '
            '[role="combobox"][contenteditable="true"], '
            '[contenteditable="true"], '
            'textarea, '
            '[placeholder*="善语结善缘"], [placeholder*="说点什么"], [placeholder*="评论"]'
        )
        if await input_sel.count() > 0:
            await input_sel.first.click()
            await page.wait_for_timeout(500)
            await page.keyboard.type(content, delay=30)
            await page.wait_for_timeout(500)

            # Submit
            send = page.locator(
                '[data-e2e="comment-post"], '
                'button:has-text("发布"), '
                '.comment-input-inner-container .wchsYBpK'
            ).last
            if await send.count() > 0:
                await send.click()
                commented = True
            else:
                await page.keyboard.press("Enter")
                commented = True

        await page.wait_for_timeout(2000)
        return {"action": "comment", "aweme_id": aweme_id, "content": content, "success": commented}

    async def _do_follow(self, page, sec_user_id: str, action: str) -> dict:
        """关注/取消关注用户。"""
        await page.goto(f"https://www.douyin.com/user/{sec_user_id}", wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)

        if action == "follow":
            btn = page.locator('[data-e2e="user-info-follow"], button:has-text("关注")')
        else:
            btn = page.locator('button:has-text("已关注"), button:has-text("互相关注")')

        clicked = False
        if await btn.count() > 0:
            await btn.first.click()
            clicked = True
            await page.wait_for_timeout(1500)

        return {"action": action, "sec_user_id": sec_user_id, "success": clicked}
