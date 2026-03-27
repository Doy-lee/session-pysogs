from . import config
from . import crypto
from .postfork import postfork

import os
import logging
import importlib.resources
import sqlalchemy.engine.base
import sqlalchemy.engine
import typing
import sys

from sys import version_info as python_version
from sqlalchemy.sql.expression import bindparam
from werkzeug.local import LocalProxy
from typing import Optional, List, Tuple

HAVE_FILE_ID_HACKS = False
# roomid => (max, offset).  Max is the highest message id that was in the old table; offset is the
# value we add to ids <= that max to calculate the new database message id.
ROOM_IMPORT_HACKS = {}

engine: Optional[sqlalchemy.engine.base.Engine] = None
engine_initial_pid, metadata = None, None

def get_conn() -> sqlalchemy.engine.Connection:
    """Gets a connection from the database engine connection pool.  This is not intended to be used
    by flask endpoints: they should use web.appdb instead (which calls this upon first use)."""
    assert engine
    return engine.connect()

def query(query, *, dbconn=None, bind_expanding=None, **params):
    """Executes a query containing :param style placeholders (regardless of the actual underlying
    database placeholder style), binding them using the given params keyword arguments.

    Note that, if the query contains a literal : it must be escaped as \\:

    For example:

        rows = db.query(
            "SELECT * FROM table1 WHERE name = :name AND age >= :age",
            name="Joe",
            age=25)

    See sqlalchemy.text for details.

    bind_expanding can be passed a sequence of bind names that are "expanding" to a tuple, most
    commonly used to bind and expand the RHS of a `x IN :x` clause.

    Can execute on a specific connection by passing it as dbconn; if omitted, uses web.appdb.  (Note
    that dbconn *cannot* be used as a placeholder bind name).
    """

    if dbconn is None:
        from . import web

        dbconn = web.appdb

    q = sqlalchemy.text(query)

    if bind_expanding:
        q = q.bindparams(*(bindparam(c, expanding=True) for c in bind_expanding))

    return dbconn.execute(q, params)


# Begins a (potentially nested) transaction.  Takes an optional connection; if omitted uses
# web.appdb.
def transaction(dbconn: Optional[LocalProxy[sqlalchemy.engine.base.Connection]] = None) -> sqlalchemy.engine.NestedTransaction:
    if dbconn is None:
        from . import web
        dbconn = web.appdb
    return dbconn.begin_nested()

have_returning = True


def insert_and_get_pk(insert, _pk, *, dbconn=None, **params):
    """
    Performs an insert and returns the value of the primary key by appending a RETURNING clause, if
    supported, and otherwise falling back to using .lastrowid.

    Takes the query, primary key column name, and any parameters to bind

    Can optionally take the database connection by passing as a dbconn parameter (note that you may
    not use "dbconn" as a bind parameter).  If omitted uses web.appdb.
    """

    if have_returning:
        insert += f" RETURNING {_pk}"

    result = query(insert, dbconn=dbconn, **params)
    if have_returning:
        return result.first()[0]
    return result.lastrowid


def insert_and_get_row(insert, _table, _pk, *, dbconn=None, **params):
    """
    Performs an insert and returned the row by appending a `RETURNING *` clause, if supported, and
    otherwise fetching the row immediately after the insertion.

    Takes the query, table name, and primary key column name (the latter two are needed for the
    SELECT query when the db doesn't support RETURNING), and any parameters to bind.

    Can optionally take the database connection by passing as a dbconn parameter (note that you may
    not use "dbconn" as a bind parameter).  If omitted uses web.appdb.
    """

    if have_returning:
        insert += " RETURNING *"
        return query(insert, dbconn=dbconn, **params).first()

    with transaction(dbconn):
        pkval = insert_and_get_pk(insert, _pk, dbconn=dbconn, **params)
        return query(f"SELECT * FROM {_table} WHERE {_pk} = :pk", pk=pkval).first()


def read_schema(flavour: str):
    if python_version >= (3, 9):
        with (importlib.resources.files('sogs') / f"schema.{flavour}").open(
            "r", encoding='utf-8', errors='strict'
        ) as f:
            return f.read()
    else:
        return importlib.resources.read_text('sogs', f"schema.{flavour}")


def database_init(create=None, upgrade=True):
    """
    Perform database initialization: constructs the schema, if necessary, and performs any required
    migrations.  This does so using its *own* database connection, and is intended to be called
    during initialization *before* forking happens during uwsgi startup.

    create -- if True then we require that the database tables not already exist and raise an error
    if they do; if False then we raise an error if the database looks empty.  If None/unspecified
    then we will automatically create tables if required.

    upgrade -- if True (or omitted) then we allow database upgrades to run, if False then we throw
    an exception if any database upgrade is required.

    Returns true if database creation or upgrades were performed.
    """

    global engine, metadata

    metadata.clear()
    metadata.reflect(bind=engine, views=True)

    conn = get_conn()

    created, migrated = False, False
    if 'messages' not in metadata.tables:
        if create is not None and not create:
            raise RuntimeError(
                "Empty database connection and database initialization disabled; aborting"
            )

        logging.warning("No database detected; creating new database schema")
        if engine.name == "sqlite":
            conn.connection.executescript(read_schema('sqlite'))
        elif engine.name == "postgresql":
            cur = conn.connection.cursor()
            cur.execute(read_schema('pgsql'))
            cur.close()
        else:
            err = f"Don't know how to create the database for {engine.name}"
            logging.critical(err)
            raise RuntimeError(err)

        created = True
        metadata.clear()
        metadata.reflect(bind=engine, views=True)

        if 'messages' not in metadata.tables:
            msg = (
                "Critical error: SQL schema creation failed; "
                f"tables: {', '.join(metadata.tables.keys())}"
            )
            logging.critical(msg)
            raise RuntimeError(msg)
    elif create:
        raise RuntimeError("Unable to initialize database: tables already exist")

    from . import migrations

    migrated = migrations.migrate(conn, check_only=not upgrade and not created)
    if migrated:
        metadata.clear()
        metadata.reflect(bind=engine, views=True)

    # Make sure the system admin users exists
    create_admin_user(conn)

    return created or migrated


def create_admin_user(dbconn):
    """
    We create a dummy user (with id 0) for system tasks such as changing moderators from
    command-line, and give it the server's x25519 pubkey (with ff prepended, *not* 05) as a fake
    default session_id.
    """
    query(
        """
        INSERT INTO users (id, session_id, moderator, admin, visible_mod)
            VALUES (0, :sid, TRUE, TRUE, FALSE)
        ON CONFLICT (id) DO UPDATE
            SET session_id = :sid, moderator = TRUE, admin = TRUE, visible_mod = FALSE
        """,
        sid="ff" + crypto.server_pubkey_hex,
        dbconn=dbconn,
    )


def _fix_plugin_keys(dbconn):
    """Verify and fix plugin x25519 keys derived from ed25519 keys."""
    import nacl.bindings as sodium
    from .db import query
    config.logger.info("Verifying plugin keys...")
    plugins = query("SELECT id, name, ed_key, x_key FROM plugins", dbconn=dbconn).all()
    if not plugins:
        return

    # Build table output as a single string
    table_lines: List[str] = [
        f"{'ID':<5} {'Name':<32} {'Status':<10}",
        "-" * 80,
    ]
    fixed_count                              = 0
    ok_count                                 = 0
    invalid_plugins: List[Tuple[str, bytes]] = []

    for index, plugin in enumerate(plugins):
        plugin_id    = typing.cast(int, plugin['id'])
        name         = typing.cast(str, plugin['name']) or f"Plugin {plugin_id}"
        ed_key       = typing.cast(bytes, plugin['ed_key'])
        stored_x_key = typing.cast(bytes, plugin['x_key'])

        derived_x_key: bytes = b''
        try:
            derived_x_key = sodium.crypto_sign_ed25519_pk_to_curve25519(ed_key)
        except Exception as e:
            pass

        ed_chunks: List[str] = [ed_key[i:i+4].hex() for i in range(0, 32, 4)]
        if len(derived_x_key) == 0:
            table_lines.append(f"{plugin_id:<5} {name:<32} {'⛔ INVALID':<10}")
            table_lines.append(f"        Ed25519: {' '.join(ed_chunks)} (Ed25519 key is not valid)")
            invalid_plugins.append((name, ed_key))
        else:
            # Format keys in 4-byte chunks for display
            stored_chunks:  List[str] = [stored_x_key[i:i+4].hex() for i in range(0, 32, 4)]
            derived_chunks: List[str] = [derived_x_key[i:i+4].hex() for i in range(0, 32, 4)]

            if stored_x_key == derived_x_key:
                table_lines.append(f"{plugin_id:<5} {name:<32} {'✅ OK':<10}")
                table_lines.append(f"        Ed25519: {' '.join(ed_chunks)}")
                table_lines.append(f"        X25519:  {' '.join(stored_chunks)}")
                ok_count += 1
            else:
                table_lines.append(f"{plugin_id:<5} {name:<32} {'⚠️ MISMATCH':<10}")
                table_lines.append(f"        Ed25519 (stored):    {' '.join(ed_chunks)}")
                table_lines.append(f"        X25519  (stored):    {' '.join(stored_chunks)}")
                table_lines.append(f"        X25519 (derived):    {' '.join(derived_chunks)}")

                # Fix the key
                with transaction(dbconn):
                    query("UPDATE plugins SET x_key = :x_key WHERE id = :id", dbconn=dbconn, x_key=derived_x_key, id=plugin_id)
                table_lines.append(f"        FIXED: Updated x_key to derived value")
                fixed_count += 1

    table_lines.append("-" * 80)
    table_lines.append(f"Summary: {fixed_count} plugin(s) fixed, {ok_count} plugin(s) OK, {len(invalid_plugins)} plugin(s) invalid")

    # Log the complete table as one multi-line message
    config.logger.info("\n" + "\n".join(table_lines))

    if len(invalid_plugins):
        config.logger.error("\nThe following plugin(s) have invalid Ed25519 public keys registered. These plugins need to\n"
               "regenerate their keys and re-register to the SOGS server\n")
        for it in invalid_plugins:
            config.logger.error(f"  Plugin '{it[0]}': python3 -m sogs --delete-plugin {it[1].hex()}")
        sys.exit(1)

def init_engine(*args, **kwargs):
    """
    Initializes db.engine.  This is called automatically during import of this submodule when
    running as a uwsgi app, but needs to be called manually otherwise before any database-using code
    is invoked.

    This can also be called to reinitialize db.engine, but that usage should be confined to the test
    suite.

    Keyword arguments:
    sogs_preinit - a callable to invoke after setting up `engine` but before calling
    `database_init()`.
    sogs_skip_init - optional boolean; if specified and True then database_init() will not be
    called; the caller must call it manually before any other database functions are used.
    """
    global engine, engine_initial_pid, metadata, have_returning

    if engine is not None:
        engine.dispose()

    skip_init = kwargs.pop('sogs_skip_init', False)
    preinit = kwargs.pop('sogs_preinit', None)

    if not len(args) and not len(kwargs):
        if config.DB_URL == 'defer-init':
            return
        args = (config.DB_URL,)

    exec_opts_args = {}
    if args[0].startswith('postgresql'):
        exec_opts_args['isolation_level'] = 'READ COMMITTED'
    else:
        # SQLite's Python code is seriously broken, so we have to force off autocommit mode and turn
        # on driver-level autocommit (which we do below).
        exec_opts_args['autocommit'] = False

    engine = sqlalchemy.create_engine(*args, **kwargs).execution_options(**exec_opts_args)
    assert engine

    engine_initial_pid = os.getpid()
    metadata = sqlalchemy.MetaData()

    if engine.name == "sqlite":
        import sqlite3

        if sqlite3.sqlite_version_info < (3, 25, 0):
            raise RuntimeError(
                f"SQLite3 library version {'.'.join(sqlite3.sqlite_version_info)} "
                "is too old for pysogs (3.25.0+ required)!"
            )

        have_returning = sqlite3.sqlite_version_info >= (3, 35, 0)

        @sqlalchemy.event.listens_for(engine, "connect")
        def sqlite_fix_connect(dbapi_connection, connection_record):
            # disable pysqlite's emitting of the BEGIN statement entirely.
            # also stops it from emitting COMMIT before any DDL.
            dbapi_connection.isolation_level = None
            # Enforce foreign keys.  It is very sad that this is not default.
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        @sqlalchemy.event.listens_for(engine, "begin")
        def do_begin(conn):
            # emit our own BEGIN
            conn.exec_driver_sql("BEGIN IMMEDIATE")

    else:
        have_returning = True

        # rooms.token is a 'citext' (case-insensitive text), which sqlalchemy doesn't recognize out
        # of the box.  Map it to a plain TEXT which is good enough for what we need (if we actually
        # needed to generate this wouldn't suffice: we'd have to use something like the
        # sqlalchemy-citext module).
        from sqlalchemy.dialects.postgresql.base import ischema_names

        if 'citext' not in ischema_names:
            ischema_names['citext'] = ischema_names['text']

    if preinit:
        preinit()

    if not skip_init:
        _ = database_init()
        from . import utils
        info_lines = utils.pretty_format_key_value_list([
            ("SOGS Public Key", crypto.server_pubkey_hex),
            ("Listen Address", config.OMQ_LISTEN),
            ("Database", config.DB_URL),
        ])
        config.logger.info("\n" + "\n".join(info_lines))
        _fix_plugin_keys(engine.connect())


if config.RUNNING_AS_APP:
    init_engine()


@postfork
def reset_db_postfork():
    """Clear any connections from the engine after forking because they aren't shareable."""
    if engine is None or os.getpid() == engine_initial_pid:
        return
    engine.dispose()
