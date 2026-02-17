import traceback
import oxenmq
import time
import functools
import dataclasses
import typing
import sogs.plugin
import sogs.types
import sogs.utils
import logging

from typing import Dict, List, Optional, Set, Tuple
from oxenc import bt_deserialize, bt_serialize
from datetime import timedelta
from nacl.encoding import HexEncoder

from sogs.types import bt_value, MessageID
from .web import app
from . import cleanup
from . import config
from . import omq as o
from .db import query
from .model.user import User
from .model.room import Room
from .model.exc import NoSuchRoom, NoSuchUser
from .model.post import Post

from . import db

# Plugin identifier that is unique to the database for the SOGS. It is the row ID primary key from
# SQL allocated to the plugin when it is registered to the DB in the `plugins` table.
PluginID = int

# This is the uwsgi "mule" that handles things not related to serving HTTP requests:
# - it holds the oxenmq instance (with its own interface into sogs)
# - it handles cleanup jobs (e.g. periodic deletions)

# holds plugin_id -> plugin omq connection for connected plugins
plugin_conns: Dict[PluginID, oxenmq.ConnectionID] = {}

@dataclasses.dataclass
class PluginMetadata:
    id:     PluginID = 0
    name:   str      = ''

    # A plugin manifests itself as a user in the community. When a plugin identifies itself to the
    # SOGS (e.g. starts hello handshake), the user account for the plugin gets added as a
    # moderator/admin. This field is the user info for said user representing the plugin.
    user:          Optional[User] = None

    def describe_str(self) -> str:
        result = f"Plugin '{self.name}' (id={self.id}"
        if self.user:
            result += f", user={typing.cast(str, self.user.session_id)})"
        else:
            result += f")"
        return result

# holds oxenmq ConnectionID -> metadata (plugin_id, plugin session_id, etc.)
plugin_conn_info: Dict[oxenmq.ConnectionID, PluginMetadata] = {}

# holds command -> plugin_id for commands registered by plugins
# key includes the prefix (default slash, may make configurable)
plugin_pre_commands:  Dict[str, Set[PluginID]] = {}
plugin_post_commands: Dict[str, Set[PluginID]] = {}

@dataclasses.dataclass
class PluginInfo:
    required: bool  = False
    name:     str   = ''
    ed_key:   bytes = b''

@dataclasses.dataclass
class FilterResult:
    response: sogs.plugin.FilterResponse
    info:     Optional[PluginInfo] = None
    reason:   Optional[str]        = None  # Human-readable description of why the message was filtered

# not changing the keys, since this is just for fixing the values if they
# need to be str and not bytes
def bytestring_fixup(d, keys):
    for k in keys:
        if k in d:
            d[k] = d[k].decode('utf-8')

def log_exceptions(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            app.logger.error(f"{f.__name__} raised exception: {e}")
            raise

    return wrapper


def needs_app_context(f):
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        with app.app_context():
            return f(*args, **kwargs)

    return wrapper

def run():
    try:
        app.logger.info("OxenMQ mule started.")

        while True:
            time.sleep(1)

    except Exception:
        app.logger.error("mule died via exception:\n{}".format(traceback.format_exc()))


@needs_app_context
def allow_conn(addr: str, pk: bytes, sn: bool):  # pyright: ignore[reportUnusedParameter]
    with db.transaction():
        row = query("SELECT id FROM plugins WHERE x_key = :key", key=pk).first()
        if row:
            app.logger.debug(f"Plugin connected: {HexEncoder.encode(pk)}")
            return oxenmq.AuthLevel.basic

    app.logger.warning(f"No plugin found with key: {HexEncoder.encode(pk)}")
    return oxenmq.AuthLevel.denied # TODO: user recognition auth

def admin_conn(addr: str, pk: bytes, sn: bool):  # pyright: ignore[reportUnusedParameter]
    return oxenmq.AuthLevel.admin

def inproc_fail(connid: oxenmq.ConnectionID, reason: str):  # pyright: ignore[reportUnusedParameter]
    raise RuntimeError(f"Couldn't connect mule to itself: {reason}")

@needs_app_context
@log_exceptions
def get_relevant_plugins(where_clause: str, room_id: Optional[int] = None, room_token: Optional[str] = None) -> Dict[PluginID, PluginInfo]:
    """
    Retrieve plugins that match the given filter criteria from both global and room-specific contexts.

    This function queries the `plugins` table for globally-enabled plugins and the `room_plugins`
    table for room-specific plugin configurations. It returns a mapping of plugin IDs to whether
    each plugin is marked as required.

    See `sogs/schema.sqlite` for more information on the table schema.

    Args:
        where_clause: SQL WHERE condition to filter plugins (e.g., "approver = 1" or "subscribe = 1").
                      This is appended to queries against both tables.
        room_id: Optional room ID to look up room-specific plugin configurations. If provided,
                 room_token is ignored.
        room_token: Optional room token to resolve to a room_id. Only used if room_id is None.

    Returns:
        A dictionary mapping plugin IDs (int) to required status (bool), or None if the room
        lookup fails. The required status is True if the plugin is marked as required in either
        the global or room-specific context (logical OR of both settings).
    """
    result: Dict[PluginID, PluginInfo] = {}
    with db.transaction():
        # Resolve room_token to room_id if needed
        if room_token and not room_id:
            id_row = query("SELECT id FROM rooms WHERE token = :token", token=room_token).first()
            if id_row is None:
                app.logger.warning(f"filtering message for inexistent room with token: \"{room_token}\"??")
                return result
            room_id = typing.cast(int, id_row['id'])

        # Query global plugins that match the where_clause
        query_str = "SELECT id, name, required, ed_key FROM plugins WHERE global = 1 AND " + where_clause
        rows      = query(query_str)
        for row in rows:
            required          = True if row['required'] and row['required'] == 1 else False
            result[row['id']] = PluginInfo(required=required, name=typing.cast(str, row['name']), ed_key=typing.cast(bytes, row['ed_key']))

        # Query room-specific plugins and merge with global results
        if room_id:
            query_str = "SELECT plugin, required FROM room_plugins WHERE room = :room_id AND " + where_clause
            rows      = query(query_str, room_id=room_id)
            for row in rows:
                # Determine if the room mandates that the plugin is required
                required  = True if row['required'] and row['required'] == 1 else False
                plugin_id = typing.cast(PluginID, row['plugin'])

                # Apply the required flag (note: Plugin ID FK is located in the 'plugin' column for the room_plugins table)
                if plugin_id in result:
                    result[plugin_id].required |= required
                else:
                    # This plugin is only applicable in this room, we'll lookup its human readable name
                    lookup            = "SELECT name, required, ed_key FROM plugins WHERE id = :id AND  " + where_clause
                    plugin_row        = query(lookup, id=plugin_id).first()
                    result[plugin_id] = PluginInfo(name=typing.cast(str, plugin_row['name']), required=required, ed_key=typing.cast(bytes, plugin_row['ed_key']))
    return result

# Commands from SOGS/uwsgi
@needs_app_context
@log_exceptions
def plugin_on_room_add_post_request(m: oxenmq.Message) -> Optional[bytes]:
    """Called by SOGS when a user sends a message to a room to execute pre/post on-message hooks"""
    responded = False
    try:
        # Run message filtering hooks
        req:           sogs.types.RoomAddPostRequest = sogs.types.RoomAddPostRequest.from_bencode(m.dataview()[0])
        filter_result: FilterResult                  = plugin_filter_message(req)
        if filter_result.response == sogs.plugin.FilterResponse.Reject:
            # Build detailed error message
            plugin_desc = ""
            if filter_result.info:
                plugin_desc = f"'{filter_result.info.name}' (0x{filter_result.info.ed_key.hex()[:8]}..{filter_result.info.ed_key.hex()[-8:]})"

            error_msg = "Message rejected"
            if plugin_desc:
                error_msg += f" by {plugin_desc}"
            if filter_result.reason:
                error_msg += f" [{filter_result.reason}]"

            return bt_serialize({b"error": error_msg})

        # Create the updated payload (w/ filtered now set, if it was filtered)
        raw_payload: list[bytes] = m.data()
        if filter_result.response == sogs.plugin.FilterResponse.Silent:
            req.filtered   = True
            raw_payload[0] = req.to_bencode()

        # TODO: Make the command trigger character configurable. IMO this is not important, infact
        # it's probably better that it's a fixed character and is predictable across all SOGS and
        # enforces a consistent "design language".
        command       = ""
        msg           = Post(raw=req.message_data)
        msg_text: str = typing.cast(str, msg.text)
        if msg_text.startswith('/'):
            app.logger.debug(f"Processing slash command, pre-command phase")
            command = msg_text.split(' ')[0]

        # Run pre-message hooks to check if the message is allowed to be posted in the room
        if len(command):
            if not plugin_pre_message_commands(raw_payload, command):
                return bt_serialize({b"ok": True})

        # TODO: handle edit message
        room              = Room(id=req.room_id)
        msg_id: MessageID = room.insert_message(sogs.types.MessageInsert(
            unpadded_data    = sogs.utils.remove_session_message_padding(req.message_data),
            padded_data_size = len(req.message_data),
            filtered         = req.filtered,
            user_id          = req.user_id,
            whisper_mods     = req.whisper_mods,
            sig              = req.sig,
            alt_id           = req.alt_id,
            whisper_to       = req.whisper_to,
        ))

        # Manually reply so we don't hold up the worker longer than necessary
        m.reply(bt_serialize({b"ok": True, b"msg_id": msg_id}))
        responded = True

        # Run post-message hooks that can react to the _act_ of a message being posted into the room
        plugin_post_message_commands(raw_payload, command)
        relay_on_message_posted(msg_id)
    except Exception as e:
        app.logger.warning(f"Exception handling new/edited message from sogs: {e}")
        if not responded:
            return bt_serialize({b"error": f"{e}"})


@needs_app_context
@log_exceptions
def request_read(m: oxenmq.Message):
    """
    This is a request rather than a command so that sogs waits for it to finish
    before answering the read request that triggered it.  This lets a plugin add
    a whisper to the user, if desired, which sogs will deliver.
    """
    command = '/request_read'
    retval = bt_serialize("OK")  # always, for now
    if command not in plugin_pre_commands:
        return retval
    if len(plugin_pre_commands[command]) != 1:
        return retval

    plugin_id = list(plugin_pre_commands[command])[0]
    if plugin_id not in plugin_conns:
        return retval

    try:
        app.logger.debug(f"Giving command 'request_read' to plugin (id={plugin_id})")
        _ = o.omq.request_future(plugin_conns[plugin_id], "plugin.request_read", *m.data(), request_timeout=timedelta(seconds=1)).get()
    except TimeoutError as e:
        app.logger.warning(f"Timeout from plugin (id={plugin_id}) handling request_read: {e}")
    except Exception:
        # TODO: Should this fail the whole command?
        pass

    return retval


@log_exceptions
def messages_deleted(m: oxenmq.Message):
    ids = bt_deserialize(m.data()[0])
    app.logger.debug(f"FIXME: mule -- message delete stub, deleted messages: {ids}")


@log_exceptions
def message_edited(m: oxenmq.Message):
    app.logger.debug("FIXME: mule -- message edited stub")

@log_exceptions
def plugin_filter_message(req: sogs.types.RoomAddPostRequest) -> FilterResult:
    result                                 = FilterResult(response=sogs.plugin.FilterResponse.Accept)
    plugin_ids: Dict[PluginID, PluginInfo] = get_relevant_plugins("approver = 1", room_id=req.room_id)
    if len(plugin_ids) == 0:
        return result

    app.logger.debug(f"Requesting message approval from {len(plugin_ids)} plugins.")

    # If the plugin is required for the filtering step and we don't have have an OMQ connection for
    # it to forward the message to for filtering, then we reject the message outright.
    for id in plugin_ids:
        info: PluginInfo = plugin_ids[id]
        if info.required and id not in plugin_conns:
            app.logger.warning(f"Message rejected because required plugin '{info.name}' (id={id}, ed_key={info.ed_key.hex()}) was required but has not connected to SOGS yet")
            return FilterResult(response=sogs.plugin.FilterResponse.Reject, info=info)

    # Submit the message to the plugins and collect the async handles
    pending_requests: List[Tuple[oxenmq.ResultFuture, PluginID]] = []
    for id in plugin_ids:
        if id in plugin_conns:
            pending_requests.append((
                o.omq.request_future(plugin_conns[id], "plugin.filter_message", req.to_bencode(), timeout = timedelta(seconds=1).seconds,),
                id,
            ))

    # Await all the async handles
    silent_result: Optional[FilterResult] = None
    for pending in pending_requests:
        future:      oxenmq.ResultFuture = pending[0]
        plugin_info: PluginInfo          = plugin_ids[pending[1]]
        try:
            response: List[bytes] = future.get()
            if len(response) != 1:
                return FilterResult(response=sogs.plugin.FilterResponse.Reject, info=plugin_info)

            # Parse the response from the plugin
            plugin_result = sogs.plugin.FilterResult.from_bencode(response[0])

            if plugin_result.status == sogs.plugin.FilterResponse.Accept:
                continue

            if plugin_result.status == sogs.plugin.FilterResponse.Silent:
                # Capture the first silent result with details
                if silent_result is None:
                    silent_result = FilterResult(response = sogs.plugin.FilterResponse.Silent,
                                                 info     = plugin_info,
                                                 reason   = plugin_result.reason)
                continue

            # Reject
            result = FilterResult(response = sogs.plugin.FilterResponse.Reject,
                                  info     = plugin_info,
                                  reason   = plugin_result.reason)
            break
        except Exception as e:
            app.logger.warning(f"Plugin filter exception: {e}")
            return FilterResult(response=sogs.plugin.FilterResponse.Reject, info=plugin_info)

    if silent_result:
        return silent_result
    return result


@needs_app_context
@log_exceptions
def _plugin_message_commands(data: List[bytes], command: str, pre_command: bool) -> bool:
    """
    pass command to plugins registered for that command, in order.
    Plugin returns True if we should continue handling the message, i.e. either that plugin ignored it
    or that plugin errored/thinks the command should not be handled further.
    If all plugins return True, this function returns True (to indicate to continue handling), else
    return False.
    If no plugins are registered to handle the command, return True (NOTE: not sure on this)

    For now, "/request_read" and "/request_write" will be special commands which, rather than
    passing a user's message to the plugin, will pass session_id, user.id, room.id, room.token
    As these are special, they are handled elsewhere, not in this function
    """

    commands_container = plugin_pre_commands if pre_command else plugin_post_commands
    command_type: str  = "pre_message_command" if pre_command else "post_message_command"

    if command not in commands_container:
        # FIXME: Should we (silently?) drop messages which start with '/' but aren't registered commands?
        return True

    for plugin_id in commands_container[command]:
        if plugin_id not in plugin_conns:
            app.logger.warning(f"Plugin (id={plugin_id}) registered to handle {command_type} {command} but no longer in plugin_conns, somehow.")
            continue

        resp: list[bytes] = []
        try:
            app.logger.debug(f"Giving {command_type} {command} to plugin (id={plugin_id})")
            resp = o.omq.request_future(
                conn            = plugin_conns[plugin_id],
                cmd             = f"plugin.{command_type}",
                data            = data,
                request_timeout = timedelta(seconds=0.2),
            ).get()
        except TimeoutError as e:
            app.logger.warning(f"Timeout from plugin (id={plugin_id}) handling {command_type} {command}")
            if pre_command:
                return False
        except Exception as e:
            app.logger.warning(f"Error from plugin (id={plugin_id}) handling {command_type} {command}, error: {e}")
            if pre_command:
                return False

        should_continue = typing.cast(bool, bt_deserialize(resp[0]))
        app.logger.debug(f"{command_type} {command} response from plugin: {should_continue}")
        if pre_command and should_continue == False:
            return False

    return True


def plugin_pre_message_commands(data: List[bytes], command: str) -> bool:
    return _plugin_message_commands(data, command, pre_command=True)

def plugin_post_message_commands(data: List[bytes], command: str) -> bool:
    return _plugin_message_commands(data, command, pre_command=False)

def setup_omq():
    app.logger.debug("Mule setting up omq")
    omq: oxenmq.OxenMQ = o.omq
    listen             = config.OMQ_LISTEN if isinstance(config.OMQ_LISTEN, list) else [config.OMQ_LISTEN]
    for addr in listen:
        omq.listen(addr, curve=True, allow_connection=allow_conn)
        app.logger.info(f"OxenMQ listening on {addr}")

    # Internal socket for workers to talk to us:
    omq.listen(config.OMQ_INTERNAL, curve=False, allow_connection=admin_conn)

    # Periodic database cleanup timer:
    _ = omq.add_timer(job=cleanup.cleanup_no_ret, interval=timedelta(seconds=cleanup.INTERVAL))

    # Commands other workers can send to us, e.g. for notifications of activity for us to know about
    plugin = omq.add_category("plugin", access_level=oxenmq.AuthLevel.basic)
    plugin.add_request_command("hello", plugin_hello)
    plugin.add_command("register_pre_commands", plugin_register_pre_command)
    plugin.add_command("register_post_commands", plugin_register_post_command)
    plugin.add_request_command("delete_message", plugin_delete_message)
    plugin.add_request_command("post_reactions", plugin_post_reactions)
    plugin.add_request_command("remove_reactions", plugin_remove_reactions)
    plugin.add_request_command("insert_message", plugin_insert_message)
    plugin.add_request_command("upload_file", plugin_upload_file)
    plugin.add_request_command("set_user_room_permissions", plugin_set_user_room_permissions)
    worker = omq.add_category("worker", access_level=oxenmq.AuthLevel.admin)
    worker.add_request_command("on_room_add_post_request", plugin_on_room_add_post_request)
    worker.add_request_command("request_read", request_read)
    worker.add_command("messages_deleted", messages_deleted)
    worker.add_command("message_edited", message_edited)
    worker.add_command("reaction_posted", relay_on_reaction_posted)

    app.logger.debug("Mule starting omq")
    omq.start()

    # Connect mule to itself so that if something the mule does wants to send something to the mule
    # it will work.  (And so be careful not to recurse!)
    app.logger.debug("Mule connecting to self")
    o.mule_conn = omq.connect_inproc(on_success=None, on_failure=inproc_fail)


@needs_app_context
@log_exceptions
def plugin_hello(m: oxenmq.Message):
    app.logger.debug(f"plugin.hello called with key: {m.conn.pubkey}")

    new_plugin_conn = False
    with db.transaction():

        row = query("SELECT id, name FROM plugins WHERE x_key = :key", key=m.conn.pubkey).first()
        if row is None:
            # TODO: would like to close conn in this case, but oxenmq only allows close on outgoing conns.
            app.logger.warning(f"No plugin found with key: {m.conn.pubkey}")
            return bt_serialize("NoSuchPlugin")

        plugin_conns[row['id']] = m.conn
        if m.conn not in plugin_conn_info:
            new_plugin_conn          = True
            plugin_conn_info[m.conn] = PluginMetadata()

        metadata: PluginMetadata = plugin_conn_info[m.conn]
        metadata.id              = typing.cast(int, row['id'])
        try:
            if len(m.dataview()):
                session_id: str = bt_deserialize(m.dataview()[0]).decode('ascii')
                u               = User(session_id=session_id, autovivify=True)

                # TODO: handle plugin permissions and setup better
                admin_user = User(id=0)
                u.set_moderator(added_by=admin_user, visible=True)
                metadata.user = u
        except Exception as e:
            app.logger.warning(f"Plugin with id {row['id']} tried to register bad session_id.")
            del plugin_conns[row['id']]
            del plugin_conn_info[m.conn]
            return bt_serialize("BadSessionID")

    new_str = "new " if new_plugin_conn else ""
    app.logger.debug(f"Added {new_str}plugin connection for known key: {m.conn.pubkey}")

    # inform the plugin that as far as we know this is a new connection from it, it should
    # re-register commands as desired
    if new_plugin_conn:
        return bt_serialize("REGISTER")

    return bt_serialize("OK")


def _require_plugin_conn_info(conn: oxenmq.ConnectionID, need_user: bool, msg_prefix: str) -> Optional[PluginMetadata]:
    result: Optional[PluginMetadata] = None
    if conn not in plugin_conn_info:
        app.logger.warning((f"{msg_prefix}: There is no plugin registered under the connection ID "
                             "{conn}. Has the plugin called `Plugin.say_hello()` yet, or check if "
                             "there was any networking via OMQ communication errors from the "
                             "plugin or SOGs"))
        return result

    result = plugin_conn_info[conn]
    if need_user and not result.user:
        app.logger.warning((f"{msg_prefix}: There is no user registered for the plugin "
                             "'{result.name}' (id={result.id}) at connection ID {conn}. Has the "
                             "plugin called `Plugin.say_hello()` and passed a Session ID, or check "
                             "if there was any networking via OMQ communication errors from the "
                             "plugin or SOGs"))
        return result

    assert result.id != 0
    return result

@needs_app_context
@log_exceptions
def plugin_register_command(m: oxenmq.Message, pre_command: bool):
    metadata: Optional[PluginMetadata] = _require_plugin_conn_info(m.conn, need_user=False, msg_prefix="Failed to register plugin")
    if not metadata:
        return

    command_type:       str                      = "pre_command" if pre_command else "post_command"
    commands_container: Dict[str, Set[PluginID]] = plugin_pre_commands if pre_command else plugin_post_commands

    req:      Dict[bytes, oxenc.bt_value] = bt_deserialize(m.dataview()[0])
    commands: List[bytes]                 = typing.cast(List[bytes], req[b'commands'])
    app.logger.debug(f"register_{command_type}, commands: {commands}")
    for command in commands:
        command_utf8: str = ''
        try:
            command_utf8 = command.decode('utf-8')
        except Exception as e:
            app.logger.warning(f"Failed to register command {command_type} '{command}' for plugin '{metadata.name}' (id={metadata.id}), decoding to UTF8 failed: {e}")
            continue

        if not command_utf8.startswith('/'):
            app.logger.warning(f"Failed to register command {command_type} '{command_utf8}' for plugin '{metadata.name}' (id={metadata.id}): Commands must start with leading '/'")
            continue

        app.logger.debug(f"Registering {command_type} {command} for plugin '{metadata.name}' (id={metadata.id})")
        if command_utf8 not in commands_container:
            commands_container[command_utf8] = set()
        commands_container[command_utf8].add(metadata.id)


def plugin_register_pre_command(m: oxenmq.Message):
    plugin_register_command(m, True)


def plugin_register_post_command(m: oxenmq.Message):
    plugin_register_command(m, False)


@needs_app_context
@log_exceptions
def plugin_get_user_permissions(m: oxenmq.Message):
    pass


@needs_app_context
@log_exceptions
def plugin_set_user_room_permissions(m: oxenmq.Message):
    """
    Limited to access/read/write for now
    arguments:
        - room_id / room_token
        - user_id / user_session_id
        - accessible/read/write = -1,0,1 (-1 is actively remove override in room for user)
    user room permissions will be changed as specified; omitting access/read/write means
    leave that value unchanged.
    """
    metadata = _require_plugin_conn_info(m.conn, need_user=True, msg_prefix="Failed to set user room permissions")
    if not metadata:
        return

    req_raw: Dict[bytes, bt_value] = bt_deserialize(m.dataview()[0])
    req                            = sogs.plugin.SetUserRoomPermissions.from_bencode(req_raw)
    try:
        if req.room_id is not None:
            room = Room(id=req.room_id)
        elif req.room_token is not None:
            room = Room(token=req.room_token.decode('ascii'))
        else:
            return bt_serialize("Must specify a room for user permissions change.")

        if req.user_id is not None:
            user = User(id=req.user_id, autovivify=False)
        elif req.user_session_id is not None:
            user = User(session_id=req.user_session_id.hex())
        else:
            return bt_serialize("Must specify a user for user permissions change.")

        new_perms = {}
        for key, value in (('accessible', req.accessible), ('read', req.read), ('write', req.write)):
            if value is not None:
                new_perms[key] = value

        if req.in_s is not None:
            set_at: float = time.time() + float(req.in_s)
            room.add_future_permission(user, at=set_at, **new_perms)
        else:
            room.set_permissions(user, mod=metadata.user, **new_perms)

    except NoSuchRoom as e:
        return bt_serialize("NoSuchRoom")
    except NoSuchUser as e:
        return bt_serialize("NoSuchUser")
    except Exception as e:
        app.logger.warning(f"Exception in plugin set perms: {e}")
        return bt_serialize("An error occurred with changing permissions.")

    return bt_serialize("OK")


@needs_app_context
@log_exceptions
def plugin_delete_message(m: oxenmq.Message):
    """
    For now, plugins can only delete messages they created.
    """
    metadata = _require_plugin_conn_info(m.conn, need_user=True, msg_prefix="Failed to delete message")
    if not metadata:
        return

    assert metadata.user
    req = bt_deserialize(m.dataview()[0])
    msg_ids = []
    if b'msg_ids' in req:
        msg_ids = req[b'msg_ids']
    if b'msg_id' in req:
        msg_ids.append(req[b'msg_id'])

    success = False
    try:
        with db.transaction():
            rowcount = query(
                """DELETE FROM message_details WHERE id IN :msg_ids AND "user" = :user""",
                msg_ids        = msg_ids,
                user           = metadata.user.id,
                bind_expanding = ['msg_ids'],
            )
            if rowcount:
                success = True
                app.logger.info(f"Deleted message with ids {msg_ids}")
    except Exception as e:
        app.logger.warning(f"Error: {e}")

    if not success:
        app.logger.warning(f"Failed to delete message with ids {msg_ids}")
        return bt_serialize({b'error': 'Message deletion failed due to DB error'})
    return bt_serialize({b'status': 'OK'})

@needs_app_context
@log_exceptions
def plugin_insert_message(m: oxenmq.Message) -> bytes:
    metadata: PluginMetadata   = plugin_conn_info[m.conn] if m.conn in plugin_conn_info else PluginMetadata()
    req                        = sogs.types.PluginInsertMessage.from_bencode(m.dataview()[0])
    sender                     = User(session_id=req.session_id.hex(), autovivify=True, touch=False)
    whisper_to: Optional[User] = None
    if req.whisper_to:
        try:
            whisper_to = User(id=req.whisper_to, autovivify=False)
        except Exception:
            app.logger.warning(f"{metadata.describe_str()} attempted to whisper a non-existing user: {req.whisper_to}")
            return bt_serialize({b'error': "NoSuchUser"})

    msg_id: MessageID = 0
    with db.transaction():
        # Lookup room to insert to
        try:
            room = Room(token=req.room_token.decode("utf-8"))
        except Exception:
            app.logger.warning(f"{metadata.describe_str()} attempted to post message to a non-existing room...")
            return bt_serialize({b'error': "NoSuchRoom"})

        p = Post(raw=req.message_data)
        if app.logger.level <= logging.DEBUG:
            log_line = (f"{metadata.describe_str()} inserting message\n"
                        f"  Room:      {room.name} (token={room.token})\n"
                        f"  Sender:    {sender.session_id} (id={sender.id}, username={p.username} using_id={sender.using_id})\n"
                        f"  Signature: {req.sig.hex()}\n"
                        f"  Message:   {p.text}\n")
            if whisper_to:
                log_line += f"  Whisper: {whisper_to.session_id} (id={whisper_to.id}, using_id={whisper_to.using_id})"
            if len(req.attachment_ids):
                log_line += f"  Files ({len(req.attachment_ids)}): {req.attachment_ids}"

        # Insert message to DB (plugin-inserted messages are not filtered, hence filtered=False)
        msg_id = room.insert_message(sogs.types.MessageInsert(
            unpadded_data    = sogs.utils.remove_session_message_padding(req.message_data),
            padded_data_size = len(req.message_data),
            filtered         = False,
            user_id          = sender.id,
            whisper_mods     = req.whisper_mods,
            sig              = req.sig,
            alt_id           = bytes.fromhex(sender.using_id) if sender.alt_id else None,
            whisper_to       = req.whisper_to,
        ))

        if len(req.attachment_ids):
            room.own_files(msg_id, req.attachment_ids, sender)
        if req.relay_to_plugins:
            relay_on_message_posted(msg_id)

    return bt_serialize({b'msg_id': msg_id})

@needs_app_context
@log_exceptions
def plugin_upload_file(m: oxenmq.Message):
    metadata: Optional[PluginMetadata] = _require_plugin_conn_info(m.conn, need_user=True, msg_prefix="Failed to upload file")
    if not metadata:
        return

    req = bt_deserialize(m.dataview()[0])
    with db.transaction():
        try:
            room = Room(token=req[b"room_token"].decode("ascii"))
        except Exception as e:
            app.logger.warning(f"Plugin attempted to upload file to inexistent room...")
            return bt_serialize({b'error': "NoSuchRoom"})

        # just passing this as bytes(req[b'file_contents']) was complaining about the type...?
        content = bytes(req[b'file_contents'])
        file_id = room.upload_file(content, metadata.user, filename=req[b'filename'].decode('utf-8'), lifetime=3600.0)

        url = f"{config.URL_BASE}/{room.token}/file/{file_id}"
        return bt_serialize({b'file_id': file_id, b"url": url})

@needs_app_context
@log_exceptions
def plugin_post_reactions(m: oxenmq.Message) -> bytes:
    """Post one or more reactions from this plugin to a single message."""
    metadata: Optional[PluginMetadata] = _require_plugin_conn_info(m.conn, need_user=True, msg_prefix="Failed to post reaction")
    if not metadata:
        return bt_serialize({b'error': 'Plugin did not register itself with a hello handshake'})
    if not metadata.user:
        return bt_serialize({b'error': f'Plugin {metadata.name} (id={metadata.id}) did not register with a Session ID'})

    try:
        req = bt_deserialize(m.dataview()[0])
        for key in (b'room_token', b'msg_id', b'reactions'):
            if not key in req:
                return bt_serialize({b'error': f"missing parameter {key}"})

        room = Room(token=req[b'room_token'].decode('ascii'))
        for reaction in req[b'reactions']:
            app.logger.debug(f"plugin_post_reactions, posting reaction to room")
            room.add_reaction(
                user             = metadata.user,
                msg_id           = typing.cast(int, req[b'msg_id']),
                reaction         = reaction.decode('utf-8'),
                relay_to_plugins = False,
            )
    except NoSuchRoom as e:
        app.logger.warning(f"Error: {e}")
        return bt_serialize({b'error': 'NoSuchRoom'})
    except Exception as e:
        app.logger.warning(f"Error: {e}")
        return bt_serialize({b'error': 'Something getting wrong'})

    return bt_serialize({b'status': 'OK'})


@needs_app_context
@log_exceptions
def plugin_remove_reactions(m: oxenmq.Message):
    """Remove all other reactions not from this plugin to a single message."""
    metadata: Optional[PluginMetadata] = _require_plugin_conn_info(m.conn, need_user=True, msg_prefix="Failed to post reaction")
    if not metadata:
        return bt_serialize({b'error': 'Plugin did not register itself with a hello handshake'})
    if not metadata.user:
        return bt_serialize({b'error': f'Plugin {metadata.name} (id={metadata.id}) did not register with a Session ID'})

    try:
        req = bt_deserialize(m.dataview()[0])
        for key in (b'room_token', b'msg_id', b'reactions'):
            if not key in req:
                return bt_serialize({b'error': f"missing parameter {key}"})

        room = Room(token=req[b'room_token'].decode('ascii'))
        for reaction in req[b'reactions']:
            app.logger.debug(f"plugin_remove_reactions, removing reactions from room")
            room.delete_other_reactions(
                user     = metadata.user,
                msg_id   = typing.cast(int, req[b'msg_id']),
                reaction = reaction.decode('utf-8'),
            )
    except NoSuchRoom as e:
        app.logger.warning(f"Error: {e}")
        return bt_serialize({b'error': 'NoSuchRoom'})
    except Exception as e:
        app.logger.warning(f"Error: {e}")
        return bt_serialize({b'error': 'Something getting wrong'})
    return bt_serialize({b'status': 'OK'})


# NOTE: this should be a list of IDs; if the plugin cares, it will have stored them.
#       or can fetch them
@needs_app_context
@log_exceptions
def on_messages_deleted(m: oxenmq.Message):
    pass

# TODO: this should be usable for reaction added/removed, not just added
@needs_app_context
@log_exceptions
def relay_on_reaction_posted(m: oxenmq.Message):
    req_raw: List[bytes] = m.data()
    req                  = sogs.types.ReactionPosted.from_bencode(req_raw[0])
    app.logger.debug(f"Reaction posted: {req}")

    plugin_ids: Dict[PluginID, PluginInfo] = get_relevant_plugins("subscribe = 1", room_id=req.room_id)
    for id in plugin_ids:
        plugin_info: PluginInfo = plugin_ids[id]
        if id in plugin_conns:
            app.logger.debug(f"Sending reaction to plugin '{plugin_info.name}' (id={id}, required={plugin_info.required})")
            o.omq.send(plugin_conns[id], "plugin.reaction_posted", *req_raw)


@needs_app_context
@log_exceptions
def relay_on_message_posted(msg_id: MessageID):
    # Fetch the message by message ID
    row = query(f"""
        SELECT message_details.*, uroom.token AS room_token FROM message_details
        JOIN rooms uroom ON message_details.room = uroom.id
        WHERE message_details.id = :msg_id
        """, msg_id=msg_id,).first()

    if row is None:
        return

    # Construct MessagePosted directly from row values
    message_posted = sogs.types.MessagePosted(
        id              = typing.cast(int,               row['id']),
        room            = typing.cast(int,               row['room']),
        room_token      = typing.cast(str,               row['room_token']).encode('utf-8'),
        user            = typing.cast(int,               row['user']),
        session_id      = bytes.fromhex(typing.cast(str, row['session_id'])),
        data            = typing.cast(bytes,             row['data']),
        data_size       = typing.cast(int,               row['data_size']),
        signature       = typing.cast(bytes,             row['signature']),
        posted          = typing.cast(float,             row['posted']),
        seqno           = typing.cast(int,               row['seqno']),
        seqno_creation  = typing.cast(int,               row['seqno_creation']),
        seqno_data      = typing.cast(int,               row['seqno_data']),
        filtered        = typing.cast(bool,              row['filtered']),
        whisper_mods    = typing.cast(bool,              row['whisper_mods']),
        seqno_reactions = typing.cast(int,               row['seqno_reactions']),
        edited          = typing.cast(float,             row['edited'])      if row['edited']     else None,
        whisper         = typing.cast(int,               row['whisper'])     if row['whisper']    else None,
        alt_id          = bytes.fromhex(typing.cast(str, row['alt_id']))     if row['alt_id']     else None,
        signing_id      = bytes.fromhex(typing.cast(str, row['signing_id'])) if row['signing_id'] else None,
        whisper_to      = bytes.fromhex(typing.cast(str, row['whisper_to'])) if row['whisper_to'] else None,
    )

    # Get relevant plugins for this room
    plugin_ids: Dict[PluginID, PluginInfo] = get_relevant_plugins("subscribe = 1", room_id=message_posted.room)
    if len(plugin_ids) == 0:
        return

    # Serialize and send to each plugin
    serialized: bytes = message_posted.to_bencode()
    for plugin_id in plugin_ids.keys():
        if plugin_id in plugin_conns:
            o.omq.send(plugin_conns[plugin_id], "plugin.message_posted", serialized)
