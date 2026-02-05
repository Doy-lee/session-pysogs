import dataclasses
import typing
import oxenc
import oxenmq
from time import time
from typing import Optional
from sogs.types import bt_value, SessionID, RoomToken, MessageID
from sogs.plugin import Plugin, RoomReadRequest
from typing import Dict

@dataclasses.dataclass
class PermissionPlugin(Plugin):
    yes_reaction:     str                                         = "\N{THUMBS UP SIGN}"
    no_reaction:      str                                         = "\N{THUMBS DOWN SIGN}"
    retry_timeout:    int                                         = 120
    write_timeout:    int                                         = 120
    pending_requests: Dict[SessionID, Dict[RoomToken, MessageID]] = dataclasses.field(default_factory=dict)
    retry_jail:       Dict[SessionID, float]                      = dataclasses.field(default_factory=dict)

    def __post_init__(self):
        super().__post_init__()
        self.register_request_read_handler(self.handle_request_read)

    def handle_request_read(self, req: RoomReadRequest) -> bt_value:
        room_token: RoomToken = req.room_token
        session_id: SessionID = req.session_id
        if session_id in self.retry_jail:
            if time() > self.retry_jail[session_id]:
                del self.retry_jail[session_id]
            else:
                return oxenc.bt_serialize("JAIL")

        if session_id in self.pending_requests and room_token in self.pending_requests[session_id]:
            return oxenc.bt_serialize("OK")
        print(f"request_read from {session_id.hex()}, id={req.user_id}, room={room_token}")
        msg_id: Optional[MessageID] = self.post_message(
            room_token,
            "Please react with a thumbs up to agree to the room rules.",
            whisper_target=session_id,
            relay_to_plugins=False,
        )
        if msg_id:
            react_resp: Dict[bytes, bt_value] = self.post_reactions(
                room_token, msg_id, self.yes_reaction, self.no_reaction
            )
            if b'error' in react_resp:
                print(f"Error adding reactions to whisper: {react_resp[b'error']}")
                return oxenc.bt_serialize("ERROR")
            if session_id not in self.pending_requests:
                self.pending_requests[session_id] = dict()
            self.pending_requests[session_id][room_token] = msg_id

        return oxenc.bt_serialize("OK")

    def reaction_posted(self, m: oxenmq.Message):
        req: Dict[bytes, bt_value] = oxenc.bt_deserialize(m.dataview()[0])
        print(f"reaction_posted, req = {req}")
        msg_id: MessageID = typing.cast(int, req[b'msg_id'])
        session_id: SessionID = bytes.fromhex(typing.cast(bytes, req[b'session_id']).decode('utf-8'))
        room_token: RoomToken = typing.cast(bytes, req[b'room_token'])
        if (
            session_id in self.pending_requests
            and room_token in self.pending_requests[session_id]
            and msg_id == self.pending_requests[session_id][room_token]
        ):
            print(f"reaction_posted, correct session_id, room, and msg_id")
            reaction: str = typing.cast(bytes, req[b'reaction']).decode('utf-8')
            if reaction == self.yes_reaction:
                print(f"Granting read permissions to {session_id.hex()} for room with token {room_token}")
                self.set_user_room_permissions(
                    room=room_token, user=session_id, sec_from_now=None, read=True
                )
                self.set_user_room_permissions(
                    room=room_token, user=session_id, sec_from_now=120, write=True
                )
                self.post_message(
                    room_token,
                    f"You may read now.  Study up, and you may learn to write in {self.write_timeout} seconds.",
                    whisper_target=session_id,
                    relay_to_plugins=False,
                )
            else:
                self.post_message(
                    room_token,
                    f"You chose...poorly.  You may try again in {self.retry_timeout} seconds with a new prompt.",
                    whisper_target=session_id,
                    relay_to_plugins=False,
                )
                self.retry_jail[session_id] = time() + self.retry_timeout
            self.delete_message(msg_id)
            del self.pending_requests[session_id][room_token]
            if len(self.pending_requests[session_id]) == 0:
                del self.pending_requests[session_id]
