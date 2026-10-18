import os
import sys

_fin_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "Fin_Model_123"))
if _fin_dir not in sys.path:
    sys.path.insert(0, _fin_dir)
os.chdir(_fin_dir)

with open(os.path.join(_fin_dir, "final.py")) as _f:
    exec(compile(_f.read(), os.path.join(_fin_dir, "final.py"), "exec"))
