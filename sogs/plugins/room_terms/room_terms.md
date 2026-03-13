# Room Terms Plugin

Presents users with terms of agreement when requesting read access to a room. Users must react with
a configured emoji (default: thumbs up) to accept and gain access.

## Features

- Per-room configuration support
- Customizable terms, accept emoji, timeouts
- Configurable reply messages with escape sequences

## Setup

### Standalone

```bash
python3 -m sogs.plugins.room_terms --plugin_room_terms_ini_path <path/to/config.ini>
```

### UWSGI Mule

Add to `[uwsgi]` section:

```ini
mule = sogs.plugins.room_terms
env  = PLUGIN_ROOM_TERMS_INI_PATH=<path/to/config.ini>
```

## Registration

After starting, the plugin outputs an Ed25519 public key. Register it with SOGS:

```bash
python3 -m sogs --add-plugin <ed25519_pubkey_hex> \
                --plugin-name 'Room Terms Plugin' \
                --plugin-global true \
                --plugin-approver true \
                --plugin-required true \
                --plugin-subscribe true
```

## Configuration

See `room_terms.ini` for configuration options.

### Escape Sequences

- `\accept` - accept emoji
- `\write` - write timeout (seconds)
- `\retry` - retry timeout (seconds)
- `\r` - room name
- `\t` - room token
- `\@` - @mention of user
- `\p` - profile name
- `\n` - newline
- `\\` - literal backslash

## Configuration Priority

1. Room-specific: `[plugin_room_terms.room.<token>]`
2. Global wildcard: `[plugin_room_terms.room.*]`
3. Built-in defaults
