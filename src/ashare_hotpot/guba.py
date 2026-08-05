"""Legacy per-stock-bar scanning module (removed).

The previous approach scanned every A-share stock bar and ranked stocks by the
number of ordinary-user topic posts. It was replaced by a low-frequency read of
the official Eastmoney popularity board; see ``popularity.py``. This module is
kept only as an empty placeholder so that no stale references break the package.
"""
