class FakeMatrix:
    def __init__(self, fail: bool = False):
        self.messages = []
        self.fail = fail
        self.ready = True

    def send_text(self, room_id: str, text: str, clid=None):
        if self.fail:
            raise RuntimeError("send failed")
        self.messages.append((room_id, text, clid))

    def is_ready(self) -> bool:
        return self.ready
