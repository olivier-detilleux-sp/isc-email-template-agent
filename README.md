# SailPoint Email Template Agent

Local agent that prepares and publishes ISC email templates for a tenant. It
loads a versioned style library from `data/masters/<language>/`, applies the
tenant logo and colors from the active SailPoint CLI environment, then asks
before publishing.

English and French masters are already in the repository. A normal run does
not call Cursor. Run `./agent --init` only to add a language that is not in
`data/masters/` yet. That command translates an existing language and keeps
its HTML layout.

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
- A Cursor User API key. Adding a language and rebuilding masters call
  Cursor, so the key is required before `./agent --init` or
  `./agent --rebuild-masters`. Prepare and publish do not call Cursor.

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
calls Cursor once per missing template. Store the key before
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

`./agent --init` adds one language that is not already covered, then stops.
It translates an existing language (English when it is present) and keeps
the HTML layout. It does not redesign, it does not publish, and it does not
replace masters that already exist.

```bash
./agent --init
```

Press Enter to fill any missing English masters from another language, or
choose a new language such as German. The translation keeps the existing
layout. The choice of the default publication language is saved in
`data/agent-config.json` (ignored by Git). Commit the new
`data/masters/<language>/` directory so the next machine does not generate
it again.

For a script, add German by translating the English masters:

```bash
./agent --init --base-language de --env my-tenant
```

`--base-language` is the language being added. English is the translation
source when `data/masters/en/` exists. Otherwise the agent uses the other
language already stored. If the requested language is already complete,
nothing is translated.

`--rebuild-masters` is the only command that rewrites masters that already
exist. It reads the target tenant, then:

- keys listed in `data/curated-keys.json` are copied from that tenant's
  custom template, in the language they are written in, and translated
  literally when the target library language is different. The HTML layout
  is kept. If the tenant has no custom for that key, the existing master
  is left as it is;
- every other key is redesigned from the stock default. Access-request and
  approval templates follow the house style below, and the current master
  is passed in as the layout to keep.

A template that fails validation is not uploaded. The copy already on the
tenant stays in place.

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
updates only templates whose subject or body differs from the tenant.
English is the default library. Pass `--language fr` to upload the French
masters instead.

ISC stores one EMAIL body per template key and catalog locale. The locale
on the uploaded template is the catalog locale, usually `en`, not the
language of the prose. Publishing French therefore replaces the English
body of the same template. Publish one language at a time, on the tenant
where that language should be the live text.

## Usage

```bash
# Add German by translating the English masters. Layout is unchanged.
./agent --init --base-language de

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

`--language` selects `data/masters/<language>/` for prepare and publish.
English is the default when those masters exist. If that directory is
missing, the agent stops and tells you to add it with
`./agent --init --base-language <language>`.

`--init` translates. `--rebuild-masters` rewrites an existing language.
Do not use `--init` to restyle masters that are already there.

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
| `data/masters/<language>/*.json` | yes | style library for that language; add a missing language with `./agent --init` |
| `data/curated-keys.json` | yes | templates copied from the tenant custom on `--rebuild-masters`, then translated literally |
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
`data/masters/<language>/`, and only to translate them. English and French
masters are both kept in Git. `--rebuild-masters` is what rewrites an
existing language.

## Flow

1. Load styled masters from `data/masters/<language>/`
2. `./agent --init` translates a language that is not already there, keeping the layout
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
| `actionButtonColor` | rejection bands, negative titles, accent borders |
| `activeLinkColor` | links. When it equals the navigation color, titles and primary borders use the navigation token |
| `productName` | signature / footer |

The tenant branding must have a `standardLogoURL`. The agent stops if that
field is empty.

Masters use tenant-independent tokens:

- `__ISC_BRAND_NAVIGATION_COLOR__`
- `__ISC_BRAND_ACTION_COLOR__`
- `__ISC_BRAND_LINK_COLOR__`

They are replaced only when the payload is assembled, not inside the masters.

Some colors are fixed and must not be replaced by a brand token:

| Use | Background | Border or text |
|---|---|---|
| Approved outcome | `#f0f7f0` | `#2e7d32` |
| Denied or cancelled outcome | `#fef5f5` | the action color |
| Just-in-time activation notice | `#fff8e1` | `#ffab00` |
| Comment or dimension details | `#f9f9f9` | navigation color on the left border, when the box is informational |

Access-request and approval masters use these boxes, a short title, and a
button (a styled `<a>`, never `<button>`) when the message sends the reader
to review or approve. Dimension attributes and just-in-time text stay inside
their `#if`, so a tenant without that data does not show an empty box.

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
  supported. Masters keep the stock form `#end`. `#{end}` is written only
  when the next character would otherwise stick to the directive name, as in
  `#{end}changed`. Inside a `#set("...")` string, keep `#end` before the
  closing quote. A braced closer there is printed as text.
- Known structural defects in a source template are repaired before
  translation when the template is redesigned.
- LLM replies are cached so an identical translation is not paid for twice.
  The cache still requires a Cursor API key to be configured, because the
  key is resolved before the cache is read.
- Keys listed in `data/curated-keys.json` are taken from the target tenant's
  custom template on `--rebuild-masters`, whichever language that custom is
  written in, and translated literally into the library language.
- Publication is idempotent: only payloads that differ from the tenant are sent.
