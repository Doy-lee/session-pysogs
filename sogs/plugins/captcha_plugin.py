import dataclasses

from sogs.plugins.captcha import CaptchaManager, Captcha
from sogs.plugins_interface import *

@dataclasses.dataclass
class UserCaptchaState:
    pending_captcha_msg_id:                MessageID | None  = None # Message ID of the CAPTCHA challenge we have sent to the user
    pending_captcha_refresh_emoji_applied: bool              = False

    # Temporary messages sent by the bot that should be deleted. This is generally diagnostic
    # messages like informing the user that they must wait before requesting a new CAPTCHA if they
    # attempt to request a new CAPTCHA too early. When a new CAPTCHA is requested, these temporary
    # messages are deleted from the room.
    temp_msgs_to_delete:                   list[MessageID]   = dataclasses.field(default_factory=list)

    retry_jail:                            TimestampS | None = None # Timestamp at which the user can retry again. None if the user isn't jailed
    retry_failures:                        int               = 0
    challenge_captcha:                     Captcha | None    = None
    challenge_timestamp:                   TimestampS        = 0.0

@dataclasses.dataclass
class CaptchaPlugin(Plugin):
    refresh_emoji:     str                                                = "\U0001F504" # Emoji to use that the user must react with to refresh the captcha
    users:             dict[SessionID, dict[RoomToken, UserCaptchaState]] = dataclasses.field(default_factory=dict)

    # Number of CAPTCHAs that a user can request and fail before being permanently jailed
    retry_limit:       int                                                         = 3

    # The time a user has to wait after failing a CAPTCHA before a new CAPTCHA will be presented.
    retry_timeout_s:   int                                                         = 60

    # The time a user has to wait between refreshes where they can request a new CAPTCHA. Each
    # refresh
    refresh_timeout_s: int                                                         = 60

    # The amount of time that a user has to wait before they can start sending messages in a
    # community after successfully solving the CAPTCHA.
    write_timeout:     int                                                         = 120
    captcha_manager:   CaptchaManager                                              = dataclasses.field(default_factory=CaptchaManager)

    def __post_init__(self):
        super().__post_init__()
        # NOTE: Register our hook which is called by SOGS when a user attempts to read from the
        # community. In this hook we check if the user has solved a captcha before and lets the user
        # read or otherwise require them to solve captcha to proceed.
        self.register_request_read_handler(self.handle_request_read)

    def get_user(self, session_id: bytes, room_token: bytes) -> UserCaptchaState | None:
        result = None
        if session_id in self.users and room_token in self.users[session_id]:
            result = self.users[session_id][room_token]
        return result

    def get_or_make_user(self, session_id: bytes, room_token: bytes) -> UserCaptchaState:
        result = self.users.setdefault(session_id, {}).setdefault(room_token, UserCaptchaState())
        return result

    def handle_request_read(self, req: RoomReadRequest) -> bt_value:
        user: UserCaptchaState = self.get_or_make_user(req.session_id, req.room_token)
        if user.retry_failures >= self.retry_limit:
            return oxenc.bt_serialize("JAIL FOREVER")

        if user.retry_jail:
            if time() > user.retry_jail:
                user.retry_jail = None
            else:
                return oxenc.bt_serialize("JAIL")

        # NOTE: If the user already has a CAPTCHA challenge to solve, no furthera ction is needed.
        if user.pending_captcha_msg_id:
            return oxenc.bt_serialize("OK")

        print(f"request_read from {req.session_id}, id={req.user_id}, room={req.room_token}")
        return self.post_challenge(req.room_token, req.session_id, req.room_name)

    def post_challenge(self, room_token: bytes, session_id: SessionID, room_name: str) -> bt_value:
        try:
            user: UserCaptchaState = self.get_or_make_user(session_id, room_token)

            # NOTE: Delete the temporary/transient messages that the plugin has sent before sending
            # them the new CAPTCHA
            if len(user.temp_msgs_to_delete):
                self.delete_messages(user.temp_msgs_to_delete)
                user.temp_msgs_to_delete.clear()

            # NOTE: Generate a new CAPTCHA for the user
            captcha:                     Captcha               = self.refresh_captcha_handler(session_id, room_token)
            captcha_attachment_metadata: dict[str, typing.Any] = self.upload_file(captcha.rel_file_path, room_token)

            # NOTE: Construct the message body to present to the user
            # Case 1: User is coming to the community for the first time
            retries_remaining: int = self.retry_limit - user.retry_failures
            body:              str = ""
            if retries_remaining == self.retry_limit:
                body += (f"Solve this CAPTCHA to read and send messages in {room_name}. ")

            if retries_remaining > 0:
                body += (f"React to the image with the emoji shown in the image. ")
                if retries_remaining == self.retry_limit:
                    body += (f"You can refresh the CAPTCHA once every {self.refresh_timeout_s} seconds by reacting with {self.refresh_emoji}. ")
                body += f"You have {retries_remaining} time{'s' if retries_remaining > 1 else ''} remaining to refresh."
            else:
                body += f"You have hit the refresh limit. Please try to solve the current CAPTCHA by reacting with the emoji in the image."

            msg_id: MessageID | None = self.post_message(room_token=room_token, body=body, whisper_target=session_id, no_plugins=True, attachments_metadata=[captcha_attachment_metadata])
            print(f'Challenge message id: {msg_id}')
            if msg_id:
                user.pending_captcha_msg_id                = msg_id
                user.pending_captcha_refresh_emoji_applied = False
                if retries_remaining > 0:
                    # NOTE: Apply the refresh emoji onto our CAPTCHA message, example of response is:
                    #   {b'status': b'OK'}
                    react_resp: dict[bytes, bt_value] = self.post_reactions(room_token, msg_id, self.refresh_emoji)
                    if b'status' in react_resp and react_resp[b'status'] == b'OK':
                        user.pending_captcha_refresh_emoji_applied = True
                    else:
                        print(f"Error adding reactions to whisper: {react_resp}")
                        return oxenc.bt_serialize("ERROR")
        except:
            import traceback
            print(traceback.format_exc())
        return oxenc.bt_serialize("OK")

    def refresh_captcha_handler(self, session_id: SessionID, room_token: bytes) -> Captcha:
        user: UserCaptchaState   = self.get_or_make_user(session_id, room_token)
        user.challenge_captcha   = self.captcha_manager.refresh()
        user.challenge_timestamp = time()
        result                   = user.challenge_captcha
        return result

    def handle_refresh(self, msg_id: MessageID, session_id: SessionID, room_token: bytes, room_name: str):
        user: UserCaptchaState | None = self.get_user(session_id, room_token)
        if not user or user.retry_failures >= self.retry_limit:
            return

        s_since_refresh: float = time() - user.challenge_timestamp
        if s_since_refresh >= self.refresh_timeout_s:
            user.retry_failures += 1                               # Increase their failure count
            self.delete_message(msg_id)                            # Delete the old challenge message
            self.post_challenge(room_token, session_id, room_name) # Submit a new challenge message
        else:
            # NOTE: Remove the refresh emoji from the old CAPTCHA example:
            #   {b'status': b'OK'}
            _                                          = self.remove_reactions(room_token, msg_id, self.refresh_emoji)
            user.pending_captcha_refresh_emoji_applied = False

            # NOTE: Submit a message telling the user they must wait N seconds before re-requesting a CAPTCHA
            timeout:    float            = self.refresh_timeout_s - s_since_refresh
            new_msg_id: MessageID | None = self.post_message(
                room_token     = room_token,
                body           = f"You can refresh the CAPTCHA in {int(timeout)} second{'s' if timeout > 1 else ''}.",
                whisper_target = session_id,
                no_plugins     = True)

            if new_msg_id:
                user.temp_msgs_to_delete.append(new_msg_id)

    def handle_success(self, msg_id: MessageID, session_id: SessionID, room_token: bytes, room_name: str):
        immediate_write_access = False
        welcome_message        = ""
        if self.write_timeout <= 0:
            welcome_message        = f"Congratulations! You can now read and send messages in {room_name}."
            immediate_write_access = True
        else:
            welcome_message        = f"Congratulations! You will be able to read and send messages in {self.write_timeout} seconds."

        # NOTE: Welcome the user and grant them access
        self.post_message(room_token, welcome_message, whisper_target=session_id, no_plugins=True)
        self.set_user_room_permissions(room_token=room_token, user_session_id=session_id, sec_from_now=None, read=True, write=immediate_write_access)

        # NOTE: If there's a write delay, we enqueue write access to the user
        if not immediate_write_access:
            assert self.write_timeout > 0
            self.set_user_room_permissions(room_token=room_token, user_session_id=session_id, sec_from_now=self.write_timeout, write=True)

        # NOTE: Cleanup, delete the captcha message and user state
        # TODO: Maybe delete the user? We need to check how that the captcha plugin only sends the
        # challenge to non-authenticated members
        user: UserCaptchaState | None = self.get_user(session_id, room_token)
        assert user

        self.delete_message(msg_id)
        user.pending_captcha_refresh_emoji_applied = False
        user.pending_captcha_msg_id                = None

    def handle_failure(self, msg_id: MessageID, session_id: SessionID, room_token: bytes):
        user: UserCaptchaState | None = self.get_user(session_id, room_token);
        assert user

        user.retry_failures      += 1
        retries_remaining:   int  = self.retry_limit - user.retry_failures

        body: str = "That was the wrong emoji."
        if retries_remaining > 0:
            remaining  = f"{retries_remaining} attempt" + ("s" if retries_remaining > 1 else "")
            body      += f"You’ll receive a new CAPTCHA in {self.retry_timeout_s} seconds. You have {remaining} remaining."
        else:
            body += f"have reached the maximum number of attempts. Contact an Administrator of the community for further assistance"


        new_msg_id: MessageID | None = self.post_message(
            room_token,
            body,
            whisper_target=session_id,
            no_plugins=True,
        )

        user.retry_jail = time() + self.retry_timeout_s
        if new_msg_id: # TODO: Handle message failure
            user.temp_msgs_to_delete.append(new_msg_id)

        self.delete_message(msg_id)
        user.pending_captcha_msg_id                = None
        user.pending_captcha_refresh_emoji_applied = False

    @typing.override
    def reaction_posted(self, m: oxenmq.Message):
        # NOTE: Example
        #  {b'is_admin': 0, b'is_mod': 0, b'msg_id': 8, b'reaction': b'\xf0\x9f\x94\x84',
        #   b'room_id': 1, b'room_name': b'foobar2', b'room_token': b'foobar2',
        #   b'session_id': b'1500784b7c2096f6ed811b25c53a63e551954ee6778c7ae4437cb01c4b01fb4a09',
        #   b'user_id': 3}
        req: dict[bytes, bt_value] = oxenc.bt_deserialize(m.dataview()[0])

        msg_id     = typing.cast(MessageID, req[b'msg_id'])
        session_id = typing.cast(SessionID, req[b'session_id'])
        room_token = typing.cast(bytes, req[b'room_token'])
        room_name  = typing.cast(bytes, req[b'room_name']).decode('utf-8')

        user: UserCaptchaState | None = self.get_user(session_id, room_token)
        if user and user.pending_captcha_msg_id == msg_id and user.challenge_captcha:
            print(f"reaction_posted, correct session_id, room, and msg_id")
            reaction = typing.cast(bytes, req[b'reaction']).decode('utf-8')
            if reaction == self.refresh_emoji:
                print(f"{session_id} request refreshing challenge.")
                self.handle_refresh(msg_id, session_id, room_token, room_name)
            elif reaction == user.challenge_captcha.answer:
                print(f"Granting permissions to {session_id} for room with token {room_token}")
                self.handle_success(msg_id, session_id, room_token, room_name)
            else:
                print(f"Wrong answer! Can't grant permission to {session_id}")
                self.handle_failure(msg_id, session_id, room_token)


