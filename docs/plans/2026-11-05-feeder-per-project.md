# Feeder: one run per project (2026-11-05)

**Goal:** Pomodo is a template; each project repo (such as `MightyHive/barryu-tracker`, `MightyHive/google-rfm`) hosts
its own work and its own board. The feeder runs once per project, reads only that project's sources, and writes only to
that project's repo, so one client's mail can never land in another client's repo.

**Why this replaces the single pin:** the owner works on several projects. One run that sorts to-dos between repos would
be a guess that can leak across clients. One run per project, limited to that project's sources, needs no sorting.

## Design

1. **Project settings, in the project repo:** `board/feeder.toml` (next to `board/pomodo.toml`), committed, so a
   colleague on the same project gets the same rules.

   ```toml
   repo = "MightyHive/barryu-tracker"      # required: owner/name; every plan for this project must name it

   [slack]
   channels = ["C04ABC123"]                # channel ids; only messages from these are fed
   [jira]
   site = "emea-monks.atlassian.net"       # links to Jira must be on this host
   projects = ["BU"]                       # ticket keys must start with one of these and "-"
   [gmail]
   domains = ["barry.edu"]                 # for the routine: mail from these senders belongs here (not checkable by feed.py)
   [calendar]
   title_words = ["Barry"]                 # for the routine: events whose title has one of these
   [meeting]
   title_words = ["Barry"]                 # for the routine: meeting notes whose title has one of these
   ```

   Every table is optional. **A source with no table is off for this project:** feed.py refuses its actions.
   Unknown keys or tables are an error that names them (same style as `config.py`).

2. **The person's allow-list, on their own computer:** `~/.pomodo-feeder/repos`, one `owner/name` per line (blank lines
   and `#` comments ignored). It replaces `~/.pomodo-feeder/repo`. A project file can only be used if its `repo` is in
   this list: a project file in a repo the person does not trust cannot send mail summaries anywhere new.

3. **feed.py command line:** `python3 -I feeder/feed.py --project <path to feeder.toml> <plan.json> [--dry-run]`.
   `--project` is required. Before any `gh` call, in order:
   - the project file loads and validates (else `Stopped: <file>: <key> ...`);
   - its `repo` is in the allow-list (case-insensitive) (else `Stopped: ... add it to ~/.pomodo-feeder/repos`);
   - the plan's `repo` equals the project's `repo` (case-insensitive) (else refused, nothing done);
   - every action's source has a table in the project file;
   - `slack` actions: the key is `slack:<channel>/<ts>` and `<channel>` is in `channels`;
   - `jira` actions: the key is `jira:<KEY>-<n>:...` with `<KEY>` in `projects`, and the link's host is `site`;
   - `gmail`, `calendar`, `meeting`: accepted when the table exists (the routine applies `domains` / `title_words`;
     feed.py cannot see a sender or title, and says so in SKILL.md).
   All of these refuse the whole plan with a PlanError naming the action and the rule.

4. **Ledger:** unchanged (`~/.pomodo-feeder/ledger.jsonl`, entries carry `repo`, so projects never see each other's).

5. **SKILL.md and routine-prompt.md:** one scheduled task per project. The prompt gets one more blank, the project's
   folder; the routine reads `board/feeder.toml` there and reads **only** the listed channels, the listed Jira projects,
   mail from the listed domains, and events and notes whose titles have a listed word. A to-do that seems to belong to
   another project is left out and named in the closing lines ("Left out: looks like another project"). Setup: add the
   repo to `~/.pomodo-feeder/repos`, commit `board/feeder.toml` to the project.

6. **README:** the feeder section says the above in plain words, with the example file.

## Tests (in `tests/test_feed.py`, all with FakeGh and a temporary HOME; never the real `gh` or home folder)

- project file: a good file loads; unknown key and unknown table are refused naming them; `repo` must be owner/name;
  `channels`, `projects`, `domains`, `title_words` must be lists of non-empty strings; `site` a bare host.
- allow-list: missing file, empty file, repo not listed → Stopped with the add-it message; comments and blank lines ignored;
  case-insensitive match.
- plan repo ≠ project repo → refused, no `gh` call at all.
- a source with no table → refused; slack channel not listed → refused; jira key project not listed → refused; jira link on
  another host → refused; all with no `gh` call.
- a good plan for the project runs as before (reuse the existing end-to-end tests through the new entry).
- `main` without `--project` → usage line, exit 2.

Existing tests keep passing, adjusted to the new `run` signature and `repos` file (say which in the commit).

## Not in scope

The board, RFM's board copy (`rfm-todo/`), and the project files themselves (the controller writes Barry's after this lands).
