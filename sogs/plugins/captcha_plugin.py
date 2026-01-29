import dataclasses
import logging
import typing_extensions
import datetime
import enum

from sogs.plugins.captcha import CaptchaManager, Captcha
from sogs.plugins_interface import *

class LogFormatter(logging.Formatter):
    @typing_extensions.override
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt     = datetime.datetime.fromtimestamp(record.created)
        result = dt.strftime('%y-%m-%d %H:%M:%S.%f')[:-3]
        return result

log_formatter = LogFormatter('%(asctime)s %(levelname)s %(name)s %(message)s')
console_log_handler = logging.StreamHandler()
console_log_handler.setFormatter(log_formatter)

log = logging.Logger('CAPTCHA')
log.addHandler(console_log_handler)

class RefreshState(enum.Enum):
    Nil     = 0
    Request = 1
    Wait    = 2
    Ready   = 3

class CaptchaState(enum.Enum):
    Nil     = 0
    Request = 1
    Wait    = 2
    Ready   = 3
    Solved  = 4

@dataclasses.dataclass
class UserCaptchaState:
    # Refreshing of CAPTCHAs state
    refresh_state:                        RefreshState     = RefreshState.Nil
    refresh_msg_id:                       MessageID | None = 0

    # CAPTCHA state in general
    captcha_state:                        CaptchaState     = CaptchaState.Nil
    captcha_failed_next_attempt_at_ts:    TimestampS       = 0 # Timestamp at which the user can retry again. None if the user isn't jailed
    captcha_attempts:                     int              = 0
    captcha_limit_msg_shown:              bool             = False
    captcha_solved_grant_access_at_ts:    TimestampS       = 0
    captcha_solved_welcome_msg_shown:     bool             = False
    captcha_solved_grant_access:          bool             = False

    # Metadata for managing the CAPTCHA posted to the user
    posted_captcha:                       Captcha | None   = None
    posted_captcha_timestamp:             TimestampS       = 0.0  # Time at which the CAPTCHA was posted
    posted_captcha_msg_id:                MessageID | None = None # Message ID of the CAPTCHA challenge we have sent to the user
    posted_captcha_refresh_emoji_applied: bool             = False

    # List of messages to cleanup/delete
    # Typically for cleaning up messages the bot has sent as diagnostics to the user such as timeout
    # messages before sending a new CAPTCHA or granting access to the room.
    msgs_to_delete_on_tick:               list[MessageID]  = dataclasses.field(default_factory=list)
    reactions_to_delete_on_tick:          list[MessageID]  = dataclasses.field(default_factory=list)
    msgs_to_delete_on_ready:              list[MessageID]  = dataclasses.field(default_factory=list)

    def clear_posted_captcha(self):
        self.posted_captcha_msg_id                = None
        self.posted_captcha_refresh_emoji_applied = False
        self.posted_captcha                       = None
        self.posted_captcha_timestamp             = 0

@dataclasses.dataclass
class CaptchaPlugin(Plugin):
    refresh_emoji:     str                                                = "\U0001F504" # Emoji to use that the user must react with to refresh the captcha
    users:             dict[SessionID, dict[RoomToken, UserCaptchaState]] = dataclasses.field(default_factory=dict)

    # Number of CAPTCHAs that a user can request and fail before being permanently jailed
    captcha_limit:    int                                                 = 3

    # The time a user has to wait after failing a CAPTCHA before a new CAPTCHA will be presented.
    retry_timeout_s:   int                                                = 10

    # The time a user has to wait between refreshes where they can request a new CAPTCHA. Each
    # refresh
    refresh_timeout_s: int                                                = 10

    # The amount of time that a user has to wait before they can start sending messages in a
    # community after successfully solving the CAPTCHA.
    write_timeout_s:   int                                                = 10
    captcha_manager:   CaptchaManager                                     = dataclasses.field(default_factory=CaptchaManager)

    def __post_init__(self):
        super().__post_init__()
        # NOTE: Register our hook which is called by SOGS when a user attempts to read from the
        # community. In this hook we check if the user has solved a captcha before and lets the user
        # read or otherwise require them to solve captcha to proceed.
        self.register_request_read_handler(self.handle_request_read)
        log.info("Plugin initialised: refresh {}s; retry {}s; write {}s; captcha limit {}"
                 .format(self.refresh_timeout_s, self.retry_timeout_s, self.write_timeout_s, self.captcha_limit))

    def get_user(self, session_id: bytes, room_token: bytes) -> UserCaptchaState | None:
        result = None
        if session_id in self.users and room_token in self.users[session_id]:
            result = self.users[session_id][room_token]
        return result

    def get_or_make_user(self, session_id: bytes, room_token: bytes) -> UserCaptchaState:
        result = self.users.setdefault(session_id, {}).setdefault(room_token, UserCaptchaState())
        return result

    def handle_request_read(self, req: RoomReadRequest) -> bt_value:
        """Handles generating a CAPTCHA for the user requesting read permission into a particular
        room as well as rate limiting these attempts and allowing users to refresh the provided
        CAPTCHA. If a user already has read permission, this hook is not called for that user.

        When this plugin is initialised, it hooks into the read requests for the current running
        SOGS. Session users that do not have read permissions for a room periodically request that
        permission. Plugins that are registered have their read function triggered where it can run
        arbitrary code at that point.

        Here we tick the plugin, executing a state machine that takes the user through the CAPTCHA
        challenge lifecycle for that particular user.
        """
        user: UserCaptchaState = self.get_or_make_user(req.session_id, req.room_token)
        if log.level <= logging.DEBUG:
            log.debug(f"Room {req.room_token} polled by 0x{req.session_id.hex()} (id={req.user_id}, challenges={user.captcha_attempts}/{self.captcha_limit})")

        result: bt_value = self.tick(room_token = req.room_token,
                                     session_id = req.session_id,
                                     room_name  = req.room_name)
        return result

    def _ensure_refresh_emoji_on_captcha(self, room_token: bytes, user: UserCaptchaState, msg_id: MessageID):
        captchas_remaining: int  = self.captcha_limit - user.captcha_attempts
        if not user.posted_captcha_refresh_emoji_applied and captchas_remaining > 1:
            react_resp: dict[bytes, bt_value] = self.post_reactions(room_token, msg_id, self.refresh_emoji)
            if b'status' in react_resp and react_resp[b'status'] == b'OK':
                user.posted_captcha_refresh_emoji_applied = True

    def tick(self, room_token: bytes, session_id: SessionID, room_name: str) -> bt_value:
        """Executes the CAPTCHA lifecycle for the specified user and room

        This is periodically called to progress the CAPTCHA lifecycle for the user:

          - First time users receive a CAPTCHA immediately to solve
          - The user can request a new CAPTCHA every `refresh_timeout_s` seconds
          - A user must wait `retry_timeout_s` seconds when the CAPTCHA is answered incorrectly
          - Throughout this process the user should be informed of the number of attempts they have,
            when they fail and amount of time they have to wait before taking certain actions.

        This plugin implements reliable message sending by checking that a message send is
        successful before progressing the CAPTCHA lifecycle.
        """
        # NOTE: Retrieve user state
        user: UserCaptchaState = self.get_or_make_user(session_id, room_token)

        # NOTE: Handle an incorrectly answered CAPTCHA. When a user answers a CAPTCHA incorrectly
        # the failed flag is set. A new CAPTCHA will not be generated by this plugin until the
        # `retry_timeout_s` delay has transpired since the time of the answer.
        #
        # Upon failure, the user must mandatorily wait and refresh capabilities are disabled.
        now: float = time()
        if user.captcha_state == CaptchaState.Ready or user.captcha_attempts >= self.captcha_limit:
          user.captcha_state = CaptchaState.Nil

        if user.captcha_state == CaptchaState.Request:
            attempts_remaining: int = self.captcha_limit - (user.captcha_attempts + 1)
            remaining               = f"{attempts_remaining} attempt" + ("s" if attempts_remaining > 1 else "")
            body                    = f"Incorrect emoji, a new CAPTCHA will be available in {self.retry_timeout_s} seconds. {remaining} remaining."
            msg_id                  = self.post_message(room_token, body, whisper_target=session_id, no_plugins=True)
            if msg_id:
                user.captcha_failed_next_attempt_at_ts = now + self.retry_timeout_s
                user.captcha_state                     = CaptchaState.Wait
                user.msgs_to_delete_on_ready.append(msg_id)
                if user.posted_captcha_msg_id: # Delete the failed CAPTCHA
                    user.msgs_to_delete_on_tick.append(user.posted_captcha_msg_id)
                    user.posted_captcha_msg_id = None

        if user.captcha_state == CaptchaState.Wait:
            if now >= user.captcha_failed_next_attempt_at_ts:
                user.captcha_attempts  += 1
                user.captcha_state  = CaptchaState.Ready

        if user.captcha_state == CaptchaState.Solved:
            s_remaining: float = user.captcha_solved_grant_access_at_ts - now
            if not user.captcha_solved_welcome_msg_shown:
                welcome_message: str   = ""
                if s_remaining <= 0:
                    welcome_message = f"Congratulations! You can now read and send messages in '{room_name}'."
                else:
                    welcome_message = f"Congratulations! You will be able to read and send messages in {int(s_remaining)} seconds."

                if self.post_message(room_token, welcome_message, whisper_target=session_id, no_plugins=True):
                    user.captcha_solved_welcome_msg_shown = True

            if s_remaining <= 0 and not user.captcha_solved_grant_access:
                resp = self.set_user_room_permissions(room_token=room_token, user_session_id=session_id, read=True, write=True)
                user.captcha_solved_grant_access = resp == b'OK'

        # NOTE: Handle refresh of the CAPTCHA. When a user requests a refresh, the refresh state is
        # set to a non-nil state. If a refresh is requested the user must wait a duration of
        # `refresh_timeout_s` from the last CAPTCHA post. Premature refreshes will cause the plugin
        # to effectively wait until the timeout has passed before generating the new CAPTCHA.
        #
        # When the refresh duration has transpired, the refresh state transitions into a `Ready`
        # state where the CAPTCHA is permitted to be generated.
        #
        # Note that a user cannot refresh a CAPTCHA if they're on their last attempt as that would
        # use up their final attempt and lead to failure.
        s_since_refresh: float = now - user.posted_captcha_timestamp
        if user.refresh_state == RefreshState.Ready or user.captcha_attempts >= self.captcha_limit:
            user.refresh_state = RefreshState.Nil

        if user.refresh_state == RefreshState.Request:
            # NOTE: Enqueue delete of old refresh message if it exists
            if user.refresh_msg_id:
                user.msgs_to_delete_on_tick.append(user.refresh_msg_id)
                user.refresh_msg_id = None

            if user.captcha_attempts >= (self.captcha_limit - 1):
                body                = f"You have hit the refresh limit, solve the CAPTCHA to proceed"
                user.refresh_msg_id = self.post_message(room_token, body, whisper_target=session_id, no_plugins=True)
                if user.refresh_msg_id:
                    user.refresh_state = RefreshState.Nil
            else:
                # NOTE: Double check if it is still necessary to show the refresh message
                if s_since_refresh >= self.refresh_timeout_s:
                    user.refresh_state = RefreshState.Wait
                else:
                    timeout:    float   = self.refresh_timeout_s - s_since_refresh
                    body                = f"You can refresh the CAPTCHA in {int(timeout)} second{'s' if timeout > 1 else ''}."
                    user.refresh_msg_id = self.post_message(room_token, body, whisper_target=session_id, no_plugins=True)
                    if user.refresh_msg_id:
                        user.refresh_state = RefreshState.Wait

        if user.refresh_state == RefreshState.Wait:
            if s_since_refresh >= self.refresh_timeout_s:
                user.captcha_attempts += 1
                user.refresh_state  = RefreshState.Ready

        # NOTE: When all CAPTCHAs are used up post the final message indicating that the user has
        # run out. No more CAPTCHAs will be generated after this.
        all_captchas_used = user.captcha_attempts >= self.captcha_limit
        if all_captchas_used and not user.captcha_limit_msg_shown:
            body = "You have reached the maximum number of CAPTCHA attempts. Contact an Administrator of the community for further assistance"
            if self.post_message(room_token, body, whisper_target=session_id, no_plugins=True) is not None:
                user.captcha_limit_msg_shown = True

        # NOTE: Determine if a new CAPTCHA should be generated based off the refresh/captcha and
        # captcha limits.
        ready_for_new_captcha = False
        if (user.refresh_state == RefreshState.Ready or  user.captcha_state == CaptchaState.Ready) or \
           (user.refresh_state == RefreshState.Nil   and user.captcha_state == CaptchaState.Nil  and user.posted_captcha_msg_id == None):
           if not all_captchas_used:
               ready_for_new_captcha = True

        # NOTE: If we're ready to create a new CAPTCHA we delete old messages by pushing them into
        # a list that retries the delete of the message until successful.
        #
        # These include messages such as the user has to wait before re-attempting and so forth
        # because we are about to send them a new CAPTCHA. It's moved into the tick queue which gets
        # reattempted periodically in case a deletion fails.
        if ready_for_new_captcha or all_captchas_used or user.captcha_state == CaptchaState.Solved:
            if user.posted_captcha_msg_id: # Delete old CAPTCHA
                user.msgs_to_delete_on_tick.append(user.posted_captcha_msg_id)
                user.posted_captcha_msg_id = None

            if user.refresh_msg_id: # Delete refresh message
                user.msgs_to_delete_on_tick.append(user.refresh_msg_id)
                user.refresh_msg_id = None

            user.msgs_to_delete_on_tick.extend(user.msgs_to_delete_on_ready)
            user.msgs_to_delete_on_ready.clear()

        # NOTE: Delete any refresh emoji reacts the user has enqueued on messages
        for msg_id in user.reactions_to_delete_on_tick:
            self.remove_reactions(room_token, msg_id, self.refresh_emoji)
        user.reactions_to_delete_on_tick.clear()

        # NOTE: Delete enqueued messages
        if self.delete_messages(user.msgs_to_delete_on_tick):
            user.msgs_to_delete_on_tick.clear()

        # NOTE: Ensure that the refresh emoji is always set on the CAPTCHA if it exists
        if user.posted_captcha_msg_id:
            self._ensure_refresh_emoji_on_captcha(room_token, user, user.posted_captcha_msg_id)

        if not ready_for_new_captcha or user.captcha_state == CaptchaState.Solved:
            return oxenc.bt_serialize("OK")

        return self._post_challenge(room_token, session_id, room_name)

    def _post_challenge(self, room_token: bytes, session_id: SessionID, room_name: str) -> bt_value:
        # NOTE: Reset state
        user: UserCaptchaState                    = self.get_or_make_user(session_id, room_token)
        user.posted_captcha_msg_id                = None
        user.posted_captcha_refresh_emoji_applied = False

        # NOTE: Generate a new CAPTCHA for the user
        user.posted_captcha           = self.captcha_manager.refresh();
        user.posted_captcha_timestamp = time()

        # NOTE: Upload the file to the SOGS so that we can reference it in a message
        captcha_attachment_metadata: dict[str, typing.Any] | None = self.upload_file(user.posted_captcha.rel_file_path, room_token)
        if not captcha_attachment_metadata:
            log.error(f"Failed to create a CAPTCHA for user 0x{session_id.hex()}: CAPTCHA file upload failed")
            user.clear_posted_captcha()
            return oxenc.bt_serialize("ERROR");

        # NOTE: Construct the message body to present to the user
        captchas_remaining: int  = self.captcha_limit - user.captcha_attempts
        first_time:         bool = user.captcha_attempts == 0
        body:               str  = ""
        if first_time:
            body += (f"Solve this CAPTCHA to read and send messages in {room_name}.\n\n")

        body += (f"React to this message with the emoji shown in the image.\n\n")
        if captchas_remaining > 0:
            if first_time:
                body += (f"You can refresh the CAPTCHA every {self.refresh_timeout_s} seconds by reacting with {self.refresh_emoji}. ")

            body += f"You have {captchas_remaining} attempt{'s' if captchas_remaining > 1 else ''}"
            if user.captcha_attempts != 0:
                body += f" remaining. "

        else:
            body += f"You have hit the attempt limit, solve this CAPTCHA to proceed."

        msg_id: MessageID | None = self.post_message(room_token=room_token, body=body, whisper_target=session_id, no_plugins=True, attachments_metadata=[captcha_attachment_metadata])
        if not msg_id:
            log.error(f"Failed to create a CAPTCHA for user 0x{session_id.hex()}: Message post failed")
            user.clear_posted_captcha()
            return oxenc.bt_serialize("ERROR");

        # NOTE: Apply refresh emoji react onto message if it's not the last one
        user.posted_captcha_msg_id = msg_id
        self._ensure_refresh_emoji_on_captcha(room_token, user, msg_id)

        return oxenc.bt_serialize("OK")

    @typing.override
    def reaction_posted(self, m: oxenmq.Message):
        """Handles reactions being posted on the CAPTCHA message which is either the user answering
        or requesting a refresh of the CAPTCHA.
        """
        # NOTE: Example
        #  {b'is_admin': 0, b'is_mod': 0, b'msg_id': 8, b'reaction': b'\xf0\x9f\x94\x84',
        #   b'room_id': 1, b'room_name': b'foobar2', b'room_token': b'foobar2',
        #   b'session_id': b'1500784b7c2096f6ed811b25c53a63e551954ee6778c7ae4437cb01c4b01fb4a09',
        #   b'user_id': 3}
        req: dict[bytes, bt_value] = oxenc.bt_deserialize(m.dataview()[0])

        # NOTE: Extract message components
        msg_id            = typing.cast(MessageID, req[b'msg_id'])
        session_id: bytes = bytes.fromhex(typing.cast(bytes, req[b'session_id']).decode('utf-8'))
        room_token        = typing.cast(bytes, req[b'room_token'])
        room_name:  str   = typing.cast(bytes, req[b'room_name']).decode('utf-8')
        reaction:   str   = typing.cast(bytes, req[b'reaction']).decode('utf-8')

        # NOTE: Lookup the user
        user: UserCaptchaState | None = self.get_user(session_id, room_token)
        if not user:
            log.warning(f'Reaction {reaction} received but user not known by plugin (user={session_id}, room={room_token}')
            return

        # NOTE: If the reaction was posted on the CAPTCHA message that was issued by this plugin
        # then we respond to it.
        if user.posted_captcha_msg_id == msg_id and user.posted_captcha:
            if reaction == user.posted_captcha.answer:
                user.captcha_solved_grant_access_at_ts = time() + self.write_timeout_s
                user.captcha_state                     = CaptchaState.Solved
                log.info(f"Access granted to 0x{session_id.hex()} in room '{room_token}'")
            elif reaction == self.refresh_emoji:
                user.reactions_to_delete_on_tick.append(msg_id) # Enqueue refresh emoji to be deleted
                user.refresh_state = RefreshState.Request
                log.debug(f"Refresh reacted by 0x{session_id.hex()} in room '{room_token}'")
            else:
                user.captcha_state = CaptchaState.Request
                log.debug(f"Incorrect emoji {reaction} reacted by 0x{session_id.hex()} in room '{room_token}')")

            _ = self.tick(room_token=room_token, session_id=session_id, room_name=room_name);


