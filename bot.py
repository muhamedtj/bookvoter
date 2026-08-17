"""BookVoter runtime entrypoint.

Stable business logic remains in bot_core.py. Product-facing behavior is split
into small runtime override modules so UI/cleanup/reporting/library integration
can evolve without another monolithic rewrite.
"""

import asyncio
import sys

import bot_core as core
import runtime_ux
import runtime_library
import runtime_reporting

runtime_ux.install()
runtime_library.install()
runtime_reporting.install()

if __name__ == "__main__":
    asyncio.run(core.main())
else:
    sys.modules[__name__] = core
