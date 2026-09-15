"""Computer-use policy layer and the guided Eyes + Hands task flow.

Neither module grants authority. The tool registry and PermissionManager remain
the only execution boundary; these constrain how the computer-use arm may be
invoked and record the visual evidence for what it did.
"""

from .controller import VALID_AUTONOMY, ComputerController
from .task import STATUSES, VERDICTS, GuidedTaskManager, TaskError

__all__ = [
    "STATUSES",
    "VALID_AUTONOMY",
    "VERDICTS",
    "ComputerController",
    "GuidedTaskManager",
    "TaskError",
]
