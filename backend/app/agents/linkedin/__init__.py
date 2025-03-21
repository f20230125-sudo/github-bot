"""Pitch, the LinkedIn agent.

Patch leaves it a note when something is worth a post. Pitch reads the note and says whether
there is enough for one. It does not write posts yet, and it never touches LinkedIn itself.
"""

from ...core.agent import Seat

# How the rest of the desk addresses Pitch. Patch needs only this to leave it a note.
PITCH = Seat(id="pitch", name="Pitch", role="LinkedIn writer")
