import configparser
import pathlib
import types

from nacl.public  import PrivateKey
from nacl.signing import SigningKey

import sogs.config
from sogs.web import app

def get_plugin_privkey(key_file: str) -> SigningKey | None:
    privkey_bytes: bytes | None = None
    dest_path                   = pathlib.Path(key_file)
    try:
        privkey_bytes = dest_path.read_bytes()
    except FileNotFoundError:
        pass

    if not privkey_bytes:
        privkey_bytes = PrivateKey.generate().encode()
        _             = dest_path.write_bytes(privkey_bytes)

    result = SigningKey(privkey_bytes)
    return result


def run_captcha_plugin(db: types.ModuleType | None = None, ini: str = "captcha.ini"):
    cp                    = configparser.ConfigParser()
    files_read: list[str] = cp.read(ini)

    app.logger.info(f"Loading captcha plugin config from {ini}")
    if len(files_read) != 1:
        app.logger.warning(f"Captcha .ini config file does not exist, stopping. File was: {ini}")
        return

    # Mandatory configs fields
    key_file:        str               = cp.get('plugin', 'key_file', fallback="captcha_x25519")
    captcha_privkey: SigningKey | None = get_plugin_privkey(key_file)
    if not captcha_privkey:
        app.logger.error(f"Captcha config file field 'key_file' is missing, aborting. File was: {ini}")
        return

    sogs_pubkey_hex: str | None = cp.get('sogs', 'sogs_pubkey_hex', fallback=None)
    if not sogs_pubkey_hex:
        app.logger.error(f"Captcha config file field 'sogs_pubkey_hex' is missing. File was: {ini}")
        return

    if sogs_pubkey_hex.startswith("0x"):
        sogs_pubkey_hex = sogs_pubkey_hex[2:]

    sogs_pubkey: bytes = b''
    try:
        sogs_pubkey = bytes.fromhex(sogs_pubkey_hex)
    except Exception as e:
        app.logger.error(f"Captcha config file field 'sogs_pubkey_hex' was not a hex string: {sogs_pubkey_hex}")
        return

    # Overridable config fields
    captcha_name:            str        = cp.get   ('plugin', 'name',            fallback="CAPTCHA")
    captcha_retry_limit:     int | None = cp.getint('plugin', 'retry_limit',     fallback=None)
    captcha_write_timeout:   int | None = cp.getint('plugin', 'write_timeout',   fallback=None)
    captcha_refresh_timeout: int | None = cp.getint('plugin', 'refresh_timeout', fallback=None)
    captcha_retry_timeout:   int | None = cp.getint('plugin', 'retry_timeout',   fallback=None)
    sogs_address:            str        = cp.get   ('sogs',   'sogs_address',    fallback=sogs.config.OMQ_LISTEN)

    # Instantiate the plugin
    from sogs.plugins.captcha_plugin import CaptchaPlugin
    plugin = CaptchaPlugin(sogs_address    = sogs_address,
                           sogs_pubkey     = sogs_pubkey,
                           ed_privkey      = captcha_privkey.encode(),
                           ed_pubkey       = captcha_privkey.verify_key.encode(),
                           display_name    = captcha_name)
    if captcha_retry_limit:
        plugin.retry_limit = captcha_retry_limit
    if captcha_retry_timeout:
        plugin.retry_timeout = captcha_retry_timeout
    if captcha_refresh_timeout:
        plugin.refresh_timeout_s = captcha_refresh_timeout
    if captcha_write_timeout:
        plugin.write_timeout = captcha_write_timeout

    if db is not None:
        from sogs.db import query
        with db.transaction():
            query("INSERT OR IGNORE INTO plugins (auth_key, global, approver, subscribe) VALUES (:key, 1, 1, 1)", key=SigningKey(plugin.x_pubkey).encode())

    plugin.run()
