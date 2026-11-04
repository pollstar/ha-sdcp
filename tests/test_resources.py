"""Pure planner of the Lovelace resource entry."""
import importlib.util
import sys
from pathlib import Path

_p = Path(__file__).parents[1] / "custom_components" / "sdcp" / "resources.py"
_spec = importlib.util.spec_from_file_location("sdcp_resources", _p)
res = importlib.util.module_from_spec(_spec)
sys.modules["sdcp_resources"] = res
_spec.loader.exec_module(res)

P = "/sdcp_static/sdcp-printer-card.js"


def it(i, url, t="module"):
    return {"id": str(i), "url": url, "type": t}


def test_removal_only_own():
    items = [it(1, P + "?v=abc"), it(2, "/hacsfiles/sdcp-printer-card/sdcp-printer-card.js"), it(3, P), it(4, "/x.js")]
    assert res.plan_removal(items, P) == ["1", "3"]
