# API Debug Plugin

Development plugin for exploring and debugging the SOGS plugin API. Whispers API payloads to users
to show what data is received at each endpoint.

This plugin helps you explore and debug the SOGS plugin API by showing you exactly what data is
received at each endpoint.

## Commands

The following commands are available:

- `/debug_pre_command <true|false>` - Triggers debug output for pre-command phase. Returns
  true/false to accept/reject the message.
- `/debug_post_command` - Triggers debug output for post-command phase
- `/help` - Shows this help message with available commands and debug capabilities

## Events

Events that occur in a room will be reflected into the room:

- **Message Posted** - Triggered when any user posts a message to the room
- **Reaction Posted** - Triggered when any user adds a reaction to a message
- **Request Read** - Triggered when you open/read a room (once per room session)
- **Filter** - Triggered before any message is inserted - shows raw message data before processing
- **Pre/Post Message Command** - Triggered before/after the message is posted

All payloads are whispered to you privately with descriptions of when they were triggered!

## Setup

### Standalone

Setup the .ini config file (see the configuration section below for more details) with the desired
parameters and then you can run the plugin standalone:

```bash
python3 -m sogs.plugins.api_debug --plugin_api_debug_ini_path <path/to/config.ini>
```

### UWSGI Mule

Alternatively you can run the plugin alongside the SOGS server as a UWSGI mule. In your UWSGI .ini
config file, add to the [uwsgi] section:

```ini
[uwsgi]
mule = sogs.plugins.api_debug:entry_point
env  = PLUGIN_API_DEBUG_INI_PATH=<path/to/plugin/config.ini>
```

Note that the plugin can be parameterized via the following methods:

- Pass the `--plugin_api_debug_ini_path` flag to the python invocation
- Set the `PLUGIN_API_DEBUG_INI_PATH` environment variable
- Otherwise expects "api_debug.ini" in the current working directory if omitted

## Registration

After starting, the plugin outputs an Ed25519 public key. Register it with SOGS:

```bash
python3 -m sogs --add-plugin <ed25519_pubkey_hex> \
                --plugin-name 'API Debug Plugin' \
                --plugin-global true \
                --plugin-approver true \
                --plugin-required true \
                --plugin-subscribe true
```

## Usage

1. Send any message to see the message_posted payload
2. Add a reaction to see the reaction_posted payload
3. Use `/debug_pre_command true` or `/debug_pre_command false` to control whether the message is
   accepted or rejected

See `api_debug.ini` for configuration options.
