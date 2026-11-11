"""A value saved before its field became a list stays editable while it does not change."""

from types import SimpleNamespace

from app.utils.ticket_payload import validate_payload


def field(key="captain", **kw):
    base = dict(
        key=key,
        type="select",
        catalog="users:pilot",
        options=None,
        required=False,
        read_only=False,
        edit_permission=None,
        label=key,
    )
    return SimpleNamespace(**{**base, **kw})


def run(incoming, existing, allowed=("Cap. Rodríguez",)):
    return validate_payload(
        [field()],
        incoming,
        existing=existing,
        creating=False,
        held=frozenset(),
        catalogs={"users:pilot": set(allowed)},
    )


def test_an_unchanged_free_text_value_is_not_rejected():
    result = run({"captain": "Juan de antes"}, {"captain": "Juan de antes"})
    assert not result.errors and result.payload["captain"] == "Juan de antes"


def test_changing_it_to_a_value_outside_the_list_is_rejected():
    assert run({"captain": "Otro de antes"}, {"captain": "Juan de antes"}).errors


def test_changing_it_to_a_listed_value_is_accepted():
    result = run({"captain": "Cap. Rodríguez"}, {"captain": "Juan de antes"})
    assert not result.errors and result.changed == {"captain"}
