"""The LinkedIn agent's seat. Nobody sits here yet.

Patch already leaves messages for Pitch when something is worth a post. They wait in a tray on
the site until this agent exists. Nothing answers them, and nothing pretends to.
"""

from ...core.agent import Seat

PITCH = Seat(id="pitch", name="Pitch", role="LinkedIn writer")
