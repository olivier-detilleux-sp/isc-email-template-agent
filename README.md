# SailPoint Email Template Agent

Local agent that prepares and publishes ISC email templates for a tenant. It
loads a versioned style library, translates subjects and bodies with the
Cursor SDK when needed, applies the tenant logo and colors from the active
SailPoint CLI environment, then asks before publishing.

On a new computer, prepare and publish use the masters already stored in
`data/masters/<language>/`. Run `./agent --init` only to generate a language
that is not in that directory yet.

## Installation

These steps are enough to run the agent on a new computer.

### 1. Prerequisites

- Python 3.11 or newer (`python3 --version`)
- Git
- The SailPoint CLI, `sail`
  - macOS: `brew tap sailpoint-oss/tap && brew install sailpoint-cli`
  - Windows and Linux: installers on
    https://github.com/sailpoint-oss/sailpoint-cli/releases
- A SailPoint ISC tenant, and a Personal Access Token for a user who can read
  branding and create notification templates
- A Cursor User API key. Generating a new master language calls Cursor, so
  the key is required before `./agent --init`.

### 2. Clone and install Python dependencies

```bash
git clone https://github.com/olivier-detilleux-sp/isc-email-template-agent.git
cd isc-email-template-agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

On Windows, create the virtual environment the same way and then run
`.venv\Scripts\python src\agent.py` instead of `./agent`.

### 3. Connect the SailPoint CLI

Create one named environment per tenant. Use the host family that matches
the tenant:

- Customer, production, or sandbox:
  `https://{tenant}.identitynow.com` and
  `https://{tenant}.api.identitynow.com`
- Demo / avant-vente:
  `https://{tenant}.identitynow-demo.com` and
  `https://{tenant}.api.identitynow-demo.com`

```bash
sail environment create my-tenant
sail set auth pat
sail set pat
sail environment use my-tenant
sail environment show
```

`sail environment create` asks for the tenant URL and the API URL.
`sail set pat` asks for the PAT client id and client secret. Do not commit
those values, `~/.sailpoint/config.yaml`, or a `config.json`.

`sail environment show` must succeed before you run the agent. The agent
uses that environment for every API call. Pass `--env my-tenant` to target
one environment without changing the active one.

### 4. Store a Cursor API key

Create a User API key at https://cursor.com/dashboard/api. Adding a language
calls Cursor once per missing template, so store the key before
`./agent --init`.
One of these sources is enough:

1. Environment variable: `export CURSOR_API_KEY='...'`
2. A `.env` file at the repository root, which Git ignores:
   `CURSOR_API_KEY=...`
3. macOS keychain, which prompts for the key without showing it:

   ```bash
   security add-generic-password -s cursor-sdk -a CURSOR_API_KEY -w
   ```

If a key is compromised, revoke it at https://cursor.com/dashboard/api,
create a new one, and replace it in the source you chose.

### 5. Add a language that is not in the masters yet

English and French masters live in `data/masters/en/` and `data/masters/fr/`.
When those directories are already in the repository, `./agent` uses them.
It does not call Cursor again.

`./agent --init` generates one language that is not already covered, then
stops. It does not publish, and it does not replace masters that already
exist. Templates already written for that language are left in place.

```bash
./agent --init
```

Press Enter to fill any missing English masters, or choose another language
such as French. The choice of the default publication language is saved in
`data/agent-config.json` (ignored by Git). Commit the new
`data/masters/<language>/` directory so the next machine does not generate
it again.

For a script:

```bash
./agent --init --base-language fr --env my-tenant
```

`--rebuild-masters` is the only command that regenerates masters that
already exist. A template that cannot be produced is left unchanged on the
tenant.

### 6. Prepare, then publish

Prepare the English templates and stop before publication:

```bash
./agent --prepare-only
```

This reads branding and the EMAIL catalog, loads the English style library,
writes a snapshot under `data/pull/{environment}/`, and writes branded
payloads under `data/payloads/{environment}/en/`. The command prints how many
templates would change. Nothing is sent to the tenant.

When the payloads look right, publish them:

```bash
./agent
```

The agent asks you to type `PUSH` before it sends anything. Publication
updates only templates whose subject or body differs from the tenant, and
those templates are English. To publish another language once, pass
`--language`. The saved default stays English.

## Usage

```bash
# Add a language that is not already in data/masters/
./agent --init --base-language fr

# Prepare the English templates without publishing
./agent --prepare-only

# Publish the English templates. Type PUSH when asked.
./agent

# Target one environment and one template
./agent \
  --env my-tenant \
  --only access_profile_owner_approval_notification \
  --prepare-only

# Publish without the interactive confirmation
./agent --env my-tenant --yes
```

Offered languages: French, English, German, Spanish, Italian, Dutch, and
Portuguese. A number from the menu or a two-letter BCP 47 code can also be
passed with `--language`.

`--language` selects `data/masters/<language>/`. English is the default when
those masters exist. If that directory is missing, the agent stops and tells
you to generate it with `./agent --init --base-language <language>`.

`--rebuild-masters` regenerates the selected language, including files that
already exist. Use `--init` to add a language, not to replace one.

To preview one generated payload in a browser:

```bash
.venv/bin/python scripts/preview.py \
  data/payloads/my-tenant/en/access_request_reviewer.json \
  --out /tmp/preview.html
```

The preview substitutes sample values. It is not a Velocity engine.

## What belongs in Git

Commit the code and the style library. Everything else is recreated locally.

| Path | In Git | Why |
|---|---|---|
| `src/`, `tests/`, `scripts/preview.py`, `agent`, `requirements.txt` | yes | the agent |
| `data/masters/<language>/*.json` | yes | style library for that language; generate a missing language with `./agent --init` |
| `data/curated-keys.json` | yes | templates whose hand-written wording is kept on a rebuild |
| `data/agent-config.json` | no | style library and upload language saved by `./agent --init` |
| `data/pull/{env}/` | no | live branding, defaults, and custom templates |
| `data/payloads/{env}/{lang}/` | no | branded payloads for one tenant |
| `data/translations/{lang}/` | no | literal translations of the masters |
| `data/llm-cache/{lang}/` | no | Cursor replies; deleting them only means the next translation is paid again |
| `data/reports/` | no | publication reports |
| `data/.sdk-agent-store/` | no | local Cursor SDK store |
| `.venv/`, `.env` | no | local environment and secrets |

`data/branding/`, `data/defaults/`, `data/custom/`, `data/backups/`, and
`data/preview/` are older local copies. The current agent does not read
them. They are gitignored and can be deleted on this machine.

`./agent --init` calls Cursor only for templates that are not already in
`data/masters/<language>/`. English and French masters are both kept in Git.
`--rebuild-masters` is what regenerates an existing language.

## Flow

1. Load styled masters from `data/masters/<language>/`
2. `./agent --init` generates a language that is not already there
3. Read branding: `GET /brandings/v1`
4. Read EMAIL defaults: `GET /notification-template-defaults/v1`
5. Read existing customs: `GET /notification-templates/v1`
6. Substitute the live tenant logo and colors
7. Validate Velocity, variables, URLs, links, HTML, and branding
8. Show the diff and ask for `PUSH`
9. Publish: `POST /notification-templates/v1`

Specs: `api-specs/idn/apis/branding/`, `api-specs/idn/apis/notifications/`.

## Branding fields

| API field | Template usage |
|---|---|
| `standardLogoURL` | `<img src=...>` |
| `navigationColor` | titles / primary links |
| `actionButtonColor` | accents / negative states |
| `activeLinkColor` | links |
| `productName` | signature / footer |

The tenant branding must have a `standardLogoURL`. The agent stops if that
field is empty.

Masters use tenant-independent tokens:

- `__ISC_BRAND_NAVIGATION_COLOR__`
- `__ISC_BRAND_ACTION_COLOR__`
- `__ISC_BRAND_LINK_COLOR__`

They are replaced only after translation, when the payload is assembled.

## Notes

- Uploaded `subject` and `body` content comes from `data/masters/<language>/`.
  English is the default when those masters are present. The technical locale
  stays the one available on the ISC template (often `en`).
- Both `subject` and `body` are stored in the masters and validated.
- Velocity directives, variables, URLs, and quiet references `$!var` are
  replaced by opaque tokens during translation.
- The result is rejected if a variable, URL, directive, or link is lost,
  invented, or unbalanced.
- The syntaxes `#end`, `#{end}`, `#else`, `#{else}`, and `#elseif` are
  supported. Known structural defects in the source catalog are repaired
  deterministically before translation.
- LLM replies are cached so an identical translation is not paid for twice.
  The cache still requires a Cursor API key to be configured, because the
  key is resolved before the cache is read.
- Keys listed in `data/curated-keys.json` preserve tenant wording only when
  rebuilding a French library; they do not inject French into English masters.
- Publication is idempotent: only payloads that differ from the tenant are sent.
