"""Bounded, deterministic pre-order meal replanning. No network or writes.

Prices are integer cents from an explicit snapshot, never an official cart quote.
The objective is lexicographic: affected people, changed item units, cash, points.
"""
from collections import Counter
from dataclasses import dataclass
from typing import Mapping


class InputError(ValueError):
    """Invalid or ambiguous decision input."""


def object_value(value: object, label: str) -> dict:
    if not isinstance(value, dict) or not all(isinstance(k, str) for k in value):
        raise InputError(f"{label} must be an object with string keys")
    return value


def integer(value: object, label: str, minimum: int = 0, maximum: int = 10_000_000) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise InputError(f"{label} must be an integer in [{minimum}, {maximum}]")
    return value


def text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise InputError(f"{label} must be a nonempty string (max 200 characters)")
    return value


def strings(value: object, label: str, allow_empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > 64:
        raise InputError(f"{label} must be an array (max 64)")
    result = tuple(text(v, label) for v in value)
    if not allow_empty and not result:
        raise InputError(f"{label} cannot be empty")
    return result


@dataclass(frozen=True)
class Option:
    id: str
    name: str
    price: int
    items: tuple[str, ...]
    tags: frozenset[str]
    points: int
    resources: tuple[str, ...]


@dataclass(frozen=True)
class Person:
    id: str
    name: str
    choices: tuple[str, ...]
    required_tags: frozenset[str]


@dataclass(frozen=True)
class Previous:
    option_id: str
    name: str
    items: tuple[str, ...]
    price: int
    points: int


def item_distance(before: tuple[str, ...], after: tuple[str, ...]) -> int:
    a, b = Counter(before), Counter(after)
    return sum((a - b).values()) + sum((b - a).values())


def solve(payload: object) -> dict:
    """Solve all remaining constraints, also finding a budget floor.

    No automatic lock/diet relaxation. Search exhaustion proves results only in
    supplied candidate space. A capped search never reports proven infeasibility.
    """
    request = object_value(payload, "request")
    snapshot = object_value(request.get("snapshot"), "snapshot")
    source = text(snapshot.get("source"), "snapshot.source")
    if source not in ("synthetic", "user_snapshot"):
        raise InputError("snapshot.source must be synthetic or user_snapshot")
    captured_at = text(snapshot.get("captured_at"), "snapshot.captured_at")
    store_id = text(snapshot.get("store_id"), "snapshot.store_id")
    budget = integer(request.get("budget_cents"), "budget_cents")
    point_budget = integer(snapshot.get("points_budget", 0), "points_budget")
    limit = integer(request.get("max_nodes", 200_000), "max_nodes", 1, 500_000)
    raw_options = snapshot.get("options")
    if not isinstance(raw_options, list) or not 1 <= len(raw_options) <= 64:
        raise InputError("snapshot.options requires 1..64 complete meal candidates")
    options: dict[str, Option] = {}
    resources = {k: integer(v, f"resource {k}", maximum=1000)
                 for k, v in object_value(snapshot.get("resources", {}), "resources").items()}
    for raw in raw_options:
        row = object_value(raw, "option")
        oid = text(row.get("id"), "option.id")
        if oid in options:
            raise InputError(f"duplicate option: {oid}")
        option = Option(oid, text(row.get("name"), "option.name"),
                        integer(row.get("price_cents"), "price_cents"),
                        strings(row.get("items"), "option.items", False),
                        frozenset(strings(row.get("verified_tags", []), "verified_tags")),
                        integer(row.get("points", 0), "option.points"),
                        strings(row.get("resources", []), "option.resources"))
        if any(r not in resources for r in option.resources):
            raise InputError(f"unknown resource in {oid}")
        options[oid] = option
    raw_people = request.get("participants")
    if not isinstance(raw_people, list) or not 1 <= len(raw_people) <= 16:
        raise InputError("participants requires 1..16 people")
    people: list[Person] = []
    for raw in raw_people:
        row = object_value(raw, "participant")
        pid = text(row.get("id"), "participant.id")
        if any(p.id == pid for p in people):
            raise InputError(f"duplicate participant: {pid}")
        choices = strings(row.get("allowed_options"), "allowed_options", False)
        if len(set(choices)) != len(choices) or any(c not in options for c in choices):
            raise InputError(f"duplicate or unknown choice for {pid}")
        people.append(Person(pid, text(row.get("name"), "participant.name"), choices,
                             frozenset(strings(row.get("required_tags", []), "required_tags"))))
    previous: dict[str, Previous] = {}
    raw_previous = request.get("previous_assignments", [])
    if not isinstance(raw_previous, list) or len(raw_previous) > 16:
        raise InputError("previous_assignments must be an array (max 16)")
    for raw in raw_previous:
        row = object_value(raw, "previous assignment")
        pid = text(row.get("person_id"), "previous person_id")
        if pid in previous:
            raise InputError(f"duplicate previous participant: {pid}")
        previous[pid] = Previous(text(row.get("option_id"), "previous option_id"),
                                 text(row.get("option_name"), "previous option_name"),
                                 strings(row.get("items"), "previous items", False),
                                 integer(row.get("price_cents"), "previous price"),
                                 integer(row.get("points", 0), "previous points"))
    locked = frozenset(strings(request.get("locked_people", []), "locked_people"))
    current_ids = {p.id for p in people}
    if not locked <= (previous.keys() & current_ids):
        raise InputError("locked_people must exist in both previous and current plans")
    pools: dict[str, list[Option]] = {}
    conflicts: list[dict] = []
    for person in people:
        choices = [options[c] for c in person.choices
                   if person.required_tags <= options[c].tags]
        if person.id in locked:
            choices = [o for o in choices if not item_distance(previous[person.id].items, o.items)]
        if not choices:
            conflicts.append({"person_id": person.id, "name": person.name,
                              "reason": "锁定餐品或已确认饮食属性与当前候选冲突；未自动放宽。"})
        def choice_key(o: Option) -> tuple:
            old = previous.get(person.id)
            d = item_distance(old.items, o.items) if old else 0
            return (int(d > 0), d, o.price, o.points, o.id)
        pools[person.id] = sorted(choices, key=choice_key)
    base = {"schema_version": 1, "scope": "supplied_complete_meal_candidates",
            "price_status": "snapshot_estimate_requires_official_cart_quote",
            "source": source, "captured_at": captured_at, "store_id": store_id,
            "budget_cents": budget, "previous_total_cents": sum(p.price for p in previous.values()),
            "departed_people": sorted(previous.keys() - current_ids),
            "conflicts": conflicts, "assignments": [], "changes": [],
            "objective_order": ["affected_people", "changed_item_units", "cash_cents", "points"],
            "warnings": ["演示数据，非真实报价。" if source == "synthetic" else
                         "人工提供的数据快照；标签、库存和优惠规则需由提供者核实。",
                         "候选须是一人份完整餐；不推断套餐结构、优惠叠加或营养属性。",
                         "不下单、不领券、不兑换；所有金额仍需官方整单核价。"]}
    if conflicts:
        return {**base, "status": "infeasible", "search_complete": True,
                "optimal_within_snapshot": False, "nodes": 0, "minimum_budget_cents": None}
    ordered = sorted(people, key=lambda p: (len(pools[p.id]), p.id))
    # Lower bounds preserve exactness and avoid exploring dominated subtrees.
    minimum_prices = [min(o.price for o in pools[p.id]) for p in ordered]
    suffix_price = [0] * (len(ordered) + 1)
    for i in range(len(ordered) - 1, -1, -1):
        suffix_price[i] = suffix_price[i + 1] + minimum_prices[i]
    nodes = 0
    capped = False
    best_key: tuple | None = None
    best: dict[str, Option] | None = None
    cheapest: int | None = None
    frontier: dict[int, tuple[tuple, dict[str, Option]]] = {}

    def visit(index: int, chosen: Mapping[str, Option], used: Counter, cash: int,
              points: int, affected: int, distance: int) -> None:
        nonlocal nodes, capped, best_key, best, cheapest
        if nodes >= limit:
            capped = True
            return
        nodes += 1
        if index == len(ordered):
            cheapest = cash if cheapest is None else min(cheapest, cash)
            key = (affected, distance, cash, points, tuple(chosen[p.id].id for p in people))
            if cash <= budget and (best_key is None or key < best_key):
                best_key, best = key, dict(chosen)
            if affected not in frontier or (cash, distance, points) < frontier[affected][0]:
                frontier[affected] = ((cash, distance, points), dict(chosen))
            return
        # Keep exploring until the minimum cost for each affected-person count
        # is known: this also provides exact budget/impact breakpoints.
        if cheapest is not None and cash + suffix_price[index] > budget:
            if cash + suffix_price[index] > cheapest and all(
                    a in frontier and frontier[a][0][0] < cash + suffix_price[index]
                    for a in range(affected, affected + len(ordered) - index + 1)):
                return
        person = ordered[index]
        for option in pools[person.id]:
            if capped:
                break
            need = Counter(option.resources)
            if points + option.points > point_budget or any(used[r] + n > resources[r] for r, n in need.items()):
                continue
            old = previous.get(person.id)
            d = item_distance(old.items, option.items) if old else 0
            visit(index + 1, {**chosen, person.id: option}, used + need,
                  cash + option.price, points + option.points, affected + int(d > 0), distance + d)

    visit(0, {}, Counter(), 0, 0, 0, 0)
    status = ("feasible" if capped else "optimal") if best is not None else ("unknown" if capped else "infeasible")
    result = {**base, "status": status, "search_complete": not capped,
              "optimal_within_snapshot": best is not None and not capped, "nodes": nodes,
              "minimum_budget_cents": cheapest if not capped else None,
              "lowest_found_budget_cents": cheapest,
              "minimum_extra_budget_cents": max(0, cheapest - budget) if cheapest is not None and not capped else None,
              "budget_breakpoints": [{"affected_people": a, "cash_cents": v[0][0],
                                      "proven": not capped} for a, v in sorted(frontier.items())]}
    if best is None:
        if cheapest is not None:
            result["conflicts"] = [{"reason": "当前预算不足。" if not capped else "搜索范围内尚未找到预算内方案。"}]
        elif not capped:
            result["conflicts"] = [{"reason": "共享库存、券次数或积分余额不足；未自动解锁或放宽饮食要求。"}]
        return result
    assignments, changes = [], []
    for person in people:
        option = best[person.id]
        row = {"person_id": person.id, "person_name": person.name, "option_id": option.id,
               "option_name": option.name, "items": list(option.items), "price_cents": option.price,
               "points": option.points, "resources": list(option.resources), "locked": person.id in locked}
        assignments.append(row)
        old = previous.get(person.id)
        d = item_distance(old.items, option.items) if old else 0
        changes.append({"person_id": person.id, "name": person.name,
                        "kind": "added" if old is None else "meal_changed" if d else
                        "repriced" if (old.price, old.points, old.option_id) != (option.price, option.points, option.id) else "unchanged",
                        "before": old.name if old else None, "after": option.name,
                        "cash_delta_cents": option.price - (old.price if old else 0),
                        "changed_item_units": d})
    total = sum(o.price for o in best.values())
    result.update(assignments=assignments, changes=changes, total_cents=total,
                  points_used=sum(o.points for o in best.values()),
                  affected_people=best_key[0], changed_item_units=best_key[1],
                  cash_delta_cents=total - base["previous_total_cents"],
                  resource_usage=dict(sum((Counter(o.resources) for o in best.values()), Counter())))
    return result
