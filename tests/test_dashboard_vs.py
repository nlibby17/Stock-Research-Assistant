from streamlit.testing.v1 import AppTest

from stockrank.dashboard_vs import _number, _score_bar


def _app(count=3):
    rows = [
        {
            "ticker": ticker,
            "company": f"{ticker} Company",
            "sector": "Technology",
            "rank": i + 1,
            "overall_score": 80 - i,
            "overall_coverage": 0.9,
            "component_scores": {"growth": 70},
            "component_coverage": {"growth": 0.8},
            "metrics": {"forward_pe": 25},
            "latest_price": 100,
            "price_as_of": "2026-09-04",
        }
        for i, ticker in enumerate(["AAA", "BBB", "CCC"][:count])
    ]
    source = (
        "from stockrank.dashboard_vs import render_stock_vs\n"
        f"render_stock_vs({rows!r}, {{'AAA': {{'thesis': 'Stored thesis', 'sources': []}}}}, "
        "{'as_of': '2026-09-04', 'provider': 'test', 'model_version': 'test'})"
    )
    return AppTest.from_string(source).run()


def test_vs_selectors_keep_distinct_stocks_and_update_cards():
    app = _app()
    assert not app.exception
    assert app.selectbox(key="vs_left").value == "AAA"
    assert app.selectbox(key="vs_right").value == "BBB"
    assert "AAA" not in app.selectbox(key="vs_right").options
    app.selectbox(key="vs_left").set_value("BBB").run()
    assert not app.exception
    assert app.selectbox(key="vs_right").value != "BBB"
    app.selectbox(key="vs_right").set_value("CCC").run()
    assert not app.exception
    cards = next(item.value for item in app.markdown if 'class="vs-arena"' in item.value)
    assert "BBB Company" in cards and "CCC Company" in cards
    assert "AAA Company" not in cards


def test_vs_discloses_missing_metrics_and_research():
    app = _app(2)
    assert not app.exception
    assert any("Unavailable" in item.value for item in app.markdown)
    assert any("Stored thesis" in item.value for item in app.markdown)
    assert any("Research has not been imported" in item.value for item in app.info)
    assert _number(float("nan")) == "Unavailable"
    assert _number(0, ".1%") == "0.0%"


def test_vs_requires_two_candidates():
    app = _app(1)
    assert not app.exception
    assert not app.selectbox
    assert any("at least two" in item.value for item in app.info)


def test_vs_is_collapsed_by_default_and_has_compact_color_coded_tables():
    app = _app()
    assert not app.exception
    assert app.expander[0].label == "Stock head-to-head"
    assert not app.expander[0].proto.expanded
    assert not any("Two perspectives" in item.value for item in app.caption)
    css = next(item.value for item in app.markdown if "vs-matrix" in item.value)
    assert "width:20%" in css
    assert ".vs-matrix td:nth-child(2){color:#e7c97f}" in css
    assert ".vs-matrix td:nth-child(3){color:#bdb0ed}" in css
    assert ".vs-matrix small{display:block;color:#f1f5fa" in css


def test_score_bars_show_stored_values_and_do_not_turn_missing_values_into_zero():
    assert "width:70.0%" in _score_bar(70)
    assert ">70.0</span>" in _score_bar(70)
    assert "width:0.0%" in _score_bar(0)
    assert "sr-missing" in _score_bar(None)
    assert "vs-component-track" not in _score_bar(None)
