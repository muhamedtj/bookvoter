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
import runtime_entry
import runtime_suggest
import runtime_commands
import runtime_cleanup
import runtime_ratings
import runtime_stage_pin
import runtime_poll_pin
import runtime_private_rating
import runtime_suggest_events
import runtime_vote_eligibility
import runtime_pin_housekeeping
import runtime_manual_suggestor

runtime_ux.install()
runtime_library.install()
runtime_reporting.install()
runtime_voting.install()
runtime_menu.install()
runtime_entry.install()
runtime_suggest.install()
runtime_commands.install()
runtime_cleanup.install()
runtime_ratings.install()
runtime_stage_pin.install()
runtime_poll_pin.install()
runtime_private_rating.install()
runtime_suggest_events.install()
runtime_vote_eligibility.install()
runtime_pin_housekeeping.install()
runtime_manual_suggestor.install()

if __name__ == "__main__":
    asyncio.run(core.main())
else:
    sys.modules[__name__] = core
