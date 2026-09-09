---
name: codex-image
description: >-
  Generate or edit images through Codex CLI using ChatGPT login, including
  reference images, batches, verified PNG output, timeouts, and recovery.
  Use for /codex-image or when the user specifically wants the Codex OAuth
  image workflow. Targets ChatGPT Images 2.5 where available; does not claim
  to select or verify a backend the CLI does not expose.
---

# Codex Image

Use the bundled runner for the local Codex CLI workflow. It accepts literal
arguments, stages reference images, runs Codex once, validates PNGs with Pillow,
and publishes files without overwriting existing output.

## Run

Resolve this skill's directory from the loaded skill path. Do not assume it is
the current directory. Requirements: macOS/Linux, Python 3.10+, Pillow, and
Codex CLI signed in with ChatGPT (`codex login`). For setup, additional options,
and recovery, read [references/usage.md](references/usage.md).

```bash
python3 /absolute/path/to/codex-image/scripts/generate.py --help
python3 /absolute/path/to/codex-image/scripts/generate.py --quality high -- 'A minimal emerald green app icon'
python3 /absolute/path/to/codex-image/scripts/generate.py --input '/path/to/photo.png' -- 'Replace only the background with pale green. Preserve the person and lighting.'
```

1. Preserve the user's prompt, reference order, explicit text, and requested
   settings. Ask only when the prompt or a required reference is missing.
2. Inspect reference images before requesting edits. Add one `--input` per
   reference and identify each reference's role in the prompt.
3. Pass arguments with proper shell quoting, or use `--prompt-file` for complex
   text. Never use `eval`, `shell=True`, or paste raw prompt text into shell code.
4. Run `generate.py` once. For long jobs, keep the host task running in the
   background and monitor that same task. The default deadline is 600 seconds.
5. Read the returned JSON, including `status`, `warnings`, and `errors`. Open
   every published image with Read or the host's image viewer. Check visual
   fidelity separately from the runner's file checks.
6. Return the images and concise results. Mention incomplete batches or
   differences from requested dimensions. Do not regenerate or reduce quality
   without user direction. If interrupted, use `--collect` to recover existing
   output before considering another generation.

## Model and settings

The target is **ChatGPT Images 2.5**. The runner preserves the configured Codex
agent model and never passes an image model to `codex --model`. It requests the
built-in image tool, without a direct API fallback or manually handled tokens.

The image backend may not expose a selector or its identity. The result's
`image_model.verified` therefore remains `null`; an agent-reported model is
separate, unverified information. Never describe that as verified Images 2.5.
If the user requires proof of that exact backend, explain this limitation before
generation. If runtime evidence identifies an older backend, Codex is instructed
to stop rather than intentionally substitute it.

`--reasoning` sets how hard the CODEX AGENT thinks, by passing
`-c model_reasoning_effort=VALUE` to that one `codex exec` call. It never writes
to `~/.codex/config.toml`, and omitting it leaves the agent exactly as
configured. It is not the image model and does not change what is painted: the
agent reads the prompt, calls the image tool, and reports on the result. Turning
it down mainly thins the `warnings`, which are the agent's own reading of the
image it got back. See references/usage.md.

`--size` and `--quality` are requests. Codex must use supported tool parameters
when available and otherwise report them as preferences. The runner measures
actual dimensions; it does not resize images to hide a mismatch.

## Boundaries

- Keep source images unchanged. Copies with safe filenames are attached to Codex.
- Do not rerun this skill from inside its Codex subprocess: call the native
  image tool there. Do not substitute a script-drawn image for generated art.
- Do not change authentication, global configuration, permissions, or the user's
  selected agent model to work around a failure.
- If this host lacks a local CLI, say so. This skill's installation does not
  install it on another computer. Use the host-native image workflow only when
  the user accepts that different execution route.

See [references/usage.md](references/usage.md) for usage, exit codes, and the
documented limits on model verification.
