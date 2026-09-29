"""Idle gripper preparation; arm commands remain in the locked control mode."""

PREPARE_ACTIONS = {'gripper_open': 'open', 'gripper_close': 'close'}


def arm_command(command):
    """Only the existing lock command crosses the NUC arm protocol boundary."""
    return dict(command, action='lock') if command['action'] in PREPARE_ACTIONS else command


class IdleGripper:
    def __init__(self):
        self.seen_id = None
        self.active_id = None
        self.was_manual = None

    def step(self, command, message, manual_allowed, fresh=True, gripper=None):
        """Return one RS485 action, or None. Caller gives physical Stop priority."""
        leaving_manual = self.was_manual is not False and not manual_allowed
        self.was_manual = manual_allowed
        if manual_allowed:
            # Starting capture preserves the prepared grasp, without another move.
            self.active_id = None
            self.seen_id = command['id']
            return None
        prepared = preparation_status(gripper or {})
        if (command['action'] == 'start' and command['connected'] and fresh
                and self.active_id is not None and prepared and prepared['complete']
                and prepared['id'] == self.active_id):
            self.active_id = None
            self.seen_id = command['id']
            return None
        gate = message.get('pilot', {})
        safe = (fresh and command['connected'] and message.get('phase') == 'ready'
                and message.get('armed') and gate.get('mode') == 'locked'
                and gate.get('command') == 'lock' and gate.get('command_id') == command['id']
                and not gate.get('error') and not gate.get('custom_home', {}).get('saving'))
        prepare = command['action'] in PREPARE_ACTIONS
        if self.active_id is not None and (not safe or command['id'] != self.active_id):
            self.active_id = None
            return 'hold'
        if leaving_manual:
            return 'hold'
        if not prepare:
            if self.seen_id != command['id']:
                self.seen_id = command['id']
                return 'hold'
        elif safe and self.seen_id != command['id']:
            self.seen_id = self.active_id = command['id']
            return PREPARE_ACTIONS[command['action']]
        return None


def preparation_status(data):
    """Completion requires fresh caller-validated telemetry for the same RS485 ID."""
    preparation = data.get('preparation')
    if not preparation:
        return None
    status = data.get('status', {})
    target = 0 if preparation['action'] == 'open' else 255
    complete = (data.get('phase') == 'ready'
                and data.get('command_id') == preparation['command_id']
                and status.get('gFLT') == 0 and status.get('gGTO') == 1
                and status.get('gPR') == target and status.get('gOBJ') in (1, 2, 3))
    return dict(preparation, complete=complete, position=status.get('gPO'),
                contact=status.get('gOBJ') in (1, 2))
