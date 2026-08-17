"""BookVoter runtime entrypoint.

Stable business logic remains in bot_core.py. Product-facing behavior is split
into small runtime override modules so UI/cleanup/reporting/library/voting can
evolve without another monolithic rewrite.
"""

import asyncio
import sys

import bot_core as core
import runtime_ux
import runtime_library
import runtime_reporting
import runtime_voting
import runtime_menu

runtime_ux.install()
runtime_library.install()
runtime_reporting.install()
runtime_voting.install()
runtime_menu.install()

if __name__ == "__main__":
    asyncio.run(core.main())
else:
    sys.modules[__name__] = core
