import oxenc
import oxenmq
from time import time
from sogs.plugin import Plugin

class PermissionPlugin(Plugin):
    def __init__(
        self,
        sogs_address,
        sogs_pubkey,
        privkey,
        pubkey,
        display_name,
        *args,
        yes_reaction="\N{THUMBS UP SIGN}",
        no_reaction="\N{THUMBS DOWN SIGN}",
        retry_timeout=120,
        write_timeout=120,
    ):

        self.yes_reaction = yes_reaction
        self.no_reaction = no_reaction
        self.pending_requests = {}  # map {session_id : {room_token : msg_id } }
        self.retry_jail = {}
        self.retry_timeout = retry_timeout
        self.write_timeout = write_timeout

        Plugin.__init__(self, sogs_address, sogs_pubkey, privkey, pubkey, display_name)
        self.register_request_read_handler(self.handle_request_read)

    def handle_request_read(self, req):
        room_token = req[b'room_token']
        session_id = req[b'session_id']
        if session_id in self.retry_jail:
            if time() > self.retry_jail[session_id]:
                del self.retry_jail[session_id]
            else:
                return oxenc.bt_serialize("JAIL")

        if session_id in self.pending_requests and room_token in self.pending_requests[session_id]:
            return oxenc.bt_serialize("OK")
        print(f"request_read from {session_id}, id={req[b'user_id']}, room={room_token}")
        msg_id = self.post_message(
            room_token,
            "Please react with a thumbs up to agree to the room rules.",
            whisper_target=session_id,
            no_plugins=True,
        )
        if msg_id:
            react_resp = self.post_reactions(
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
        req = oxenc.bt_deserialize(m.dataview()[0])
        print(f"reaction_posted, req = {req}")
        msg_id = req[b'msg_id']
        session_id = req[b'session_id']
        room_token = req[b'room_token']
        if (
            session_id in self.pending_requests
            and room_token in self.pending_requests[session_id]
            and msg_id == self.pending_requests[session_id][room_token]
        ):
            print(f"reaction_posted, correct session_id, room, and msg_id")
            reaction = req[b'reaction'].decode('utf-8')
            if reaction == self.yes_reaction:
                print(f"Granting read permissions to {session_id} for room with token {room_token}")
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
                    no_plugins=True,
                )
            else:
                self.post_message(
                    room_token,
                    f"You chose...poorly.  You may try again in {self.retry_timeout} seconds with a new prompt.",
                    whisper_target=session_id,
                    no_plugins=True,
                )
                self.retry_jail[session_id] = time() + self.retry_timeout
            self.delete_message(msg_id)
            del self.pending_requests[session_id][room_token]
            if len(self.pending_requests[session_id]) == 0:
                del self.pending_requests[session_id]
