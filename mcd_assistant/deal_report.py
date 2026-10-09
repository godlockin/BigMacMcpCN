"""Deal Report Generator - creates visual HTML reports for composed deals.

Generates a self-contained HTML file with:
- Original NL request
- Parsed order spec
- Deal composition (combos + singles + points + coupons)
- Price breakdown with visual bars
- Constraint satisfaction matrix
- Gap analysis
- Confidence score
"""
import os
from datetime import datetime
from typing import Optional

from .deal_composer import Deal, DealComponent


class DealReportGenerator:
    """Generate visual HTML reports for composed deals."""

    def __init__(self, output_dir: str = "."):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

    def generate(self, deal: Deal, nl_request: str = "") -> str:
        """Generate HTML report and return file path."""
        html = self._build_html(deal, nl_request)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"deal_report_{timestamp}.html"
        filepath = os.path.join(self.output_dir, filename)

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(html)

        return filepath

    def _build_html(self, deal: Deal, nl_request: str) -> str:
        """Build the complete HTML report."""
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        confidence_color = self._confidence_color(deal.confidence)
        confidence_label = self._confidence_label(deal.confidence)

        # Build components table
        combo_rows = self._build_component_rows(deal.combo_items, "套餐")
        single_rows = self._build_component_rows(deal.single_items, "单点")
        points_rows = self._build_component_rows(deal.points_items, "积分兑换")

        # Build constraint matrix
        constraint_html = self._build_constraint_matrix(deal)

        # Build gaps section
        gaps_html = self._build_gaps(deal)

        # Build price breakdown bars
        savings_total = deal.coupon_savings + deal.points_savings + deal.combo_savings
        savings_pct = (savings_total / deal.original_total * 100) if deal.original_total > 0 else 0

        spec = deal.order_spec
        spec_html = ""
        if spec:
            spec_html = f"""
            <div class="spec-grid">
                <div class="spec-item">
                    <span class="spec-label">人数</span>
                    <span class="spec-value">{spec.total_people}</span>
                </div>
                <div class="spec-item">
                    <span class="spec-label">预算</span>
                    <span class="spec-value">¥{spec.budget or '未指定'}</span>
                </div>
                <div class="spec-item">
                    <span class="spec-label">餐次</span>
                    <span class="spec-value">{spec.meal_type or '不限'}</span>
                </div>
                <div class="spec-item">
                    <span class="spec-label">就餐方式</span>
                    <span class="spec-value">{spec.dining_mode or '默认'}</span>
                </div>
            </div>
            <div class="constraints">
                <span class="section-subtitle">特殊要求</span>
                <div class="constraint-tags">
                    {''.join(f'<span class="tag">{c}</span>' for c in spec.constraints) if spec.constraints else '<span class="tag tag-neutral">无</span>'}
                </div>
            </div>
            <div class="constraints">
                <span class="section-subtitle">备注</span>
                <span class="notes-text">{spec.notes or '无'}</span>
            </div>
            """

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>麦当劳 Deal 方案报告</title>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; background: #f5f5f7; color: #1d1d1f; line-height: 1.6; }}
.container {{ max-width: 800px; margin: 0 auto; padding: 20px; }}
.header {{ background: linear-gradient(135deg, #ffbc0d 0%, #ff9500 100%); color: #fff; padding: 30px; border-radius: 16px; margin-bottom: 20px; }}
.header h1 {{ font-size: 28px; font-weight: 700; margin-bottom: 8px; }}
.header .subtitle {{ font-size: 14px; opacity: 0.9; }}
.header .meta {{ font-size: 12px; opacity: 0.7; margin-top: 8px; }}
.card {{ background: #fff; border-radius: 12px; padding: 20px; margin-bottom: 16px; box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
.card-title {{ font-size: 18px; font-weight: 600; margin-bottom: 12px; color: #1d1d1f; }}
.section-subtitle {{ font-size: 13px; color: #86868b; font-weight: 500; display: block; margin-bottom: 6px; }}
.nl-request {{ background: #f5f5f7; border-left: 4px solid #ff9500; padding: 12px 16px; border-radius: 0 8px 8px 0; font-size: 15px; margin-bottom: 16px; }}
.spec-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }}
.spec-item {{ text-align: center; padding: 10px; background: #f5f5f7; border-radius: 8px; }}
.spec-label {{ font-size: 11px; color: #86868b; display: block; }}
.spec-value {{ font-size: 18px; font-weight: 600; color: #1d1d1f; }}
.constraints {{ margin-bottom: 12px; }}
.constraint-tags {{ display: flex; flex-wrap: wrap; gap: 6px; }}
.tag {{ background: #e8f5e9; color: #2e7d32; padding: 4px 10px; border-radius: 12px; font-size: 12px; font-weight: 500; }}
.tag-neutral {{ background: #f5f5f7; color: #86868b; }}
.notes-text {{ font-size: 13px; color: #555; }}
.confidence-badge {{ display: inline-flex; align-items: center; gap: 6px; padding: 6px 14px; border-radius: 20px; font-size: 14px; font-weight: 600; background: {confidence_color}; color: #fff; }}
.confidence-badge .dot {{ width: 8px; height: 8px; border-radius: 50%; background: #fff; }}
table {{ width: 100%; border-collapse: collapse; }}
th {{ text-align: left; padding: 8px 12px; font-size: 12px; color: #86868b; border-bottom: 2px solid #f0f0f0; }}
td {{ padding: 10px 12px; font-size: 14px; border-bottom: 1px solid #f0f0f0; }}
.type-badge {{ padding: 2px 8px; border-radius: 10px; font-size: 11px; font-weight: 600; }}
.type-combo {{ background: #e3f2fd; color: #1565c0; }}
.type-single {{ background: #fff3e0; color: #e65100; }}
.type-points {{ background: #f3e5f5; color: #7b1fa2; }}
.type-coupon {{ background: #e8f5e9; color: #2e7d32; }}
.coupon-cell {{ color: #2e7d32; font-weight: 500; }}
.free-cell {{ color: #2e7d32; font-weight: 600; }}
.price-cell {{ text-align: right; font-weight: 500; }}
.gap-item {{ background: #fff3cd; border: 1px solid #ffc107; padding: 8px 12px; border-radius: 8px; margin-bottom: 6px; font-size: 13px; display: flex; align-items: center; gap: 8px; }}
.gap-icon {{ color: #f57c00; }}
.price-summary {{ display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #f0f0f0; }}
.price-summary.total {{ border-bottom: none; border-top: 2px solid #1d1d1f; padding-top: 12px; margin-top: 4px; }}
.price-label {{ font-size: 14px; color: #555; }}
.price-value {{ font-size: 16px; font-weight: 600; }}
.price-value.savings {{ color: #2e7d32; }}
.price-value.final {{ font-size: 24px; color: #1d1d1f; }}
.bar-container {{ height: 8px; background: #f0f0f0; border-radius: 4px; overflow: hidden; margin-top: 4px; }}
.bar-fill {{ height: 100%; border-radius: 4px; }}
.bar-coupon {{ background: #4caf50; }}
.bar-points {{ background: #9c27b0; }}
.bar-combo {{ background: #2196f3; }}
.satisfaction-row {{ display: flex; align-items: center; gap: 12px; padding: 8px 0; border-bottom: 1px solid #f0f0f0; }}
.satisfaction-label {{ font-size: 14px; flex: 1; }}
.satisfaction-value {{ font-size: 14px; font-weight: 600; }}
.satisfied {{ color: #2e7d32; }}
.unsatisfied {{ color: #d32f2f; }}
.reasoning {{ background: #f5f5f7; padding: 12px 16px; border-radius: 8px; font-size: 13px; color: #555; line-height: 1.8; }}
.footer {{ text-align: center; padding: 20px; font-size: 12px; color: #86868b; }}
.action-bar {{ display: flex; gap: 12px; margin-top: 16px; }}
.btn {{ padding: 10px 24px; border-radius: 8px; font-size: 14px; font-weight: 600; cursor: pointer; border: none; }}
.btn-primary {{ background: #ff9500; color: #fff; }}
.btn-secondary {{ background: #f5f5f7; color: #1d1d1f; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>麦当劳 Deal 方案报告</h1>
        <div class="subtitle">{deal.title} | {deal.description}</div>
        <div class="meta">生成时间: {now} | 门店: {deal.store_name or deal.store_id or '自动选择'}</div>
    </div>

    <div class="card">
        <div class="card-title">需求输入</div>
        <div class="nl-request">"{nl_request}"</div>
        {spec_html}
    </div>

    <div class="card">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
            <div class="card-title">方案评估</div>
            <div class="confidence-badge">
                <span class="dot"></span>
                {confidence_label} {deal.confidence:.0%}
            </div>
        </div>
        <div class="reasoning">{deal.reasoning}</div>
    </div>

    <div class="card">
        <div class="card-title">Deal 组成</div>
        <table>
            <thead>
                <tr>
                    <th>类型</th>
                    <th>餐品</th>
                    <th>数量</th>
                    <th>优惠券</th>
                    <th>积分</th>
                    <th style="text-align:right;">金额</th>
                </tr>
            </thead>
            <tbody>
                {combo_rows}
                {single_rows}
                {points_rows}
            </tbody>
        </table>
    </div>

    {gaps_html}

    <div class="card">
        <div class="card-title">价格分解</div>
        <div class="price-summary">
            <span class="price-label">原价合计</span>
            <span class="price-value">¥{deal.original_total:.2f}</span>
        </div>
        <div class="price-summary">
            <span class="price-label">优惠券节省</span>
            <span class="price-value savings">-¥{deal.coupon_savings:.2f}</span>
        </div>
        <div class="bar-container">
            <div class="bar-fill bar-coupon" style="width: {savings_pct:.0f}%"></div>
        </div>
        <div class="price-summary">
            <span class="price-label">积分兑换价值</span>
            <span class="price-value savings">-¥{deal.points_savings:.2f}</span>
        </div>
        <div class="bar-container">
            <div class="bar-fill bar-points" style="width: {savings_pct:.0f}%"></div>
        </div>
        <div class="price-summary total">
            <span class="price-label" style="font-weight:600;">应付总价 ({spec.total_people if spec else 1}人)</span>
            <span class="price-value final">¥{deal.final_price:.2f}</span>
        </div>
        <div class="price-summary">
            <span class="price-label">人均</span>
            <span class="price-value">¥{deal.per_person_price:.2f}</span>
        </div>
        <div class="action-bar">
            <button class="btn btn-primary" onclick="alert('创建订单功能将调用 create-order MCP 工具')">确认下单</button>
            <button class="btn btn-secondary" onclick="alert('方案已保存，可调整后重新生成')">调整方案</button>
        </div>
    </div>

    <div class="card">
        <div class="card-title">约束满足度</div>
        {constraint_html}
    </div>

    <div class="footer">
        麦当劳个人助理 | MCP-powered Deal Composer | {now}
    </div>
</div>
</body>
</html>"""

    def _build_component_rows(self, components: list, type_label: str) -> str:
        """Build HTML table rows for a list of components."""
        if not components:
            return ""

        type_class = {
            "套餐": "type-combo",
            "单点": "type-single",
            "积分兑换": "type-points",
        }.get(type_label, "type-single")

        rows = []
        for c in components:
            coupon_cell = f'<span class="coupon-cell">{c.coupon_applied}</span>' if c.coupon_applied else '<span style="color:#ccc;">-</span>'
            points_cell = f'<span class="free-cell">{c.points_used} pts</span>' if c.points_used > 0 else '<span style="color:#ccc;">-</span>'
            price_cell = '<span class="free-cell">积分兑换</span>' if c.is_free else f'¥{c.total_price:.2f}'

            rows.append(f"""
            <tr>
                <td><span class="type-badge {type_class}">{type_label}</span></td>
                <td>{c.item_name}</td>
                <td>{c.quantity}</td>
                <td>{coupon_cell}</td>
                <td>{points_cell}</td>
                <td class="price-cell">{price_cell}</td>
            </tr>""")

        return "".join(rows)

    def _build_constraint_matrix(self, deal: Deal) -> str:
        """Build constraint satisfaction matrix HTML."""
        if not deal.constraint_satisfaction:
            return '<div style="color:#86868b;font-size:14px;">无约束要求</div>'

        rows = []
        for key, value in deal.constraint_satisfaction.items():
            if isinstance(value, str) and "/" in value:
                satisfied, required = value.split("/")
                is_satisfied = int(satisfied) >= int(required)
                cls = "satisfied" if is_satisfied else "unsatisfied"
                icon = "✓" if is_satisfied else "✗"
            else:
                cls = ""
                icon = ""
                satisfied = str(value)
                required = ""

            rows.append(f"""
            <div class="satisfaction-row">
                <span class="satisfaction-label">{key}</span>
                <span class="satisfaction-value {cls}">{icon} {value}</span>
            </div>""")

        return "".join(rows)

    def _build_gaps(self, deal: Deal) -> str:
        """Build gaps section HTML."""
        if not deal.gaps:
            return ""

        gap_items = []
        for g in deal.gaps:
            gap_items.append(f"""
            <div class="gap-item">
                <span class="gap-icon">!</span>
                <span>{g}</span>
            </div>""")

        return f"""
        <div class="card">
            <div class="card-title">缺口分析</div>
            {''.join(gap_items)}
            <div style="margin-top:12px;font-size:13px;color:#86868b;">
                以上缺口需要在 review 后调整方案或放宽约束条件。
            </div>
        </div>"""

    @staticmethod
    def _confidence_color(confidence: float) -> str:
        if confidence >= 0.8:
            return "#2e7d32"
        elif confidence >= 0.5:
            return "#f57c00"
        else:
            return "#d32f2f"

    @staticmethod
    def _confidence_label(confidence: float) -> str:
        if confidence >= 0.8:
            return "高规则通过率"
        elif confidence >= 0.5:
            return "部分匹配"
        else:
            return "低规则通过率"
