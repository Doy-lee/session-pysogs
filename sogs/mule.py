import traceback
import oxenmq
import time
import functools
import dataclasses
import typing
import oxenmq
import sogs.plugin

from oxenc import bt_deserialize, bt_serialize
from datetime import timedelta
from nacl.encoding import HexEncoder

from sogs.utils import bt_value
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
PluginID: typing.TypeAlias = int

# This is the uwsgi "mule" that handles things not related to serving HTTP requests:
# - it holds the oxenmq instance (with its own interface into sogs)
# - it handles cleanup jobs (e.g. periodic deletions)

# holds plugin_id -> plugin omq connection for connected plugins
plugin_conns: dict[PluginID, oxenmq.ConnectionID] = {}

@dataclasses.dataclass
class PluginMetadata:
    id:   PluginID    = 0
    name: str         = ''
    # A plugin manifests itself as a user in the community. When a plugin identifies itself to the
    # SOGS (e.g. starts hello handshake), the user account for the plugin gets added as a
    # moderator/admin. This field is the user info for said user representing the plugin.
    user: User | None = None

# holds oxenmq ConnectionID -> metadata (plugin_id, plugin session_id, etc.)
plugin_conn_info: dict[oxenmq.ConnectionID, PluginMetadata] = {}

# holds command -> plugin_id for commands registered by plugins
# key includes the prefix (default slash, may make configurable)
plugin_pre_commands:  dict[str, set[PluginID]] = {}
plugin_post_commands: dict[str, set[PluginID]] = {}

@dataclasses.dataclass
class PluginInfo:
    required: bool = False
    name:     str  = ''

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
def allow_conn(addr, pk, sn):
    with db.transaction():

        row = query("SELECT id FROM plugins WHERE auth_key = :key", key=pk).first()

        if row:
            app.logger.debug(f"Plugin connected: {HexEncoder.encode(pk)}")
            return oxenmq.AuthLevel.basic

    app.logger.warning(f"No plugin found with key: {HexEncoder.encode(pk)}")
    # TODO: user recognition auth
    return oxenmq.AuthLevel.denied


def admin_conn(addr, pk, sn):
    return oxenmq.AuthLevel.admin


def inproc_fail(connid, reason):
    raise RuntimeError(f"Couldn't connect mule to itself: {reason}")


@needs_app_context
@log_exceptions
def get_relevant_plugins(where_clause: str, room_id: int | None = None, room_token: str | None = None) -> dict[PluginID, PluginInfo] | None:
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
    result: dict[PluginID, PluginInfo] = {}
    with db.transaction():
        # Query global plugins that match the where_clause
        query_str = "SELECT id, name, required FROM plugins WHERE global = 1 AND " + where_clause
        rows      = query(query_str)
        for row in rows:
            required = False
            if row['required'] and row['required'] == 1:
                required = True
            result[row['id']] = PluginInfo(required=required, name=row['name'])

        # Resolve room_token to room_id if needed
        if room_token and not room_id:
            id_row = query("SELECT id FROM rooms WHERE token = :token", token=room_token).first()
            if id_row is None:
                app.logger.warning(f"filtering message for inexistent room with token: \"{room_token}\"??")
                return None
            room_id = id_row['id']

        # Query room-specific plugins and merge with global results
        query_str = "SELECT plugin, required FROM room_plugins WHERE room = :room_id AND " + where_clause
        rows = query(query_str, room_id=room_id)
        for row in rows:
            required = False
            if row['required'] and row['required'] == 1:
                required = True

            # Plugin ID is in 'plugin' column for room_plugins table
            plugin_id = row['plugin']
            if plugin_id in result:
                required = required or result[plugin_id]
            result[plugin_id] = required
    return result

# Commands from SOGS/uwsgi
@needs_app_context
@log_exceptions
def message_request(m: oxenmq.Message):
    """
    Called by SOGS when a user sends or edits a message
    TODO: handle edits
    """
    app.logger.debug("message_request called on mule")

    responded = False
    try:
        request_raw: dict[bytes, bt_value]            = bt_deserialize(m.dataview()[0])
        request:     sogs.plugin.FilterMessageRequest = sogs.plugin.FilterMessageRequest.from_bencode(request_raw)

        filter_resp = plugin_filter_message(m.data(), room_id=request.room_id)
        if filter_resp == "REJECT":
            return bt_serialize({"error": "Message rejected by filter plugin(s)"})
        elif filter_resp == "SILENT":
            request_raw[b"filtered"] = True

        # TODO: Make the command trigger character configurable
        command = ""
        msg     = Post(raw=request.message_data)
        if msg.text.startswith('/'):
            app.logger.debug(f"Processing slash command, pre-command phase")
            command = msg.text.split(' ')[0]

        if command:
            if not plugin_pre_message_commands(m.data(), request_raw, command):
                return bt_serialize({"ok": True})

        # TODO: pre-insertion plugin command, e.g. not a command and passed all filters,
        #       but for some other reason we don't want to insert it (or not yet).

        # TODO: handle edit message
        room = Room(id=request.room_id)
        msg_id = room.insert_message(request_raw)
        responded = True
        # manually reply so we don't hold up the worker longer than necessary
        m.reply(bt_serialize({"ok": True, "msg_id": msg_id}))

        plugin_post_message_commands(m.data(), request_raw, command)
        on_message_posted(msg_id)

        return
    except Exception as e:
        app.logger.warning(f"Exception handling new/edited message from sogs: {e}")
        if not responded:
            return bt_serialize({"error": f"{e}"})


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
        resp = o.omq.request_future(
            plugin_conns[plugin_id], "plugin.request_read", m.data(), request_timeout=timedelta(seconds=1)
        ).get()
    except TimeoutError as e:
        app.logger.warning(f"Timeout from plugin (id={plugin_id}) handling request_read")
        pass
    except Exception:
        # TODO: Should this fail the whole command?
        pass

    return retval


@log_exceptions
def reaction_posted(m: oxenmq.Message):
    on_reaction_posted(m)


@log_exceptions
def messages_deleted(m: oxenmq.Message):
    ids = bt_deserialize(m.data()[0])
    app.logger.debug(f"FIXME: mule -- message delete stub, deleted messages: {ids}")


@log_exceptions
def message_edited(m: oxenmq.Message):
    app.logger.debug("FIXME: mule -- message edited stub")


# Commands *to* pPlugins


@log_exceptions
def plugin_filter_message(data: bytes, room_id: int) -> sogs.plugin.FilterResponse:
    plugin_ids: dict[PluginID, PluginInfo] | None = get_relevant_plugins("approver = 1", room_id=room_id)
    if not plugin_ids:
        return sogs.plugin.FilterResponse.Accept

    app.logger.debug(f"Requesting message approval from {len(plugin_ids)} plugins.")

    # If the plugin is required for the filtering step and we don't have have an OMQ connection for
    # it to forward the message to for filtering, then we reject the message outright.
    for id in plugin_ids:
        if plugin_ids[id].required and id not in plugin_conns:
            return sogs.plugin.FilterResponse.Reject

    # Submit the message to the plugins and collect the async handles
    pending_requests: list[oxenmq.ResultFuture] = []
    for id in plugin_ids:
        pending_requests.append(o.omq.request_future(plugin_conns[id],
                                                     "plugin.filter_message",
                                                     data,
                                                     timeout=timedelta(seconds=1).seconds))

    # Await all the async handles
    silent = False
    for pending in pending_requests:
        try:
            response: list[bytes] = pending.get()
            if len(response) != 1:
                return sogs.plugin.FilterResponse.Reject

            resp_text: str = typing.cast(bytes, bt_deserialize(response[0])).decode('utf-8')
            if resp_text == sogs.plugin.FilterResponse.Accept:
                continue
            elif resp_text == sogs.plugin.FilterResponse.Silent:
                silent = True
                continue
            else:
                return sogs.plugin.FilterResponse.Reject
        except Exception as e:
            app.logger.warning(f"Plugin filter exception: {e}")
            return sogs.plugin.FilterResponse.Reject

    return sogs.plugin.FilterResponse.Silent if silent else sogs.plugin.FilterResponse.Accept


@needs_app_context
@log_exceptions
def plugin_message_commands(data, deserialized_data, command, pre_command: bool):
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
            app.logger.warning(
                f"Plugin (id={plugin_id}) registered to handle {command_type} {command} "
                f"but no longer in plugin_conns, somehow."
            )
            continue
        try:
            app.logger.debug(f"Giving {command_type} {command} to plugin (id={plugin_id})")
            resp = o.omq.request_future(
                plugin_conns[plugin_id],
                f"plugin.{command_type}",
                data,
                request_timeout=timedelta(seconds=0.2),
            ).get()
        except TimeoutError as e:
            app.logger.warning(f"Timeout from plugin (id={plugin_id}) handling {command_type} {command}")
            if pre_command:
                return False
        except Exception as e:
            app.logger.warning(
                f"Error from plugin (id={plugin_id}) handling {command_type} {command}, error: {e}"
            )
            if pre_command:
                return False

        should_continue = bt_deserialize(resp[0])
        app.logger.debug(f"{command_type} {command} response from plugin: {should_continue}")
        if pre_command and not should_continue:
            return False

    return True


def plugin_pre_message_commands(data, deserialized_data, command):
    return plugin_message_commands(data, deserialized_data, command, True)


def plugin_post_message_commands(data, deserialized_data, command):
    return plugin_message_commands(data, deserialized_data, command, False)


def setup_omq():
    omq = o.omq

    app.logger.debug("Mule setting up omq")
    if isinstance(config.OMQ_LISTEN, list):
        listen = config.OMQ_LISTEN
    elif config.OMQ_LISTEN is None:
        listen = []
    else:
        listen = [config.OMQ_LISTEN]
    for addr in listen:
        omq.listen(addr, curve=True, allow_connection=allow_conn)
        app.logger.info(f"OxenMQ listening on {addr}")

    # Internal socket for workers to talk to us:
    omq.listen(config.OMQ_INTERNAL, curve=False, allow_connection=admin_conn)

    # Periodic database cleanup timer:
    omq.add_timer(cleanup.cleanup, timedelta(seconds=cleanup.INTERVAL))

    # Commands other workers can send to us, e.g. for notifications of activity for us to know about
    plugin = omq.add_category("plugin", access_level=oxenmq.AuthLevel.basic)
    plugin.add_request_command("hello", plugin_hello)
    plugin.add_command("register_pre_commands", plugin_register_pre_command)
    plugin.add_command("register_post_commands", plugin_register_post_command)
    plugin.add_request_command("delete_message", plugin_delete_message)
    plugin.add_request_command("post_reactions", plugin_post_reactions)
    plugin.add_request_command("remove_reactions", plugin_remove_reactions)
    plugin.add_request_command("message", plugin_insert_message)
    plugin.add_request_command("upload_file", plugin_upload_file)
    plugin.add_request_command("set_user_room_permissions", plugin_set_user_room_permissions)
    worker = omq.add_category("worker", access_level=oxenmq.AuthLevel.admin)
    worker.add_request_command("message_request", message_request)
    worker.add_request_command("request_read", request_read)
    worker.add_command("messages_deleted", messages_deleted)
    worker.add_command("message_edited", message_edited)
    worker.add_command("reaction_posted", reaction_posted)

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

        row = query("SELECT id, name FROM plugins WHERE auth_key = :key", key=m.conn.pubkey).first()
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


def _require_plugin_conn_info(conn: oxenmq.ConnectionID, need_user: bool, msg_prefix: str) -> PluginMetadata | None:
    result: PluginMetadata | None = None
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
    metadata: PluginMetadata | None = _require_plugin_conn_info(m.conn, need_user=False, msg_prefix="Failed to register plugin")
    if not metadata:
        return

    command_type:       str                      = "pre_command" if pre_command else "post_command"
    commands_container: dict[str, set[PluginID]] = plugin_pre_commands if pre_command else plugin_post_commands

    req:      dict[bytes, oxenc.bt_value] = bt_deserialize(m.dataview()[0])
    commands: list[bytes]                 = typing.cast(list[bytes], req[b'commands'])
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

    req_raw: dict[bytes, bt_value] = bt_deserialize(m.dataview()[0])
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
                msg_ids=msg_ids,
                user=metadata.user.id,
                bind_expanding=['msg_ids'],
            )
            if rowcount:
                success = True
                app.logger.info(f"Deleted message with ids {msg_ids}")
    except Exception as e:
        app.logger.warning(f"Error: {e}")

    if not success:
        app.logger.warning(f"Failed to delete message with ids {msg_ids}")
        return bt_serialize({'error': 'Message deletion failed due to DB error'})
    return bt_serialize({'status': 'OK'})

@needs_app_context
@log_exceptions
def plugin_insert_message(m: oxenmq.Message):
    req = bt_deserialize(m.dataview()[0])

    # TODO: confirm plugin sessid is 25-blinded of plugin omq auth key?
    sender = User(session_id=req[b'session_id'].decode('ascii'), autovivify=True, touch=False)
    whisper_target = None
    if b'whisper_target' in req:
        try:
            whisper_target = User(
                session_id=req[b'whisper_target'].decode('ascii'), autovivify=False
            )
        except Exception:
            # invalid whisper target, plugin messed up?
            app.logger.warning(f"Plugin attempted to whisper an inexistent user...")
            return bt_serialize({'error': "NoSuchUser"})

    whisper_mods = req[b'whisper_mods']
    with db.transaction():
        try:
            room = Room(token=req[b"room_token"].decode("ascii"))
        except Exception:
            app.logger.warning(f"Plugin attempted to post message to inexistent room...")
            return bt_serialize({'error': "NoSuchRoom"})

        sig = req[b'sig']
        msg = req[b'message']
        plugin_str = sender.session_id + f" ({sender.using_id})"
        whisper_target_str = ''
        if whisper_target:
            whisper_target_str = whisper_target.session_id + f" ({whisper_target.using_id})"
        app.logger.debug(f"Posting message from plugin: {plugin_str}")
        app.logger.debug(f"signature: {sig}")
        app.logger.debug(f"Whisper target: {whisper_target_str}")
        p = Post(raw=msg)
        app.logger.debug(f"message text: {p.text}")
        app.logger.debug(f"message username: {p.username}")

        message_args = {
            b"room_id": room.id,
            b"room_token": room.token,
            b"room_name": room.name,
            b"user_id": sender.id,
            b"session_id": sender.session_id,
            b"message_data": msg,
            b"data_size": len(msg),
            b"sig": sig,
            b"filtered": False,
            b"is_mod": room.check_moderator(sender),
            b"whisper_mods": whisper_mods,
        }
        if whisper_target:
            message_args[b"whisper_to"] = whisper_target.id
        if sender.alt_id:
            message_args[b"alt_id"] = sender.using_id


        msg_id = room.insert_message(message_args)

        if b"files" in req:
            app.logger.debug(f"associating files {req[b'files']} with msg {msg_id}")
            room._own_files(msg_id, req[b"files"], sender)

        if b'no_plugins' not in req:
            on_message_posted(msg_id)

        return bt_serialize({'msg_id': msg_id})


@needs_app_context
@log_exceptions
def plugin_upload_file(m: oxenmq.Message):
    metadata: PluginMetadata | None = _require_plugin_conn_info(m.conn, need_user=True, msg_prefix="Failed to upload file")
    if not metadata:
        return

    req = bt_deserialize(m.dataview()[0])
    with db.transaction():
        try:
            room = Room(token=req[b"room_token"].decode("ascii"))
        except Exception as e:
            app.logger.warning(f"Plugin attempted to upload file to inexistent room...")
            return bt_serialize({'error': "NoSuchRoom"})

        # just passing this as bytes(req[b'file_contents']) was complaining about the type...?
        content = bytes(req[b'file_contents'])
        file_id = room.upload_file(content, metadata.user, filename=req[b'filename'].decode('utf-8'), lifetime=3600.0)

        url = f"{config.URL_BASE}/{room.token}/file/{file_id}"
        return bt_serialize({'file_id': file_id, "url": url})

@needs_app_context
@log_exceptions
def on_message_posted(msg_id):
    app.logger.warn(f"Calling on_message_posted with id={msg_id}")
    msg = None
    for row in query(
        f"""
        SELECT message_details.*, uroom.token AS room_token FROM message_details
        JOIN rooms uroom ON message_details.room = uroom.id
        WHERE message_details.id = :msg_id
        """,
        msg_id=msg_id,
    ):
        app.logger.warn("Message details:")
        for key in row.keys():
            app.logger.warn(f"{key}: {row[key]}")
        msg = {x: row[x] for x in row.keys()}

    if msg is None:
        return

    for key in msg.keys():
        if msg[key] is None:
            msg[key] = ""
    msg['posted'] = str(msg['posted'])

    plugin_ids = get_relevant_plugins("subscribe = 1", room_id=msg['room'])
    serialized = bt_serialize(msg)
    for plugin_id in plugin_ids.keys():
        o.omq.send(plugin_conns[plugin_id], "plugin.message_posted", serialized)


@needs_app_context
@log_exceptions
def plugin_post_reactions(m: oxenmq.Message):
    """
    Post one or more reactions from this plugin to a single message.
    """
    try:
        req = bt_deserialize(m.dataview()[0])
        for key in (b'room_token', b'msg_id', b'reactions'):
            if not key in req:
                return bt_serialize({'error': f"missing parameter {key}"})

        room = Room(token=req[b'room_token'].decode('ascii'))
        for reaction in req[b'reactions']:
            app.logger.debug(f"plugin_post_reactions, posting reaction to room")
            room.add_reaction(
                plugin_conn_info[m.conn]['user'],
                req[b'msg_id'],
                reaction.decode('utf-8'),
                send_to_plugins=False,
            )
    except NoSuchRoom as e:
        app.logger.warning(f"Error: {e}")
        return bt_serialize({'error': 'NoSuchRoom'})
    except Exception as e:
        app.logger.warning(f"Error: {e}")
        return bt_serialize({'error': 'Something getting wrong'})
    return bt_serialize({'status': 'OK'})


@needs_app_context
@log_exceptions
def plugin_remove_reactions(m: oxenmq.Message):
    """
    Remove all other reactions not from this plugin to a single message.
    """
    try:
        req = bt_deserialize(m.dataview()[0])
        for key in (b'room_token', b'msg_id', b'reactions'):
            if not key in req:
                return bt_serialize({'error': f"missing parameter {key}"})

        room = Room(token=req[b'room_token'].decode('ascii'))
        for reaction in req[b'reactions']:
            app.logger.debug(f"plugin_remove_reactions, removing reactions from room")
            room.delete_other_reactions(
                plugin_conn_info[m.conn]['user'],
                req[b'msg_id'],
                reaction.decode('utf-8'),
            )
    except NoSuchRoom as e:
        app.logger.warning(f"Error: {e}")
        return bt_serialize({'error': 'NoSuchRoom'})
    except Exception as e:
        app.logger.warning(f"Error: {e}")
        return bt_serialize({'error': 'Something getting wrong'})
    return bt_serialize({'status': 'OK'})


# TODO: this should be usable for reaction added/removed, not just added
@needs_app_context
@log_exceptions
def on_reaction_posted(m: oxenmq.Message):
    msg_dict = bt_deserialize(m.dataview()[0])
    app.logger.warn(f"on_reaction_posted, reaction:\n{msg_dict}")
    plugin_ids: dict[PluginID, PluginInfo] = get_relevant_plugins("subscribe = 1", room_id=msg_dict[b'room_id'])
    for key in plugin_ids:
        if key in plugin_conns:
            plugin_info: PluginInfo = plugin_ids[key]
            app.logger.warn(f"Sending reaction to plugin '{plugin_info.name}' (id={key}, required={plugin_info.name})")
            o.omq.send(plugin_conns[plugin_id], "plugin.reaction_posted", m.data())


# NOTE: this should be a list of IDs; if the plugin cares, it will have stored them.
#       or can fetch them
@needs_app_context
@log_exceptions
def on_messages_deleted(m: oxenmq.Message):
    pass
