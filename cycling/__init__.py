"""Cycling Progress Tracker — local engine.

Imports Wahoo .fit files, rebuilds elevation from UK lidar, estimates power
with honest uncertainty bands, and tracks fitness over time. Everything runs
on the local machine; the engine exposes a FastAPI server on localhost and the
browser UI talks to it.
"""

__version__ = "0.1.0"
