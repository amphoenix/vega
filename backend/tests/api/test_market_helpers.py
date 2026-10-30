import pandas as pd

from app.api.market import _cell


def test_cell_plain_value():
    row = pd.Series({'Open': 100.5, 'Volume': 1200})
    assert _cell(row, 'Open') == 100.5
    assert _cell(row, 'Volume') == 1200.0


def test_cell_unwraps_single_element_series():
    # yfinance multi-index frames give 1-element Series per cell
    row = {'Close': pd.Series([42.25])}
    assert _cell(row, 'Close') == 42.25


def test_cell_is_float():
    assert isinstance(_cell(pd.Series({'High': 7}), 'High'), float)
