# Usage and troubleshooting

## Original skill and this version

Adapted from [wjb127/codex-image](https://github.com/wjb127/codex-image), under
the included [MIT license](../LICENSE). Its `main` branch, reviewed September 9,
2026, contains the original skill's Bash examples inside `SKILL.md`, documentation,
and image/demo assets. It has no standalone generation script or `scripts/` folder.
The `generate.py` runner and regression suite in this version are new additions;
install this complete version to use them. Cloning the original upstream alone
does not install these additions. Existing size, quality, output-directory, and
count defaults are retained; reference input and recovery are added.

## Setup

Use macOS or Linux with Python 3.10 or later and Codex CLI. Check `codex --version`
and `codex login status`; sign in with ChatGPT using `codex login` if needed.
Install/update Codex through its original package manager. For npm installations:

```bash
npm install -g @openai/codex@latest
```

The runner uses Python's standard library and Pillow for image validation. If
Pillow is missing, an isolated environment avoids changing system Python:

```bash
python3 -m venv "$HOME/.cache/codex-image/venv"
"$HOME/.cache/codex-image/venv/bin/python" -m pip install Pillow
```

Use that environment's Python to run `scripts/generate.py`. No OpenAI SDK or API
key is needed. The runner never installs dependencies or changes login for you.

For Claude Code, place the complete skill folder at
`~/.claude/skills/codex-image/`, retaining `SKILL.md`, `scripts/`, and `references/`.
Invoke `/codex-image`. Do not copy `SKILL.md` alone: the runner is required.
The optional `agents/openai.yaml` contains Codex UI metadata.

## Options

| Option | Behavior | Default |
|---|---|---|
| Positional prompt | Place after `--` to preserve leading dashes | Required unless using a prompt file |
| `--prompt-file PATH` | Read literal UTF-8 text; `-` reads stdin | None |
| `--input PATH` | Reference image; repeat to preserve reference order | None |
| `--size WxH` | Positive dimensions, or `auto`; a request, not forced resizing | `1024x1024` |
| `--quality VALUE` | `low`, `medium`, `high`, `auto` | `auto` |
| `-n COUNT` | Separate images, from 1 to 10 | `1` |
| `--out DIR` | Absolute or relative to the project root | Project root |
| `--project DIR` | Explicit project root; otherwise detect Git root or use cwd | Auto |
| `--timeout SECONDS` | Deadline for the single Codex execution | `600` |
| `--reasoning VALUE` | `minimal`, `low`, `medium`, `high`, `xhigh`. How hard the CODEX AGENT thinks; see below | Whatever `~/.codex/config.toml` says |
| `--dry-run` | Validate arguments/references and print a plan; no Codex call or output-directory creation | Off |
| `--collect RUN_DIR` | Verify/publish completed staged files; never call Codex again | None |

Input and prompt-file paths resolve against the invocation directory. A relative
output path resolves against the project root. Input images are attached through
safe relative filenames, including when original filenames contain commas.
Use PNG, JPEG, WEBP, or a single-frame GIF for references. Animated inputs require
an explicit still image. Input bytes are copied, never edited or re-encoded.

Examples (replace the skill path):

```bash
python3 /path/to/codex-image/scripts/generate.py -n 3 --out public/images -- 'Three separate concepts for an emerald fitness mascot'
python3 /path/to/codex-image/scripts/generate.py --input logo.png --input style.png --prompt-file brief.txt
python3 /path/to/codex-image/scripts/generate.py --dry-run --size 1536x1024 -- 'A botanical studio photograph'
python3 /path/to/codex-image/scripts/generate.py --collect '/absolute/run/path/from/result'
```

## Output and recovery

Diagnostics go to stderr; the final result is JSON on stdout. The JSON includes
absolute image paths, measured dimensions, file sizes, SHA-256 hashes, warnings,
errors, and model information. All output must be fully decodable PNG. Identical
files in a batch count once and are reported as duplicates.

Each run has a unique directory under `<output>/.codex-image-runs/`. It retains
the request, reference copies, staged images, Codex logs, and result. This lets
`--collect` recover completed files even when the final agent reply is missing.
Runs are locked; collection refuses an active run. Collection is idempotent and
does not spend another image generation. Keep run directories private: they
contain the user's prompt and reference copies. Remove them only when recovery
is no longer needed.

Published files use a timestamp and random run identifier. Existing files are
never overwritten. The runner gives Codex a dedicated work directory; only the
controller copies verified images to the requested destination. A timeout or
interrupt stops the owned local process group, then collects valid images. It
cannot promise that an already-submitted server request was cancelled or free.

| Exit code | Meaning |
|---|---|
| `0` | Complete batch with a successful Codex report and verified files |
| `1` | Failed run; inspect JSON and retained logs |
| `2` | Invalid arguments, missing dependency/login, or collection unavailable |
| `3` | Partial or unconfirmed result; usable files may be present |
| `124` | Deadline exceeded; any completed files are retained |
| `130` | Interrupted; any completed files are retained |

`complete` verifies the file contract, not artistic quality or model identity.
An agent's success message alone cannot establish either. Review images visually.
Actual dimensions that differ from `--size` are reported, not silently corrected.

## Agent reasoning effort

`--reasoning` passes `-c model_reasoning_effort="VALUE"` to `codex exec` for that one run. Omit it
and nothing is passed, so the agent behaves exactly as any other Codex session on this machine. It
never touches `~/.codex/config.toml`, so the setting cannot leak into the next run or into
unrelated work.

**It is the agent, not the painter.** The agent reads the prompt, attaches the references, calls the
built-in image tool and writes the report. The image backend is separate and is not selectable, so
lowering this does not make the pictures cheaper or worse. What it changes is how carefully the
result is inspected: the `warnings` in the report are the agent's own reading of the image it got
back, and on a high setting they have named real defects, "the far plate is obscured", "the palms
are not clearly visible", before a human spotted them. Lower the effort and expect thinner warnings.

It is also not much of a speed-up. Most of the three to five minutes is the image tool, not the
agent.

The chosen value is echoed back in the result as `requested.agent_reasoning`, and it is recorded in
the run directory so `--collect` on an interrupted run still validates.

## Model identity and official sources

Checked September 9, 2026. OpenAI's [Images 2.5 announcement](https://openai.com/index/introducing-chatgpt-images-2-5/)
includes Codex availability. The [image-generation documentation](https://learn.chatgpt.com/docs/image-generation)
still names `gpt-image-2`. Neither establishes a CLI selector for an exact image
backend. A prompt requesting Images 2.5 does not pin it, and an agent's reported
model name is not independent evidence. The runner intentionally leaves the
verified model empty.

The [CLI reference](https://learn.chatgpt.com/docs/developer-commands?surface=cli)
documents stdin prompts, image attachments, JSON output, output schemas, sandbox
settings, and login status. The runner uses those controls and keeps the user's
agent model. It does not depend on a hardcoded internal generated-images path.

## Validation scope

Run the bundled regression suite with:

```bash
python3 /path/to/codex-image/scripts/test_generate.py
```

These tests use a fake local Codex executable to exercise process control and
file handling without generating images or consuming account usage. They do not
prove real Codex tool availability, backend selection, or artistic quality. A
real generation must be tested on a machine with a signed-in Codex CLI.
