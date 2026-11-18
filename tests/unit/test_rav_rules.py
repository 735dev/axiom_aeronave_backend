"""Passenger-seat and dangerous-goods rules from RAV 119.10 and RAV 135 chapter L."""

from app.config.tickets import RuleAction, RuleField, RuleOperator
from app.utils.rules_engine import Facts, RuleLike, fires


def rule(field: str, operator: str, value: str | None) -> RuleLike:
    return RuleLike(
        id="1",
        name="rav",
        field=field,
        operator=operator,
        value=value,
        action=RuleAction.WARN,
        message="m",
        route_limits=None,
    )


def facts(**payload) -> Facts:
    return Facts(amount_base=None, attachments=0, concept=None, route=None, payload=payload)


def test_more_than_19_seats_fires():
    limit = rule(RuleField.PASSENGERS, RuleOperator.GT, "19")
    assert fires(limit, facts(passengers=20))
    assert fires(limit, facts(passengers=20, governing_rav="rav_135"))
    assert not fires(limit, facts(passengers=20, governing_rav="rav_121"))
    assert not fires(limit, facts(passengers=19))
    assert not fires(limit, facts())


def test_dangerous_goods_fires_only_when_checked():
    box = rule(RuleField.CHECKBOX, RuleOperator.EQ, "dangerous_goods")
    assert fires(box, facts(dangerous_goods=True))
    assert not fires(box, facts(dangerous_goods=False))
    assert not fires(box, facts())
