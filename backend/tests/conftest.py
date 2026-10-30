import os

# Keep engines that restore state at import time away from the real data/ files.
os.environ['VEGA_TESTING'] = '1'
