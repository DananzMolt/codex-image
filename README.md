# codex-image

A Claude Code / Codex skill that generates and edits images through the **Codex CLI** using your **ChatGPT login** — no API key, no manually handled tokens.

It ships a Python runner (`scripts/generate.py`) that stages reference images, runs Codex once, validates the returned PNGs with Pillow, and publishes them without overwriting existing files. It returns structured JSON with `status`, `warnings`, and `errors`.

## Requirements

- macOS or Linux
- Python 3.10+ with [Pillow](https://pypi.org/project/Pillow/)
- [Codex CLI](https://github.com/openai/codex) signed in with ChatGPT (`codex login`)

## Install

```bash
git clone https://github.com/DananzMolt/codex-image ~/.claude/skills/codex-image
```

For Codex, clone into `~/.codex/skills/codex-image` instead — `agents/openai.yaml` carries the Codex plugin manifest.

## Usage

```bash
python3 ~/.claude/skills/codex-image/scripts/generate.py --help

# generate
python3 ~/.claude/skills/codex-image/scripts/generate.py \
  --quality high -- 'A minimal emerald green app icon'

# edit, with a reference image
python3 ~/.claude/skills/codex-image/scripts/generate.py \
  --input '/path/to/photo.png' \
  -- 'Replace only the background with pale green. Preserve the person and lighting.'
```

Pass one `--input` per reference image and name each reference's role in the prompt. Use `--prompt-file` for complex prompt text. If a run is interrupted, use `--collect` to recover output that already exists before generating again.

See [SKILL.md](SKILL.md) for the agent-facing instructions and [references/usage.md](references/usage.md) for all options, exit codes, and recovery.

## On model verification

The target backend is **ChatGPT Images 2.5**. The runner preserves your configured Codex agent model and never passes an image model to `codex --model`.

The image backend may not expose a selector or its own identity, so `image_model.verified` in the result stays `null`. An agent-reported model is separate, unverified information — it should never be described as verified Images 2.5. `--size` and `--quality` are requests, not guarantees; the runner measures the actual dimensions rather than resizing to hide a mismatch.

## Tests

```bash
python3 -m unittest discover -s scripts -p 'test_*.py'
```

Run this with a Python that has Pillow installed.

## Origin

Started from [wjb127/codex-image](https://github.com/wjb127/codex-image) (MIT) and rewritten: the prompt-only skill was replaced with a Python runner, a test suite, reference docs, and a Codex agent manifest. The original MIT license is retained in [LICENSE](LICENSE).

## License

MIT
