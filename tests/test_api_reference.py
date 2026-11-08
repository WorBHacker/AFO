"""The API reference is made from the code (hub/api_reference.py, Q5): it must say what the code does now."""

from hub import api_reference


def test_what_the_reference_says():
    made = api_reference.render()
    assert "| GET | `/v1/health` | без токена |" in made
    assert "| PUT | `/v1/model_settings` | только окно |" in made  # a guard as a dependency
    assert "| POST | `/v1/links/scan` | только окно |" in made  # a guard called inside the handler
    assert "`?types: str = ''`" in made and "тело `{model_id: str}`" in made
    kinds = api_reference.event_types()
    assert kinds["chat.delta"]["stored"] is False and kinds["chat.done"]["stored"] is True
    assert kinds["links.proposed"]["keys"] == ["added", "links"]
    assert "port.opened" in kinds and "setup.changed" in kinds and "budget.{kind}" in kinds  # not in a publish call
    assert '"op": "subscribe"' in made
