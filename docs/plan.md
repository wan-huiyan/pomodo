# Pomodo: design and build plan (2026-10-30)

Pomodo is an internal team board made generic: a shared progress board for a team of 2-5 people, read from the issues and pull
requests of any one GitHub repo. It keeps the original's sections, "It's mine!", "Checked in the app", the done calendar,
Pomo the pixel tomato, the sparkle layer and the light/dark switch.

Decisions the user made: tasks stay in GitHub issues; people sign in with GitHub; the name is Pomodo.

## What changes

1. **One settings file, `pomodo.toml`** (path from `POMODO_CONFIG`, default `pomodo.toml`; if that file is missing,
   `pomodo.example.toml` is used and a warning is logged). Read with `tomllib`. Shape:

   ```toml
   title = "Pomodo"
   repo = "owner/name"

   [time]
   zone = "Europe/London"   # the board's day: Today, Yesterday, the calendar
   zone_label = "UK"        # how the page names that clock ("UK time")
   work_start = 9           # after-hours starts before this hour ...
   work_end = 18            # ... and from this hour, Monday to Friday, on each person's own clock

   [labels]
   chore = "todo"                 # small admin jobs: the Chores card
   tracker = "tracker"            # a checklist of other issues: a progress card
   verified = "human-verified"    # "Checked in the app" badge
   priority = "priority"          # the top-priority label
   priority_title = "Top priority"
   nice = "nice-to-have"
   milestone = ""                 # optional: a label whose closed/total shows as a stat; "" hides it

   [[team]]                       # 1 to ~5 people; order is card order
   login = "ada-demo"
   name = "Ada"
   zone = "Europe/London"
   ```

   `config.py` validates it (repo is `owner/name`, zones exist, 0 <= work_start < work_end <= 24, at least one team
   member, logins unique ignoring case) and fails at start-up with a plain message naming the bad key.
   `board.py` keeps its module constants (`PEOPLE`, `PEOPLE_TZ`, `BOARD_TZ`, `WORK_START`, `WORK_END`, label names)
   but fills them from the config, so most of its code does not change. Every user-visible zone name, end-of-day hour, priority
   wording and person name comes from the config.

2. **Sign in with GitHub** (`auth.py` replaces `identity.py`; `google-auth` and `cryptography` leave
   `requirements.txt`).
   - Every page and `/api/*` needs a signed-in team member. Open to all: `/login`, `/login/github`,
     `/auth/callback`, `/static/*`, `/healthz`.
   - Not signed in: a page asks you to sign in (`/api/*` answers 401 JSON). Signed in but not in `[[team]]`: a
     "not on this team" page with a sign-out button (`/api/*` answers 403 JSON). Logins compare ignoring case.
   - OAuth app flow by hand with `requests`: a random `state` kept in the session and compared with
     `hmac.compare_digest`; the code is exchanged on the server; no scopes are asked; the login is read from
     `GET https://api.github.com/user` and the user's token is then dropped, never stored.
   - Callback URL is `PUBLIC_URL + "/auth/callback"` (`PUBLIC_URL` env); without it, the request's own host.
   - Flask session cookie: HttpOnly, SameSite=Lax, Secure unless `PUBLIC_URL` starts with `http://`; 30 days.
   - `SECRET_KEY` env is required, except in local-copy mode (below), where a random one is made per start.
   - Sign out is `POST /logout`; the CSP's `form-action` becomes `'self'`.
   - The write endpoints keep their JSON-only and `Sec-Fetch-Site: same-origin` checks; with a cookie session these
     are the CSRF protection.
   - Writes still use the one server `GITHUB_TOKEN`, so GitHub shows its owner as the one who made each change; the
     comment text names the person.

3. **Local-copy mode and demo data.** Local-copy mode is `DEV_FIXTURE=<path>` (a saved GitHub read) or `DEMO=1`
   (made-up data, `demo.py`). In it, nothing is ever written to GitHub. `DEMO=1` builds data relative to the time the
   app starts, for the configured team, shaped exactly like `github.fetch_repo`'s output, so the calendar, every
   section and Pomo's lines all have something to show on day one. `DEV_USER=<login>` signs you in as that login, only
   in local-copy mode and never when `K_SERVICE` or `POMODO_PRODUCTION` is set.

4. **No client details anywhere**: no company, project, email domain, cloud project names or numbers, service
   accounts, Google's sign-in gate, or the original people. The example config and tests use a fictional team.

5. **Deploys anywhere**: the Dockerfile stays; the README covers creating the GitHub OAuth app, the fine-grained
   token, `pomodo.toml`, running locally, and deploying to any container host (Cloud Run as one example, no project
   ids).

## Tasks (one after another; each ends with the whole test suite green and one commit)

| # | Task | Tier |
|---|---|---|
| 1 | Config: `config.py`, `pomodo.example.toml`, `board.py`/`main.py` read it; the old priority label becomes `priority`, the review stat becomes the optional milestone; renamed fields followed through templates and JS; tests use a fictional test config | Opus |
| 2 | Auth: `auth.py`, gate, sign-in / not-on-team pages, logout, dev user rules, tests for signed out / wrong user / team member / bad state | Opus |
| 3 | Demo data: `demo.py`, `DEMO=1`, test that the demo fills every section and this month's calendar | Sonnet |
| 4 | Pomodo branding and wording sweep across templates, static files, sparkle headers; grep for client terms comes back empty | Sonnet |
| 5 | README, Dockerfile, ignore files, token script | Sonnet |

Then: run on demo data in the browser pane, an independent review of the sign-in code, and a final grep.
