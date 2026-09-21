"""Scanner registry. Add a scanner = add a module with NAME/TITLE/DESCRIPTION/PARAMS/scan and list it here."""
from . import cup_handle, darvas, stage

REGISTRY = {m.NAME: m for m in (stage, darvas, cup_handle)}
