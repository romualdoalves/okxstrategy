import unittest

from backend.strategy_factory.code_policy import check_code

OK = '''
from __future__ import annotations
import pandas as pd
import pandas_ta as ta
from ..base import BaseStrategy, Signal

class S(BaseStrategy):
    def set_params(self, params):
        for k, v in params.items():
            setattr(self, k, v)
'''


class TestCodePolicy(unittest.TestCase):
    def test_accepts_regular_strategy(self):
        self.assertEqual(check_code(OK), [])

    def test_blocks_bypass_attempts(self):
        for code in [
            "import os",
            "from backend.main import app",
            "x = getattr(__builtins__, 'ev' + 'al')",
            "x = ().__class__.__bases__[0].__subclasses__()",
            "import pandas as pd\npd.read_csv('a')",
            "import pandas as pd\npd.io.common",
            "import logging.handlers as lh\nlh.socket.create_connection(('h', 1))",
            "x = '{0.__class__.__init__.__globals__}'.format(1)",
            "open('x', 'w')",
            "setattr(some_module, 'f', 1)",
        ]:
            with self.subTest(code=code):
                self.assertTrue(check_code(code))


if __name__ == "__main__":
    unittest.main()
