"""Independent exhaustive oracle and behavioral decision-contract tests."""
import copy
import itertools
import json
import random
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from mcd_assistant.decision import InputError, solve

ROOT = Path(__file__).resolve().parents[1]


def request(prices=(1000, 2000), count=2, budget=2000):
    return {"snapshot": {"source": "synthetic", "store_id": "test", "captured_at": "test",
                         "options": [{"id": str(i), "name": str(i), "price_cents": price,
                                      "items": [str(i)], "verified_tags": []}
                                     for i, price in enumerate(prices)]},
            "participants": [{"id": f"p{i}", "name": f"P{i}", "allowed_options": [str(j) for j in range(len(prices))]}
                             for i in range(count)], "budget_cents": budget}


def test_budget_counterexample_and_floor():
    r = solve(request())
    assert r["status"] == "optimal" and r["total_cents"] == 2000
    r = solve(request(budget=1999))
    assert r["status"] == "infeasible" and r["minimum_extra_budget_cents"] == 1


def test_locked_choice_never_silently_relaxes():
    data = request(count=1, budget=3000)
    data["previous_assignments"] = solve(data)["assignments"]
    data["locked_people"] = ["p0"]
    data["participants"][0]["required_tags"] = ["no-spicy"]
    data["snapshot"]["options"][1]["verified_tags"] = ["no-spicy"]
    result = solve(data)
    assert result["status"] == "infeasible"
    assert result["conflicts"][0]["person_id"] == "p0"
    data["locked_people"] = []
    assert solve(data)["affected_people"] == 1


def test_departure_preserves_remaining_and_no_input_mutation():
    data = request(count=3, budget=6000)
    data["previous_assignments"] = solve(data)["assignments"]
    data["participants"].pop()
    before = copy.deepcopy(data)
    result = solve(data)
    assert data == before
    assert result["departed_people"] == ["p2"]
    assert result["affected_people"] == 0
    assert result["cash_delta_cents"] == -1000


def test_coupon_loss_reprices_locked_food_without_changing_person():
    data = request(prices=(800, 1000), count=1)
    data["snapshot"]["resources"] = {"coupon": 1}
    data["snapshot"]["options"][0]["resources"] = ["coupon"]
    data["snapshot"]["options"][1]["items"] = ["0"]
    data["previous_assignments"] = solve(data)["assignments"]
    data["locked_people"] = ["p0"]
    data["snapshot"]["resources"]["coupon"] = 0
    r = solve(data)
    assert r["total_cents"] == 1000 and r["affected_people"] == 0
    assert r["changes"][0]["kind"] == "repriced"


def test_points_and_shared_resources_not_reused():
    data = request(prices=(0, 1000), count=3, budget=3000)
    data["snapshot"].update(points_budget=100, resources={"coupon": 1})
    data["snapshot"]["options"][0].update(points=100, resources=["coupon"])
    r = solve(data)
    assert r["total_cents"] == 2000 and r["points_used"] == 100
    assert r["resource_usage"] == {"coupon": 1}


def test_change_count_prioritized_over_cash():
    data = request(prices=(2000, 1000), count=1, budget=3000)
    previous = copy.deepcopy(data)
    previous["participants"][0]["allowed_options"] = ["0"]
    data["previous_assignments"] = solve(previous)["assignments"]
    r = solve(data)
    assert r["affected_people"] == 0 and r["total_cents"] == 2000
    assert r["budget_breakpoints"] == [{"affected_people": 0, "cash_cents": 2000, "proven": True},
                                        {"affected_people": 1, "cash_cents": 1000, "proven": True}]


def test_search_cap_cannot_prove_infeasibility_or_floor():
    data = request()
    data["max_nodes"] = 1
    r = solve(data)
    assert r["status"] == "unknown" and not r["search_complete"]
    assert r["minimum_budget_cents"] is None


@pytest.mark.parametrize("change", [
    lambda r: r.update(budget_cents=True),
    lambda r: r.update(budget_cents=-1),
    lambda r: r.update(locked_people=["missing"]),
    lambda r: r["participants"].append(copy.deepcopy(r["participants"][0])),
    lambda r: r["snapshot"]["options"][0].update(resources=["unknown"]),
    lambda r: r["participants"][0].update(allowed_options=["missing"]),
    lambda r: r["snapshot"]["options"][0].update(price_cents=1.5),
    lambda r: r["snapshot"].update(source="official_verified"),
])
def test_fail_fast_invalid_inputs(change):
    data = request()
    change(data)
    with pytest.raises(InputError):
        solve(data)


def test_random_small_cases_against_independent_exhaustive_oracle():
    # Test constraints and optimization, not an implementation-shaped mock.
    rng = random.Random(91026)
    for _ in range(80):
        prices = [rng.randrange(50, 300) for _ in range(3)]
        data = request(prices=prices, count=3, budget=rng.randrange(200, 850))
        data["snapshot"].update(points_budget=100, resources={"shared": 1})
        data["snapshot"]["options"][0].update(resources=["shared"], points=100)
        old_ids = [rng.randrange(3) for _ in range(3)]
        data["previous_assignments"] = [{"person_id": f"p{i}", "option_id": str(k),
                                          "option_name": str(k), "items": [str(k)],
                                          "price_cents": prices[k]} for i, k in enumerate(old_ids)]
        possibilities = [ks for ks in itertools.product(range(3), repeat=3) if ks.count(0) <= 1]
        floor = min(sum(prices[k] for k in ks) for ks in possibilities)
        valid = [ks for ks in possibilities if sum(prices[k] for k in ks) <= data["budget_cents"]]
        result = solve(data)
        assert result["minimum_budget_cents"] == floor
        if not valid:
            assert result["status"] == "infeasible"
            continue
        def objective(ks):
            changed = sum(a != b for a, b in zip(ks, old_ids))
            return changed, changed * 2, sum(prices[k] for k in ks), ks.count(0) * 100, tuple(map(str, ks))
        expected = min(valid, key=objective)
        assert tuple(int(a["option_id"]) for a in result["assignments"]) == expected


def test_workshop_end_to_end_ten_people_and_replanning():
    data = json.loads((ROOT / "examples/workshop.json").read_text())
    base = solve(data)
    assert base["status"] == "optimal" and len(base["assignments"]) == 10
    data["previous_assignments"] = base["assignments"]
    data["participants"].pop()
    data["participants"][2]["required_tags"] = ["no-spicy"]
    data["locked_people"] = ["p1"]
    result = solve(data)
    assert result["status"] == "optimal" and len(result["assignments"]) == 9
    assert result["affected_people"] == 1
    assert result["assignments"][0]["items"] == base["assignments"][0]["items"]


def test_local_http_pipeline_auth_and_validation():
    # Import CLI by filename to avoid confusing it with package decision module.
    import importlib.util
    spec = importlib.util.spec_from_file_location("decision_cli", ROOT / "decision.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    server = module.make_server(request(), 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        page = urllib.request.urlopen(base).read().decode()
        token = page.split("const sessionToken='")[1].split("'")[0]
        body = json.dumps(request()).encode()
        headers = {"Content-Type": "application/json", "X-Decision-Token": token}
        response = urllib.request.urlopen(urllib.request.Request(base + "/api/solve", body, headers))
        assert json.load(response)["total_cents"] == 2000
        with pytest.raises(urllib.error.HTTPError) as rejected:
            urllib.request.urlopen(urllib.request.Request(base + "/api/solve", body))
        assert rejected.value.code == 403
        with pytest.raises(urllib.error.HTTPError) as invalid:
            urllib.request.urlopen(urllib.request.Request(base + "/api/solve", b'{}', headers))
        assert invalid.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
