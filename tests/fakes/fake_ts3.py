from tsmatrix_notify.domain.events import TSEvent


class FakeTS3Client:
    def __init__(self):
        self._handler = None
        self._clients = {}

    def version(self) -> str:
        return "fake"

    def clientlist(self):
        return list(self._clients.values())

    def clientinfo(self, clid: str):
        return self._clients[clid]

    def hostinfo(self):
        return {"instance_uptime": "0"}

    def serverinfo(self):
        return {"virtualserver_uptime": "0"}

    def add_client(self, clid: str, nickname: str, **extra):
        entry = {
            "clid": clid,
            "client_type": "0",
            "client_nickname": nickname,
            "client_away": extra.get("client_away", "0"),
            "client_away_message": extra.get("client_away_message", ""),
            "client_input_muted": extra.get("client_input_muted", "0"),
            "client_output_muted": extra.get("client_output_muted", "0"),
        }
        self._clients[clid] = entry
        return entry

    def remove_client(self, clid: str):
        self._clients.pop(clid, None)

    def register_event_handler(self, handler):
        self._handler = handler

    def emit(self, event: TSEvent):
        if self._handler:
            self._handler(event)
