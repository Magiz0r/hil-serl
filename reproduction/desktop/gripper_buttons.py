"""Single-button clicks on release; a two-button stop always takes precedence."""


class GripperButtons:
    def __init__(self):
        self.ready = False
        self.held = None
        self.pending = None
        self.stopped = False

    def observe(self, buttons):
        left, right = map(bool, buttons[:2])
        if left and right:
            self.stopped = True
            self.held = self.pending = None
        if self.stopped:
            return
        if not self.ready:
            self.ready = not (left or right)
            return
        if not left and not right:
            if self.held is not None:
                self.pending = self.held
            self.held = None
        elif self.held is None:
            self.held = 'close' if left else 'open'
        elif self.held != ('close' if left else 'open'):
            # Changing directly between buttons without release is ambiguous.
            self.ready = False
            self.held = self.pending = None

    def take(self):
        action, self.pending = self.pending, None
        return None if self.stopped else action
