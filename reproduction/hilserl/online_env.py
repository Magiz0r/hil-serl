"""Human actions are selected by the existing NUC input owner, never here."""
from reproduction.autoserl.online_env import PortalEnv


class HILPortalEnv(PortalEnv):
    algorithm_id = 'hilserl'
    enable_interventions = False  # No AutoIntervention wrapper or recovery.

    def __init__(self, root, transport=None, *, evaluation=False):
        super().__init__(root, transport, human_intervention=not evaluation)

