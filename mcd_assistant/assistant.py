"""Main assistant orchestrator - ties all modules together with CLI interface."""
import asyncio
import json
import sys
from typing import Optional

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.markdown import Markdown

from .config import Config
from .mcp_client import McpClient, McpClientError, McpAuthError
from .monitor import ActivityMonitor
from .recommender import Recommender
from .order_parser import OrderParser
from .notifier import Notifier
from .models import OrderSpec

console = Console()


class McAssistant:
    """McDonald's Personal Assistant - main orchestrator.

    Provides an interactive CLI for:
    - User dashboard (points, coupons, profile)
    - Smart meal recommendations
    - NL order parsing and building
    - Activity monitoring with notifications
    - Coupon auto-claiming
    - Lottery participation
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config.load()
        self.client = McpClient(self.config)
        self.notifier = Notifier(self.config.notification_method, self.config.data_dir)
        self.monitor = ActivityMonitor(self.client, self.config, self.notifier)
        self.recommender = Recommender(self.client)
        self.parser = OrderParser(self.client)
        self._running = False

    async def start(self):
        """Initialize and show welcome."""
        console.print(Panel(
            "[bold magenta]麦当劳个人助理[/bold magenta]\n"
            "[dim]MCP-powered smart ordering, monitoring & recommendations[/dim]\n\n"
            f"Server: {self.config.mcp_server_url}\n"
            f"Auto mode: {'ON' if self.config.auto_mode else 'OFF'}\n"
            f"Monitor interval: {self.config.monitor_interval_minutes} min",
            border_style="magenta",
            padding=(1, 2),
        ))

        # Test connection
        await self._test_connection()

        # Show initial dashboard
        await self.dashboard()

        # Start interactive loop
        await self._interactive_loop()

    async def _test_connection(self):
        """Test MCP server connection."""
        console.print("[dim]Connecting to McDonald's MCP Server...[/dim]")
        try:
            await self.client.connect()
            tools = await self.client.list_tools()
            console.print(
                f"[green]Connected![/green] {len(tools)} tools available.\n",
            )
        except McpAuthError:
            console.print("[red]Authentication failed![/red] Token may be invalid or expired.")
            console.print("[dim]Please check your MCP token in config.json[/dim]")
        except Exception as e:
            console.print(f"[red]Connection error:[/red] {e}")

    async def _interactive_loop(self):
        """Main interactive command loop."""
        self._running = True

        commands_help = """
[bold]Available commands:[/bold]
  [cyan]dashboard[/cyan]    - Show your profile, points, coupons overview
  [cyan]nearby[/cyan]       - Find nearby McDonald's restaurants
  [cyan]menu[/cyan]         - Show menu for a specific store
  [cyan]recommend[/cyan]    - Get smart meal recommendations
  [cyan]order[/cyan]        - Parse natural language order request
  [cyan]deals[/cyan]        - Check current deals and promotions
  [cyan]claim[/cyan]        - Claim all available coupons
  [cyan]lottery[/cyan]      - Check lottery info and draw
  [cyan]monitor[/cyan]      - Start/stop activity monitoring
  [cyan]history[/cyan]      - View order history
  [cyan]points[/cyan]       - Check points and redeem products
  [cyan]auto[/cyan]         - Toggle auto mode
  [cyan]help[/cyan]         - Show this help
  [cyan]quit[/cyan]         - Exit
"""
        console.print(commands_help)

        while self._running:
            try:
                cmd = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: Prompt.ask("[bold green]mcd>[/bold green]")
                )
                cmd = cmd.strip().lower()

                if not cmd:
                    continue

                handlers = {
                    "dashboard": self.dashboard,
                    "d": self.dashboard,
                    "nearby": self.cmd_nearby,
                    "menu": self.cmd_menu,
                    "recommend": self.cmd_recommend,
                    "rec": self.cmd_recommend,
                    "order": self.cmd_order,
                    "deals": self.cmd_deals,
                    "claim": self.cmd_claim,
                    "lottery": self.cmd_lottery,
                    "monitor": self.cmd_monitor,
                    "history": self.cmd_history,
                    "points": self.cmd_points,
                    "auto": self.cmd_auto,
                    "help": lambda: console.print(commands_help),
                    "h": lambda: console.print(commands_help),
                    "quit": self._quit,
                    "q": self._quit,
                    "exit": self._quit,
                }

                handler = handlers.get(cmd)
                if handler:
                    result = handler()
                    if asyncio.iscoroutine(result):
                        await result
                else:
                    console.print(f"[yellow]Unknown command: {cmd}[/yellow]. Type 'help' for commands.")

            except (KeyboardInterrupt, EOFError):
                await self._quit()
            except Exception as e:
                console.print(f"[red]Error:[/red] {e}")

    async def _quit(self):
        """Clean up and exit."""
        self._running = False
        if self.monitor.is_running:
            await self.monitor.stop()
        await self.client.disconnect()
        console.print("[dim]Goodbye! Thanks for using McDonald's Assistant.[/dim]")
        sys.exit(0)

    # ============================================================
    # Command Handlers
    # ============================================================

    async def dashboard(self):
        """Show user dashboard: profile, points, coupons, time."""
        console.print("\n[bold blue]Loading dashboard...[/bold blue]")

        account = await self.client.call_tool_safe("query-my-account")
        coupons = await self.client.call_tool_safe("query-my-coupons")
        time_info = await self.client.call_tool_safe("now-time-info")
        campaigns = await self.client.call_tool_safe("campaign-calendar")
        lottery = await self.client.call_tool_safe("query-lottery-info")

        # Profile section
        if isinstance(account, dict) and "error" not in account:
            avail = account.get("availablePoints") or account.get("available_points", 0)
            total = account.get("totalPoints") or account.get("total_points", 0)
            expiring = account.get("expiringPoints") or account.get("expiring_points", 0)

            content = f"[bold]Available Points:[/bold] {avail}\n"
            content += f"[bold]Total Points:[/bold] {total}\n"
            if expiring and int(expiring) > 0:
                content += f"[yellow bold]Expiring Soon:[/yellow bold] {expiring} points!\n"

            console.print(Panel(content, title="[bold]Account[/bold]", border_style="green"))
        else:
            console.print(Panel("[red]Failed to load account info[/red]", title="Account"))

        # Time
        if isinstance(time_info, dict) and "error" not in time_info:
            now = time_info.get("currentTime") or time_info.get("datetime") or str(time_info)
            console.print(Panel(f"[dim]{now}[/dim]", title="[bold]Current Time[/bold]", border_style="blue"))

        # Coupons
        if isinstance(coupons, dict) and "error" not in coupons:
            coupon_list = self._extract_list(coupons, "coupons")
            content = f"You have [bold]{len(coupon_list)}[/bold] coupons\n"
            for c in coupon_list[:5]:
                if isinstance(c, dict):
                    title = c.get("title") or c.get("name", "Unknown")
                    expiry = c.get("expiryDate") or c.get("expireDate") or c.get("endDate", "")
                    content += f"  - {title}" + (f" (expires: {expiry})" if expiry else "") + "\n"
            if len(coupon_list) > 5:
                content += f"  ... and {len(coupon_list) - 5} more\n"
            console.print(Panel(content, title="[bold]My Coupons[/bold]", border_style="yellow"))

        # Campaigns
        if isinstance(campaigns, dict) and "error" not in campaigns:
            camp_list = self._extract_list(campaigns, "campaigns")
            if camp_list:
                content = ""
                for camp in camp_list[:3]:
                    if isinstance(camp, dict):
                        title = camp.get("title") or camp.get("name", "Unknown")
                        desc = camp.get("description") or camp.get("detail", "")[:60]
                        content += f"[bold]{title}[/bold]\n{desc}\n\n"
                console.print(Panel(content, title="[bold]Current Campaigns[/bold]", border_style="magenta"))

        # Lottery
        if isinstance(lottery, dict) and "error" not in lottery:
            status = lottery.get("status") or lottery.get("activityStatus", "unknown")
            console.print(Panel(
                f"Lottery status: [bold]{status}[/bold]\nType 'lottery' for details",
                title="[bold]Lottery[/bold]",
                border_style="cyan",
            ))

        console.print()

    async def cmd_nearby(self):
        """Find nearby stores."""
        lat = self.config.default_location.lat
        lng = self.config.default_location.lng
        addr = self.config.default_location.address

        console.print(f"[dim]Searching near: {addr} ({lat}, {lng})[/dim]")

        result = await self.client.call_tool_safe("query-nearby-stores", {"lat": lat, "lng": lng})

        if isinstance(result, dict) and "error" not in result:
            stores = self._extract_list(result, "stores")
            if stores:
                table = Table(title="Nearby McDonald's Stores", show_header=True)
                table.add_column("#", style="dim")
                table.add_column("Name", style="bold")
                table.add_column("Address")
                table.add_column("Distance", style="cyan")
                table.add_column("Hours", style="dim")

                for i, store in enumerate(stores[:10]):
                    if isinstance(store, dict):
                        name = store.get("name") or store.get("storeName", "?")
                        address = store.get("address") or store.get("storeAddress", "")
                        dist = store.get("distance") or store.get("distanceDesc", "")
                        hours = store.get("businessHours") or store.get("openHours", "")
                        table.add_row(str(i + 1), name, address[:30], str(dist), str(hours))

                console.print(table)
                console.print(f"\n[dim]Use 'menu <store_id>' to view menu for a store[/dim]")
            else:
                console.print("[yellow]No stores found nearby.[/yellow]")
        else:
            console.print(f"[red]Error: {result}[/red]")

    async def cmd_menu(self, store_id: str = ""):
        """Show menu for a store."""
        if not store_id:
            # Get nearest store first
            result = await self.client.call_tool_safe(
                "query-nearby-stores",
                {"lat": self.config.default_location.lat, "lng": self.config.default_location.lng},
            )
            stores = self._extract_list(result, "stores")
            if stores and isinstance(stores[0], dict):
                store_id = (
                    stores[0].get("storeId")
                    or stores[0].get("store_id")
                    or stores[0].get("id", "")
                )
                name = stores[0].get("name") or stores[0].get("storeName", "?")
                console.print(f"[dim]Using nearest store: {name} ({store_id})[/dim]")
            else:
                console.print("[red]No nearby store found. Please specify store ID.[/red]")
                return

        menu = await self.client.call_tool_safe("query-meals", {"storeId": store_id})
        meals = self._extract_list(menu, "meals")

        if meals:
            table = Table(title=f"Menu ({len(meals)} items)", show_header=True)
            table.add_column("Code", style="dim")
            table.add_column("Name", style="bold")
            table.add_column("Category")
            table.add_column("Price", style="green", justify="right")
            table.add_column("Tags", style="cyan")

            for meal in meals[:20]:
                if isinstance(meal, dict):
                    code = meal.get("productCode") or meal.get("product_code") or meal.get("code", "")
                    name = meal.get("name") or meal.get("productName", "?")
                    cat = meal.get("category") or meal.get("categoryName", "")
                    price = meal.get("price") or meal.get("salePrice", "")
                    tags = ", ".join(meal.get("tags") or meal.get("labels", []))
                    table.add_row(str(code), name, str(cat), f"¥{price}", tags)

            console.print(table)
            if len(meals) > 20:
                console.print(f"[dim]... and {len(meals) - 20} more items[/dim]")
        else:
            console.print("[yellow]No menu items found.[/yellow]")

    async def cmd_recommend(self):
        """Smart recommendation."""
        console.print(Panel(
            "[bold]Smart Recommendation Engine[/bold]\n"
            "Tell me what you need and I'll find the best combination.",
            border_style="magenta",
        ))

        request = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: Prompt.ask(
                "[green]Describe your need[/green]\n"
                "[dim](e.g., '3人午餐预算150', '最便宜的巨无霸套餐', '5人团餐素食2份')[/dim]"
            ),
        )

        if not request:
            return

        # Parse the request
        spec = self.parser.parse(request)

        # Get nearest store
        store_result = await self.client.call_tool_safe(
            "query-nearby-stores",
            {"lat": self.config.default_location.lat, "lng": self.config.default_location.lng},
        )
        stores = self._extract_list(store_result, "stores")
        store_id = ""
        if stores and isinstance(stores[0], dict):
            store_id = (
                stores[0].get("storeId")
                or stores[0].get("store_id")
                or stores[0].get("id", "")
            )

        if not store_id:
            console.print("[red]No nearby store found for recommendation.[/red]")
            return

        # Generate recommendation
        if spec.budget and spec.total_people:
            rec = await self.recommender.recommend_by_budget(
                store_id, spec.budget, spec.total_people, spec.meal_type or "lunch"
            )
        elif spec.constraints:
            rec = await self.recommender.recommend_group_meal(
                store_id, spec.total_people, spec.budget, spec.constraints
            )
        elif spec.items:
            rec = await self.recommender.recommend_cheapest_combo(
                store_id, [i.name for i in spec.items]
            )
        else:
            # Default: best value combo
            rec = await self.recommender.recommend_by_budget(
                store_id, 100.0, spec.total_people, spec.meal_type or "lunch"
            )

        console.print(rec.summary())

        # Ask if user wants to order
        if rec.items:
            order = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: Prompt.ask(
                    "\n[green]Create order with this recommendation?[/green]",
                    choices=["y", "n"],
                    default="n",
                ),
            )
            if order == "y":
                await self._create_order_from_items(store_id, rec.items, spec.dining_mode)

    async def cmd_order(self):
        """Parse NL and build order."""
        console.print(Panel(
            "[bold]Natural Language Order Builder[/bold]\n"
            "Describe what you need and I'll parse it into an order.",
            border_style="cyan",
        ))
        console.print("[dim]Examples:[/dim]")
        console.print("[dim]  - 我今天中午要组织一个workshop，要准备10份饭，其中素食2份，汉堡加量4份[/dim]")
        console.print("[dim]  - 5个人的午餐，预算200，要鸡翅和可乐[/dim]")
        console.print("[dim]  - 帮我点一个巨无霸套餐加可乐[/dim]\n")

        request = await asyncio.get_event_loop().run_in_executor(
            None, lambda: Prompt.ask("[green]Your order request[/green]")
        )

        if not request:
            return

        # Get nearest store
        store_result = await self.client.call_tool_safe(
            "query-nearby-stores",
            {"lat": self.config.default_location.lat, "lng": self.config.default_location.lng},
        )
        stores = self._extract_list(store_result, "stores")
        store_id = ""
        if stores and isinstance(stores[0], dict):
            store_id = (
                stores[0].get("storeId")
                or stores[0].get("store_id")
                or stores[0].get("id", "")
            )

        # Parse and match
        spec = await self.parser.parse_and_match(request, store_id)
        console.print(spec.summary())

        if not spec.items:
            console.print("\n[yellow]No specific items detected. Try 'recommend' for suggestions.[/yellow]")
            return

        # Calculate price
        if store_id and spec.items:
            price_result = await self.client.call_tool_safe(
                "calculate-price",
                {
                    "items": [
                        {"productCode": i.product_code, "quantity": i.quantity}
                        for i in spec.items
                        if i.product_code
                    ],
                    "storeId": store_id,
                },
            )
            if isinstance(price_result, dict) and "error" not in price_result:
                total = price_result.get("totalPrice") or price_result.get("total_price", 0)
                discount = price_result.get("discountAmount") or price_result.get("discount", 0)
                console.print(f"\n[bold green]Price estimate: ¥{total}[/bold green]")
                if discount:
                    console.print(f"[green]Discount applied: -¥{discount}[/green]")

        # Ask to create order
        create = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: Prompt.ask(
                "\n[green]Create this order?[/green]",
                choices=["y", "n"],
                default="n",
            ),
        )
        if create == "y":
            await self._create_order_from_items(store_id, spec.items, spec.dining_mode)

    async def cmd_deals(self):
        """Check current deals."""
        console.print("\n[bold blue]Checking current deals...[/bold blue]")

        deals = await self.client.get_all_available_deals()

        # Campaigns
        campaigns = deals.get("campaigns", {})
        if isinstance(campaigns, dict) and "error" not in campaigns:
            camp_list = self._extract_list(campaigns, "campaigns")
            if camp_list:
                console.print(Panel(
                    "\n".join(
                        f"- {c.get('title', c.get('name', '?'))}" for c in camp_list[:5]
                        if isinstance(c, dict)
                    ),
                    title="[bold magenta]Campaigns[/bold magenta]",
                    border_style="magenta",
                ))

        # Available coupons
        avail = deals.get("available_coupons", {})
        if isinstance(avail, dict) and "error" not in avail:
            coupon_list = self._extract_list(avail, "coupons")
            if coupon_list:
                console.print(Panel(
                    f"Found [bold]{len(coupon_list)}[/bold] claimable coupons!\n"
                    + "\n".join(
                        f"- {c.get('title', c.get('name', '?'))}" for c in coupon_list[:5]
                        if isinstance(c, dict)
                    ),
                    title="[bold yellow]Available Coupons (麦麦省)[/bold yellow]",
                    border_style="yellow",
                ))
                console.print("[dim]Type 'claim' to auto-claim all coupons[/dim]")

        # My coupons
        my_coupons = deals.get("my_coupons", {})
        if isinstance(my_coupons, dict) and "error" not in my_coupons:
            my_list = self._extract_list(my_coupons, "coupons")
            console.print(f"[dim]You currently have {len(my_list)} coupons[/dim]")

        # Lottery
        lottery = deals.get("lottery", {})
        if isinstance(lottery, dict) and "error" not in lottery:
            status = lottery.get("status") or lottery.get("activityStatus", "")
            console.print(f"[dim]Lottery status: {status}[/dim]")

        # Account
        account = deals.get("account", {})
        if isinstance(account, dict) and "error" not in account:
            avail_pts = account.get("availablePoints") or account.get("available_points", 0)
            console.print(f"[dim]Available points: {avail_pts}[/dim]")

    async def cmd_claim(self):
        """Claim all available coupons."""
        console.print("[bold]Claiming all available coupons...[/bold]")
        result = await self.client.call_tool_safe("auto-bind-coupons")

        if isinstance(result, dict) and "error" not in result:
            coupons = self._extract_list(result, "coupons")
            if coupons:
                console.print(f"[green]Successfully claimed {len(coupons)} coupons![/green]")
                for c in coupons[:5]:
                    if isinstance(c, dict):
                        title = c.get("title") or c.get("name", "?")
                        console.print(f"  - {title}")
            else:
                console.print("[yellow]No new coupons to claim.[/yellow]")
        else:
            console.print(f"[red]Claim failed: {result}[/red]")

    async def cmd_lottery(self):
        """Lottery info and draw."""
        info = await self.client.call_tool_safe("query-lottery-info")

        if isinstance(info, dict) and "error" not in info:
            status = info.get("status") or info.get("activityStatus", "")
            prizes = info.get("prizes") or info.get("prizeList", [])
            cost = info.get("cost") or info.get("consumePoints", "?")

            content = f"Status: [bold]{status}[/bold]\n"
            content += f"Cost per draw: [bold]{cost}[/bold] points\n"
            if prizes:
                content += "\n[bold]Prizes:[/bold]\n"
                for p in prizes[:5]:
                    if isinstance(p, dict):
                        name = p.get("name") or p.get("prizeName", "?")
                        content += f"  - {name}\n"

            console.print(Panel(content, title="[bold]Lottery[/bold]", border_style="cyan"))

            draw = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: Prompt.ask(
                    "[green]Draw lottery?[/green]",
                    choices=["y", "n"],
                    default="n",
                ),
            )
            if draw == "y":
                result = await self.client.call_tool_safe("draw-lottery")
                if isinstance(result, dict) and "error" not in result:
                    win = result.get("isWin") or result.get("win", False)
                    prize = result.get("prize") or result.get("prizeName", "")
                    if win:
                        console.print(f"[bold magenta]You won: {prize}![/bold magenta]")
                    else:
                        console.print("[dim]Better luck next time![/dim]")
                else:
                    console.print(f"[red]Draw failed: {result}[/red]")
        else:
            console.print(f"[red]No lottery activity available: {info}[/red]")

    async def cmd_monitor(self):
        """Start/stop monitoring."""
        if self.monitor.is_running:
            await self.monitor.stop()
        else:
            await self.monitor.start()

    async def cmd_history(self):
        """View order history."""
        result = await self.client.call_tool_safe("order-list")

        if isinstance(result, dict) and "error" not in result:
            orders = self._extract_list(result, "orders")
            if orders:
                table = Table(title="Recent Orders", show_header=True)
                table.add_column("Order ID", style="dim")
                table.add_column("Status")
                table.add_column("Items")
                table.add_column("Total", style="green", justify="right")
                table.add_column("Date", style="dim")

                for order in orders[:10]:
                    if isinstance(order, dict):
                        oid = order.get("orderId") or order.get("id", "?")
                        status = order.get("status") or order.get("orderStatus", "?")
                        items = order.get("items") or order.get("products", [])
                        item_count = len(items) if isinstance(items, list) else 0
                        total = order.get("totalPrice") or order.get("amount", 0)
                        date = order.get("createTime") or order.get("orderDate", "")
                        table.add_row(str(oid), str(status), f"{item_count} items", f"¥{total}", str(date)[:19])

                console.print(table)
            else:
                console.print("[yellow]No recent orders found.[/yellow]")
        else:
            console.print(f"[red]Error: {result}[/red]")

    async def cmd_points(self):
        """Points and mall products."""
        account = await self.client.call_tool_safe("query-my-account")
        products = await self.client.call_tool_safe("mall-points-products")

        if isinstance(account, dict) and "error" not in account:
            avail = account.get("availablePoints") or account.get("available_points", 0)
            console.print(Panel(
                f"[bold]Available Points: {avail}[/bold]",
                title="[bold green]Points[/bold green]",
                border_style="green",
            ))

        if isinstance(products, dict) and "error" not in products:
            prod_list = self._extract_list(products, "products")
            if prod_list:
                table = Table(title="Mall Products (Points Redeemable)", show_header=True)
                table.add_column("ID", style="dim")
                table.add_column("Name", style="bold")
                table.add_column("Points", style="cyan", justify="right")
                table.add_column("Cash", style="green", justify="right")

                for p in prod_list[:10]:
                    if isinstance(p, dict):
                        pid = p.get("productId") or p.get("id", "")
                        name = p.get("name") or p.get("productName", "?")
                        pts = p.get("points") or p.get("needPoints", 0)
                        cash = p.get("price") or p.get("cashPrice", "")
                        table.add_row(str(pid), name, str(pts), f"¥{cash}" if cash else "-")

                console.print(table)

    async def cmd_auto(self):
        """Toggle auto mode."""
        self.config.auto_mode = not self.config.auto_mode
        self.config.save()
        console.print(
            f"[bold]Auto mode: {'ON' if self.config.auto_mode else 'OFF'}[/bold]\n"
            f"[dim]When ON, coupons are auto-claimed and lottery is auto-drawn[/dim]"
        )

    # ============================================================
    # Helpers
    # ============================================================

    async def _create_order_from_items(
        self, store_id: str, items: list, dining_mode: str = ""
    ):
        """Create an order from a list of MealItems."""
        order_items = []
        for item in items:
            if item.product_code:
                order_items.append({
                    "productCode": item.product_code,
                    "quantity": item.quantity,
                })

        if not order_items:
            console.print("[red]No valid items to order (missing product codes)[/red]")
            return

        mode = dining_mode or "takeaway"

        console.print("[dim]Creating order...[/dim]")
        result = await self.client.call_tool_safe(
            "create-order",
            {
                "storeId": store_id,
                "diningMode": mode,
                "items": order_items,
            },
        )

        if isinstance(result, dict) and "error" not in result:
            order_id = result.get("orderId") or result.get("id", "?")
            pay_url = result.get("payUrl") or result.get("paymentUrl") or result.get("payLink", "")
            total = result.get("totalPrice") or result.get("amount", "?")

            content = f"[bold green]Order created![/bold green]\n"
            content += f"Order ID: {order_id}\n"
            content += f"Total: ¥{total}\n"
            if pay_url:
                content += f"\n[cyan]Payment URL:[/cyan] {pay_url}"

            console.print(Panel(content, title="[bold]Order Created[/bold]", border_style="green"))
        else:
            console.print(f"[red]Order creation failed: {result}[/red]")

    @staticmethod
    def _extract_list(data: dict, key: str) -> list:
        """Extract a list from response data."""
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            if key in data and isinstance(data[key], list):
                return data[key]
            for wrapper in ["data", "result", "items", "list"]:
                if wrapper in data:
                    inner = data[wrapper]
                    if isinstance(inner, list):
                        return inner
                    if isinstance(inner, dict) and key in inner:
                        return inner[key] if isinstance(inner[key], list) else []
        return []
