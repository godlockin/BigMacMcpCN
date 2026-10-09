"""Unit tests for OrderParser - natural language order parsing."""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from mcd_assistant.order_parser import OrderParser
from mcd_assistant.mcp_client import McpClient
from mcd_assistant.config import Config


def make_parser():
    """Create an OrderParser instance for testing."""
    config = Config.load() if os.path.exists(
        os.path.join(os.path.dirname(__file__), "..", "config.json")
    ) else Config()
    client = McpClient(config)
    return OrderParser(client)


def test_parse_people_count():
    """Test parsing number of people from various NL patterns."""
    parser = make_parser()

    cases = [
        ("3个人吃", 3),
        ("10份饭", 10),
        ("5位", 5),
        ("组织workshop要10份", 10),
        ("今晚3个人吃", 3),
        ("8份套餐", 8),
    ]

    for text, expected in cases:
        spec = parser.parse(text)
        assert spec.total_people == expected, (
            f"Failed: '{text}' -> {spec.total_people}, expected {expected}"
        )


def test_parse_budget():
    """Test parsing budget from NL."""
    parser = make_parser()

    spec = parser.parse("5个人预算200")
    assert spec.budget == 200.0, f"Budget mismatch: {spec.budget}"

    spec = parser.parse("10份饭，预算500")
    assert spec.budget == 500.0, f"Budget mismatch: {spec.budget}"


def test_parse_meal_type():
    """Test parsing meal type from NL."""
    parser = make_parser()

    spec = parser.parse("今天中午3个人吃")
    assert spec.meal_type == "lunch", f"Meal type: {spec.meal_type}"

    spec = parser.parse("明天早餐2份")
    assert spec.meal_type == "breakfast", f"Meal type: {spec.meal_type}"

    spec = parser.parse("今晚5个人吃晚餐")
    assert spec.meal_type in ("dinner", "supper"), f"Meal type: {spec.meal_type}"


def test_parse_constraints():
    """Test parsing dietary constraints."""
    parser = make_parser()

    spec = parser.parse("10份饭，素食2份，汉堡加量4份")
    assert "vegetarian:2" in spec.constraints or any(
        c.startswith("vegetarian") for c in spec.constraints
    ), f"Constraints: {spec.constraints}"
    assert any(
        c.startswith("extra-burger") for c in spec.constraints
    ), f"Constraints: {spec.constraints}"


def test_parse_no_spicy():
    """Test parsing no-spicy constraint."""
    parser = make_parser()

    spec = parser.parse("3个人吃，不要辣")
    assert any("no-spicy" in c for c in spec.constraints), f"Constraints: {spec.constraints}"


def test_parse_workshop_scenario():
    """Test the user's exact example from the requirements."""
    parser = make_parser()

    text = "我今天中午要组织一个workshop，要准备10份饭，其中素食2份，汉堡加量4份，预算500"
    spec = parser.parse(text)

    assert spec.total_people == 10, f"People: {spec.total_people}"
    assert spec.budget == 500.0, f"Budget: {spec.budget}"
    assert spec.meal_type == "lunch", f"Meal type: {spec.meal_type}"
    assert len([c for c in spec.constraints if c.startswith("vegetarian")]) > 0
    assert len([c for c in spec.constraints if c.startswith("extra-burger")]) > 0


if __name__ == "__main__":
    test_parse_people_count()
    print("✅ test_parse_people_count passed")

    test_parse_budget()
    print("✅ test_parse_budget passed")

    test_parse_meal_type()
    print("✅ test_parse_meal_type passed")

    test_parse_constraints()
    print("✅ test_parse_constraints passed")

    test_parse_no_spicy()
    print("✅ test_parse_no_spicy passed")

    test_parse_workshop_scenario()
    print("✅ test_parse_workshop_scenario passed")

    print("\n🎉 All tests passed!")
