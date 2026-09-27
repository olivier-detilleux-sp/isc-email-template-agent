# SailPoint Email Template Agent

Local agent that prepares and publishes ISC email templates for a tenant. It
loads a versioned style library, translates subjects and bodies with the
Cursor SDK when needed, applies the tenant logo and colors from the active
SailPoint CLI environment, then asks before publishing.

On a new computer, initialize before the first prepare or publish. Init sets
the default language and rebuilds the style library from that tenant's
catalog.

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
- A Cursor User API key. Initialization rebuilds the style library with Cursor,
  so the key is required before the first run.

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

Create a User API key at https://cursor.com/dashboard/api. Initialization
calls Cursor once per template, so store the key before `./agent --init`.
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

### 5. Initialize

`./agent` refuses to prepare or publish until this step has succeeded.
Init rebuilds the style library with Cursor. It does not publish.

```bash
./agent --init
```

Press Enter to build the English style library, or choose another library
language. With the default, uploaded subjects and bodies are that English
library, with no separate translation. The choice is saved in
`data/agent-config.json` (ignored by Git).

The curated list belongs to the older French library, so its French wording
is not copied into the English library. English masters are rebuilt from the
tenant's default templates. A template that cannot be produced in English is
left unchanged on the tenant.

For a script:

```bash
./agent --init --env my-tenant
```

That builds the English style library and uploads English. Pass
`--language de` to translate that library and upload German instead. Pass
`--base-language fr` only if the library itself should be French.

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
# First run: rebuild the English style library.
./agent --init

# Same initialization without a prompt
./agent --init --env my-tenant

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

After init, `--base-language` and `--language` both default to English.
Uploads use `data/masters/en/` directly. Pass `--language de` to translate
that library; Cursor then writes `data/translations/de/`.

`--rebuild-masters` rebuilds the saved style library again. It does not
change the saved languages. Use `--init` to choose a new default.

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
| `data/masters/en/*.json` | yes, once initialized | English style library built by `./agent --init` |
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

`./agent --init` calls Cursor for every template that is not listed in
`data/curated-keys.json` and writes the English library to
`data/masters/en/`. The older files in `data/masters/fr/` are not used.

## Flow

1. `./agent --init` rebuilds the English style library
2. Read branding: `GET /brandings/v1`
3. Read EMAIL defaults: `GET /notification-template-defaults/v1`
4. Read existing customs: `GET /notification-templates/v1`
5. Load styled masters from `data/masters/en/`
6. Translate the **subject and the body** only when `--language` is not English
7. Substitute the live tenant logo and colors
8. Validate Velocity, variables, URLs, links, HTML, and branding
9. Show the diff and ask for `PUSH`
10. Publish: `POST /notification-templates/v1`

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

- The style library and the uploaded `subject` and `body` content are English
  by default. The technical locale stays the one available on the ISC
  template (often `en`).
- Both `subject` and `body` are always translated and validated.
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
