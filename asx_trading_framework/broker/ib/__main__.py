"""Allow running the IB CLI as: python -m asx_trading_framework.broker.ib"""

import sys
from .cli import main

sys.exit(main())
