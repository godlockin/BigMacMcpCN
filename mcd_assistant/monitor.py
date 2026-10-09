"""Activity monitor - periodically checks for new deals, coupons, and events."""
import asyncio
import json
import os
from datetime import datetime
from typing import Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from .config import Config
from .mcp_client import McpClient, McpClientError
from .notifier import Notifier


class ActivityMonitor:
    """Background monitor for McDonald's activities, deals, and promotions.

    Periodically checks:
    - Campaign calendar (new marketing events)
    - Available coupons (麦麦省 new coupons to claim)
    - Lottery activities (new draw events)
    - Theme party events (new party/品鉴会 openings)

    In auto_mode, automatically claims coupons and enters lottery.
    """

    def __init__(
        self,
        client: McpClient,
        config: Config,
        notifier: Notifier,
    ):
        self.client = client
        self.config = config
        self.notifier = notifier
        self.scheduler = AsyncIOScheduler()
        self.cache_file = os.path.join(config.data_dir, "monitor_cache.json")
        self._cache = self._load_cache()
        self._running = False

    def _load_cache(self) -> dict:
        """Load cached state from disk."""
        if os.path.exists(self.cache_file):
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {
            "last_check": None,
            "known_campaigns": [],
            "known_coupons": [],
            "known_lottery": None,
            "known_parties": [],
            "auto_claimed_coupons": [],
        }

    def _save_cache(self):
        """Save cache to disk."""
        self._cache["last_check"] = datetime.now().isoformat()
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(self._cache, f, ensure_ascii=False, indent=2)

    async def start(self):
        """Start the background monitor."""
        if self._running:
            return

        interval = self.config.monitor_interval_minutes
        self.scheduler.add_job(
            self._run_check_cycle,
            trigger=IntervalTrigger(minutes=interval),
            id="mcd_monitor",
            replace_existing=True,
            next_run_time=datetime.now(),  # Run immediately on start
        )
        self.scheduler.start()
        self._running = True
        await self.notifier.notify(
            "监控已启动",
            f"麦当劳活动监控已启动，每 {interval} 分钟检查一次\n"
            f"自动模式: {'开启' if self.config.auto_mode else '关闭'}",
            level="success",
        )

    async def stop(self):
        """Stop the background monitor."""
        if self._running:
            self.scheduler.shutdown(wait=False)
            self._running = False
            await self.notifier.notify("监控已停止", "麦当劳活动监控已停止", level="info")

    @property
    def is_running(self) -> bool:
        return self._running

    async def _run_check_cycle(self):
        """Run a full check cycle for all activity types."""
        await self.notifier.notify(
            "开始检查",
            "正在扫描麦当劳最新活动、优惠和积分动态...",
            level="info",
        )

        await self._check_campaigns()
        await self._check_available_coupons()
        await self._check_lottery()
        await self._check_my_account()

        self._save_cache()

        await self.notifier.notify(
            "检查完成",
            f"本轮检查完成，下次检查: {self.config.monitor_interval_minutes}分钟后",
            level="info",
        )

    async def _check_campaigns(self):
        """Check campaign calendar for new events."""
        try:
            result = await self.client.call_tool_safe("campaign-calendar")
            if isinstance(result, dict) and "error" in result:
                return

            current_campaigns = self._extract_items(result, "campaigns")
            known = set(self._cache.get("known_campaigns", []))
            new_campaigns = []

            for campaign in current_campaigns:
                title = campaign.get("title") or campaign.get("name") or str(campaign)[:50]
                if title not in known:
                    new_campaigns.append(campaign)
                    known.add(title)

            self._cache["known_campaigns"] = list(known)

            if new_campaigns:
                for camp in new_campaigns:
                    title = camp.get("title") or camp.get("name", "未知活动")
                    desc = camp.get("description") or camp.get("detail", "")
                    start = camp.get("startDate") or camp.get("start_date", "")
                    end = camp.get("endDate") or camp.get("end_date", "")
                    msg = f"麦当劳推出新活动: {title}"
                    if desc:
                        msg += f"\n详情: {desc[:100]}"
                    if start or end:
                        msg += f"\n活动时间: {start} ~ {end}"
                    await self.notifier.notify_deal(
                        "新活动",
                        msg,
                    )
            else:
                await self.notifier.notify(
                    "活动日历",
                    "暂无新活动，当前活动已全部记录",
                    level="info",
                )

        except Exception as e:
            await self.notifier.notify_error("活动检查失败", str(e))

    async def _check_available_coupons(self):
        """Check 麦麦省 for new claimable coupons."""
        try:
            result = await self.client.call_tool_safe("available-coupons")
            if isinstance(result, dict) and "error" in result:
                return

            current_coupons = self._extract_items(result, "coupons")
            known = set(self._cache.get("known_coupons", []))
            new_coupons = []

            for coupon in current_coupons:
                coupon_id = coupon.get("couponId") or coupon.get("id") or str(coupon)[:50]
                title = coupon.get("title") or coupon.get("name", "未知优惠券")
                if coupon_id not in known:
                    new_coupons.append(coupon)
                    known.add(coupon_id)

            self._cache["known_coupons"] = list(known)

            if new_coupons:
                titles = [c.get("title", c.get("name", "?")) for c in new_coupons]
                await self.notifier.notify_deal(
                    "新优惠券",
                    f"发现 {len(new_coupons)} 张新可领优惠券!\n"
                    f"优惠券: {', '.join(titles[:5])}"
                    + (f" 等{len(new_coupons)}张" if len(new_coupons) > 5 else ""),
                )

                # Auto-claim in auto mode
                if self.config.auto_mode and len(new_coupons) > 0:
                    await self._auto_claim_coupons()
                elif not self.config.auto_mode:
                    await self.notifier.notify(
                        "领券提醒",
                        "检测到新优惠券，输入 'claim' 手动领取，或开启 auto 模式自动领取",
                        level="warning",
                    )
            else:
                await self.notifier.notify(
                    "麦麦省",
                    "暂无新可领优惠券",
                    level="info",
                )

        except Exception as e:
            await self.notifier.notify_error("优惠券检查失败", str(e))

    async def _auto_claim_coupons(self):
        """Auto-claim all available coupons."""
        try:
            result = await self.client.call_tool("auto-bind-coupons")
            claimed = self._extract_items(result, "coupons")
            titles = [c.get("title", c.get("name", "?")) for c in claimed]
            self._cache.setdefault("auto_claimed_coupons", []).extend(titles)
            await self.notifier.notify(
                "自动领券成功",
                f"已自动领取 {len(claimed)} 张优惠券: {', '.join(titles[:5])}",
                level="success",
                action=f"auto-bind-coupons: 领取{len(claimed)}张",
            )
        except McpClientError as e:
            await self.notifier.notify_error("自动领券失败", str(e))

    async def _check_lottery(self):
        """Check lottery activity status."""
        try:
            result = await self.client.call_tool_safe("query-lottery-info")
            if isinstance(result, dict) and "error" in result:
                return

            lottery_id = (
                result.get("activityId")
                or result.get("activity_id")
                or json.dumps(result, sort_keys=True)[:50]
                if isinstance(result, dict)
                else None
            )

            if lottery_id and lottery_id != self._cache.get("known_lottery"):
                self._cache["known_lottery"] = lottery_id
                status = result.get("status") or result.get("activityStatus", "unknown")
                prizes = result.get("prizes") or result.get("prizeList", [])
                prize_names = [
                    p.get("name") or p.get("prizeName", "?") for p in prizes[:5]
                ] if isinstance(prizes, list) else []

                msg = f"积分抽奖活动: {status}"
                if prize_names:
                    msg += f"\n奖品: {', '.join(prize_names)}"
                msg += "\n输入 'lottery' 查看详情或抽奖"

                await self.notifier.notify_deal("抽奖活动", msg)

        except Exception as e:
            await self.notifier.notify_error("抽奖检查失败", str(e))

    async def _check_my_account(self):
        """Check account for expiring points."""
        try:
            result = await self.client.call_tool_safe("query-my-account")
            if isinstance(result, dict) and "error" in result:
                return

            expiring = result.get("expiringPoints") or result.get("expiring_points", 0)
            available = result.get("availablePoints") or result.get("available_points", 0)

            if expiring and int(expiring) > 0:
                await self.notifier.notify(
                    "积分即将过期!",
                    f"您有 {expiring} 积分即将过期!\n"
                    f"当前可用积分: {available}\n"
                    f"建议尽快兑换商品或参与抽奖",
                    level="warning",
                )

        except Exception as e:
            await self.notifier.notify_error("积分检查失败", str(e))

    async def check_now(self):
        """Trigger an immediate check cycle."""
        await self._run_check_cycle()

    async def claim_all_coupons(self):
        """Manually trigger coupon claiming."""
        await self._auto_claim_coupons()

    @staticmethod
    def _extract_items(data: Any, key: str) -> list:
        """Extract a list of items from MCP response data."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if key in data:
                return data[key] if isinstance(data[key], list) else [data[key]]
            # Try common response wrappers
            for wrapper in ["data", "result", "items", "list"]:
                if wrapper in data:
                    inner = data[wrapper]
                    if isinstance(inner, list):
                        return inner
                    if isinstance(inner, dict) and key in inner:
                        return inner[key] if isinstance(inner[key], list) else [inner[key]]
        return []
