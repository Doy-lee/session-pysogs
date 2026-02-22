# Plugins

PySOGS supports plugins that extend server functionality. Plugins run as separate Python processes and
communicate with PySOGS via OxenMQ, allowing them to filter messages, handle user verification,
respond to events, and more.

## Installation

All plugins follow the same general installation pattern:

### Step 1: Choose a Deployment Method

**Option A: UWSGI Mule (Recommended for Production)**

Add the plugin as a mule in your UWSGI configuration file:

```ini
[uwsgi]
mule = sogs.plugins.<plugin_name>:entry_point
env  = PLUGIN_<PLUGIN_NAME>_INI_PATH=<path/to/config.ini>
```

**Option B: Standalone (Development/Debugging)**

Run the plugin directly as a Python module:

```bash
cd session-pysogs
python3 -m sogs.plugins.<plugin_name> --plugin_<plugin_name>_ini_path <path/to/config.ini>
```

### Step 2: Configure the Plugin

Create or update your `.ini` configuration file with the plugin settings. This can be in your main
PySOGS `.ini` file or a separate file.

At minimum, you need to configure the PySOGS connection in the `[plugin]` section:

```ini
[plugin]
; Your PySOGS public key (found in room URLs)
sogs_pubkey_hex = xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

; The OxenMQ address PySOGS is listening on
sogs_address = tcp://127.0.0.1:22028
```

Each plugin has its own configuration section (e.g., `[plugin_emoji_captcha]`) with plugin-specific
settings. See the plugin source files for detailed configuration options.

### Step 3: Enable OxenMQ in PySOGS

Ensure your PySOGS configuration has OxenMQ enabled so plugins can connect:

```ini
[net]
omq_listen = tcp://127.0.0.1:22028
```

### Step 4: Generate Plugin Keys

Start the plugin once (standalone or via UWSGI). On first run, it will generate an Ed25519 keypair
and output the public key:

```
[PLUGIN NAME] Plugin loaded:
  SOGS Address (Pubkey):      tcp://127.0.0.1:22028 (cccc...cccc)
  Display Name:               Plugin Name
  Ed25519 Pubkey:             aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
  X25519 Pubkey:              bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
  Session Account (Blind-15): 15xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

### Step 5: Register the Plugin with PySOGS

Use the Ed25519 public key from the previous step to register the plugin:

```bash
python3 -m sogs --add-plugin       <ed25519_pubkey_hex> \
                --plugin-name      'Plugin Name' \
                --plugin-global    true \
                --plugin-approver  true \
                --plugin-required  true \
                --plugin-subscribe true
```

#### Plugin Registration Flags

TODO: Plugins should internally have a name set by the plugin developer which it will shows up as
in the slash menu authoritatively rather than allow users to set this arbitrarily. The plugin should
then register this name on the "hello" handshake.

| Flag | Description |
|------|-------------|
| `--plugin-name` | Name of the plugin to register to the PySOGS instance. Cosmetic only for operator book-keeping |
| `--plugin-global` | When `true`, the plugin is enabled for all rooms on the server. When `false`, the plugin must be explicitly enabled per-room. |
| `--plugin-approver` | When `true`, the plugin will receive notifications for new messages which it can choose to filter, accept or deny the message. |
| `--plugin-required` | When `true`, this plugin must be online, connected to the instance and approve incoming messages. If the plugin is not connected to the PySOGS all messages are rejected until the plugin is connected. Ignored if `approver` is false. |
| `--plugin-subscribe` | When `true`, the plugin receives notifications after messages and reactions are posted from PySOGS. |

#### Per-Room vs Global Registration

**Global registration** (recommended for most plugins):
```bash
python3 -m sogs --add-plugin <pubkey> --plugin-global true
```
The plugin is automatically active in all rooms.

**Per-room registration**:
```bash
# Register plugin without global access
python3 -m sogs --add-plugin <pubkey> --plugin-global false

# Enable for specific rooms
python3 -m sogs --room myroom --add-room-plugin <pubkey>
python3 -m sogs --room anotherroom --add-room-plugin <pubkey>
```
The plugin is only active in explicitly enabled rooms.

## Available Plugins

See the documentation in the plugin file for more detailed configuration options.

### Emoji CAPTCHA

**File:** `sogs/plugins/emoji_captcha.py`

Generates visual CAPTCHA challenges for users joining rooms. Users must react with the correct emoji
shown in an image to gain read/write permissions. Supports configurable retry limits and timeouts.

Requires removing default read/write permissions from rooms:
```bash
sogs --rooms='*' --remove-perms "rw"
```

### SOGS Filter

**File:** `sogs/plugins/sogs_filter.py`

Provides message filtering for profanity and arbitrarily defined alphabets by specifying regex
patterns. Can automatically reject messages or reply with warnings. Supports per-room configuration
overrides and customizable filter responses.

### Room Terms

**File:** `sogs/plugins/room_terms.py`

Presents users with terms of agreement when joining a room. Users must react with an accept emoji
(default: thumbs up) to agree and gain access. Supports per-room terms messages and configurable
retry timeouts.

Requires removing default read/write permissions from rooms:
```bash
sogs --rooms='*' --remove-perms "rw"
```

### API Debug

**File:** `sogs/plugins/api_debug.py`

A development/debugging tool that echoes PySOGS API payloads back to users via whispers. Useful for
understanding the plugin API and debugging plugin development. Provides slash commands for testing
pre/post message handling.
