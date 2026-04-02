"""Pitch, the LinkedIn agent.

Patch leaves it a note when something is worth a post. Pitch reads the note, says whether there
is enough for one, and writes a draft when you ask. It never touches LinkedIn itself.
"""

from ...core.agent import Seat

# How the rest of the desk addresses Pitch. Patch needs only this to leave it a note.
PITCH = Seat(id="pitch", name="Pitch", role="LinkedIn writer")
# A note you left yourself: who it is from, and what kind of note it is.
YOU = "you"
PICK = "pick"
