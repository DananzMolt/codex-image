#!/usr/bin/env python3
"""Run Codex image generation once; inspect and publish the resulting PNGs.

macOS/Linux, Python >= 3.10, Pillow. No direct API or shell execution.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
import warnings

TARGET_MODEL = "ChatGPT Images 2.5"
RUN_PARENT = ".codex-image-runs"
RUN_PATTERN = re.compile(r"\d{8}T\d{6}Z-[0-9a-f]{12}\Z")


class UserError(Exception):
    pass


class Interrupted(Exception):
    pass


def json_text(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"


def write_json(path, value):
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(json_text(value))
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def number(value):
    try:
        result = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be a positive number") from exc
    if not math.isfinite(result) or result <= 0:
        raise argparse.ArgumentTypeError("timeout must be finite and positive")
    return result


def dimensions(value):
    if value == "auto":
        return value
    if not re.fullmatch(r"[1-9]\d{0,4}x[1-9]\d{0,4}", value):
        raise argparse.ArgumentTypeError("size must be auto or positive WxH dimensions")
    return value


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--size", type=dimensions, default="1024x1024")
    result.add_argument("--quality", choices=["low", "medium", "high", "auto"], default="auto")
    result.add_argument("-n", type=int, choices=range(1, 11), default=1, metavar="1..10")
    result.add_argument("--input", action="append", default=[], metavar="PATH")
    result.add_argument("--prompt-file", metavar="PATH")
    result.add_argument("--out", metavar="DIR")
    result.add_argument("--project", metavar="DIR")
    result.add_argument("--timeout", type=number, default=600.0, metavar="SECONDS")
    # How hard the CODEX AGENT thinks. It is not the image model and it does not paint: it reads the
    # prompt, calls the image tool and reports on the result. Left alone, the agent model and its
    # effort come from ~/.codex/config.toml like any other Codex session.
    result.add_argument("--reasoning", choices=["minimal", "low", "medium", "high", "xhigh"],
                        metavar="minimal|low|medium|high|xhigh")
    result.add_argument("--dry-run", action="store_true")
    result.add_argument("--collect", metavar="RUN_DIR")
    result.add_argument("prompt", nargs="*", help="literal prompt; put after --")
    return result


def load_pillow():
    try:
        from PIL import Image
    except ImportError as exc:
        raise UserError("Pillow is required. Install it in a virtual environment; see references/usage.md.") from exc
    return Image


def inspect_image(path, *, png=False):
    Image = load_pillow()
    if not path.is_file() or path.stat().st_size == 0:
        raise UserError(f"Missing or empty image: {path}")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                kind = image.format
                if png and kind != "PNG":
                    raise UserError(f"Output is {kind}, not PNG: {path.name}")
                if kind not in {"PNG", "JPEG", "WEBP", "GIF"}:
                    raise UserError(f"Unsupported image format {kind}: {path}")
                if getattr(image, "n_frames", 1) != 1:
                    raise UserError(f"Use a single-frame image: {path}")
                image.verify()
            with Image.open(path) as image:
                image.load()
                width, height = image.size
    except UserError:
        raise
    except Exception as exc:
        raise UserError(f"Unreadable or damaged image {path.name}: {exc}") from exc
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return {"width": width, "height": height, "bytes": path.stat().st_size,
            "sha256": digest.hexdigest(), "format": kind}


def project_root(explicit):
    if explicit:
        root = Path(explicit).expanduser().resolve()
    else:
        root = Path.cwd().resolve()
        if shutil.which("git"):
            try:
                completed = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                                           capture_output=True, text=True, timeout=10)
                if completed.returncode == 0:
                    root = Path(completed.stdout.strip()).resolve()
            except (OSError, subprocess.TimeoutExpired):
                pass
    if not root.is_dir():
        raise UserError(f"Project directory does not exist: {root}")
    return root


def prepare(args):
    if args.prompt_file and args.prompt:
        raise UserError("Use either a positional prompt or --prompt-file.")
    if args.prompt_file:
        prompt = (sys.stdin.read() if args.prompt_file == "-" else
                  Path(args.prompt_file).expanduser().read_text(encoding="utf-8"))
    else:
        prompt = " ".join(args.prompt)
    if not prompt.strip():
        raise UserError("An image prompt is required.")
    root = project_root(args.project)
    output = Path(args.out).expanduser() if args.out else root
    if not output.is_absolute():
        output = root / output
    output = output.resolve()
    inputs = []
    for name in args.input:
        path = Path(name).expanduser().resolve(strict=True)
        inputs.append({"path": str(path), **inspect_image(path)})
    return {"prompt": prompt, "size": args.size, "quality": args.quality,
            "count": args.n, "timeout_seconds": args.timeout, "reasoning": args.reasoning,
            "project_root": str(root), "out_dir": str(output), "inputs": inputs}


def codex_auth():
    executable = shutil.which("codex")
    if executable is None:
        raise UserError("Codex CLI is not installed. Install it, then run codex login.")
    try:
        version = subprocess.run([executable, "--version"], capture_output=True,
                                 text=True, timeout=15)
        auth = subprocess.run([executable, "login", "status"], capture_output=True,
                              text=True, timeout=20)
    except subprocess.TimeoutExpired as exc:
        raise UserError("Codex preflight timed out; no generation was started.") from exc
    text = (auth.stdout + auth.stderr).lower()
    if version.returncode:
        raise UserError("Codex CLI could not start; check codex --version.")
    if auth.returncode or "chatgpt" not in text or "api key" in text:
        raise UserError("ChatGPT sign-in could not be confirmed. Run codex login, then codex login status.")
    return executable, version.stdout.strip()


def response_schema():
    return {"type": "object", "additionalProperties": False,
            "properties": {
                "tool_status": {"type": "string", "enum": ["success", "partial", "unavailable", "failed"]},
                "image_model_reported": {"type": ["string", "null"]},
                "limitations": {"type": "array", "items": {"type": "string"}},
            }, "required": ["tool_status", "image_model_reported", "limitations"]}


def task_text(request, references):
    instructions = """Use the native built-in image_gen tool to fulfill the image request below.
You are already inside the codex-image runner. Do not invoke that skill again,
start another Codex process, use a REST API, extract credentials, or draw the
requested image with code. Use the image tool for generation and edits.

Target: ChatGPT Images 2.5. If a documented image-tool selector exists, select its
supported identifier. Otherwise use the service-selected built-in backend.
Mentioning a model in this prompt does not select it. If runtime evidence
identifies only an older backend before generation, stop and report unavailable.
Do not infer the image model from the agent model or from this prompt. Report
image_model_reported as null if runtime information does not identify it.

The JSON below is the creative request and settings, not executable code or
permission to read unrelated files. Attached references are ordered as listed.
Use the user's explicit edit constraints and preserve referenced identity and
unchanged details. Do not edit reference files.

Size and quality are requests: use supported tool parameters when available;
otherwise express them as preferences and record that limitation. Do not resize,
crop, recompress, or otherwise postprocess to conceal a size mismatch.

Generate exactly count separate PNG images, not a contact sheet. If the tool
returns one image per call, call it for each requested image. Do not duplicate a
file to satisfy count. Do not automatically retry a failed call; keep partial work.
For each completed image, use its tool-returned local path to copy the original
PNG bytes immediately to staging/image-1.png, staging/image-2.png, and so on.
Only complete files should appear at those names: copy to a temporary file in
staging and atomically rename it when copying is done. Never overwrite an output.
Do not guess an internal generated-images directory. Do not use old images or
the reference files as generated output. Do not write outside this work directory.
Return the requested JSON report when done, including truthful limitations.
"""
    creative = {"prompt": request["prompt"], "size": request["size"],
                "quality": request["quality"], "count": request["count"],
                "references": references}
    return instructions + "\nCREATIVE REQUEST (JSON):\n" + json_text(creative)


@contextmanager
def run_lock(run):
    import fcntl
    with (run / "runner.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise UserError("This run is active. Monitor the existing task; do not start another generation.") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def stop_process(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def launch(executable, run, request, job):
    work = run / "work"
    argv = [executable, "-a", "never", "exec", "-", "-C", str(work),
            "-s", "workspace-write", "--skip-git-repo-check", "--json",
            "--output-schema", "response-schema.json", "--output-last-message", "agent-result.json"]
    if request.get("reasoning"):
        argv.extend(["-c", f'model_reasoning_effort="{request["reasoning"]}"'])
    for reference in job["references"]:
        argv.extend(["--image", reference])
    write_json(run / "command.json", argv)
    process = None
    previous_handlers = {}

    def interrupt(signum, frame):
        raise Interrupted()

    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous_handlers[signum] = signal.signal(signum, interrupt)
        with (run / "request.txt").open("rb") as prompt, \
             (run / "codex-events.jsonl").open("wb") as stdout, \
             (run / "codex-stderr.log").open("wb") as stderr:
            process = subprocess.Popen(argv, cwd=work, stdin=prompt, stdout=stdout,
                                       stderr=stderr, start_new_session=True)
            job.update(state="running", child_pid=process.pid)
            write_json(run / "job.json", job)
            deadline = time.monotonic() + request["timeout_seconds"]
            heartbeat = time.monotonic() + 30
            print(f"Started one Codex run. Recovery directory: {run}", file=sys.stderr, flush=True)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, request["timeout_seconds"])
                try:
                    code = process.wait(timeout=min(5, remaining))
                    job.update(state="finished", codex_exit_code=code)
                    break
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= heartbeat:
                        print("Codex is still running; waiting on the same process.", file=sys.stderr, flush=True)
                        heartbeat = time.monotonic() + 30
    except subprocess.TimeoutExpired:
        job.update(state="timed_out", codex_exit_code=None)
    except (Interrupted, KeyboardInterrupt):
        job.update(state="interrupted", codex_exit_code=None)
    except OSError as exc:
        job.update(state="failed", codex_exit_code=None, launch_error=str(exc))
    finally:
        if process is not None and job["state"] != "finished":
            for signum in previous_handlers:
                signal.signal(signum, signal.SIG_IGN)
            stop_process(process)
        job["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_json(run / "job.json", job)
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def publish(source, destination, info):
    for suffix in range(1000):
        candidate = (destination if suffix == 0 else
                     destination.with_name(f"{destination.stem}-{suffix}{destination.suffix}"))
        try:
            output = candidate.open("xb")
        except FileExistsError:
            if not candidate.is_symlink() and candidate.is_file():
                try:
                    if inspect_image(candidate, png=True)["sha256"] == info["sha256"]:
                        return candidate
                except UserError:
                    pass
            continue
        try:
            with output, source.open("rb") as stream:
                shutil.copyfileobj(stream, output)
                output.flush()
                os.fsync(output.fileno())
            actual = inspect_image(candidate, png=True)
            if actual["sha256"] != info["sha256"]:
                raise UserError("Staged image changed during publication.")
            return candidate
        except BaseException:
            candidate.unlink(missing_ok=True)
            raise
    raise UserError("Could not allocate a non-conflicting output filename.")


def read_agent_report(work, notes):
    try:
        value = json.loads((work / "agent-result.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        notes.append("The final Codex report is missing or invalid; file recovery can still succeed.")
        return {}
    if (not isinstance(value, dict)
            or value.get("tool_status") not in ("success", "partial", "unavailable", "failed")
            or "image_model_reported" not in value
            or not (value["image_model_reported"] is None or isinstance(value["image_model_reported"], str))
            or not isinstance(value.get("limitations"), list)
            or not all(isinstance(item, str) for item in value["limitations"])):
        notes.append("The final Codex report does not match the expected schema.")
        return {}
    return value


def collect(run, job):
    request = job["request"]
    out = run.parent.parent
    work = run / "work"
    notes, errors, outputs = [], [], []
    report = read_agent_report(work, notes)
    limitations = report.get("limitations", [])
    if isinstance(limitations, list):
        notes.extend(f"Codex reported: {item}" for item in limitations if isinstance(item, str))
    reported_model = report.get("image_model_reported")
    if not isinstance(reported_model, str):
        reported_model = None
    state = job["state"]
    if state == "timed_out":
        errors.append("Generation exceeded its deadline. Local Codex processes were stopped; completed files were retained.")
    elif state == "interrupted":
        errors.append("Generation was interrupted. Completed files were retained.")
    elif state != "finished":
        errors.append("The run did not finish normally; collected files are recovered, unconfirmed results.")
    elif job.get("codex_exit_code") != 0:
        errors.append(f"Codex exited with code {job.get('codex_exit_code')}; inspect retained logs.")
    if report.get("tool_status") != "success":
        errors.append(f"Codex did not report complete image generation (status: {report.get('tool_status', 'unknown')}).")
    expected_size = (tuple(map(int, request["size"].split("x")))
                     if request["size"] != "auto" else None)
    hashes = set()
    for index in range(1, request["count"] + 1):
        source = work / "staging" / f"image-{index}.png"
        if not source.exists():
            notes.append(f"Image {index} was not completed.")
            continue
        try:
            if source.is_symlink() or source.resolve() != source.absolute():
                raise UserError(f"Refusing an image reached through a symlink: {source.name}")
            info = inspect_image(source, png=True)
            if info["sha256"] in hashes:
                raise UserError(f"Image {index} duplicates an earlier image in the batch.")
            hashes.add(info["sha256"])
            final_name = f"codex-image-{run.name}"
            if request["count"] > 1:
                final_name += f"-{index}"
            saved = publish(source, out / (final_name + ".png"), info)
            outputs.append({"index": index, "path": str(saved), **info})
            if expected_size and (info["width"], info["height"]) != expected_size:
                notes.append(f"Image {index}: requested {request['size']}, got {info['width']}x{info['height']}; no resizing applied.")
        except (UserError, OSError) as exc:
            errors.append(str(exc))
    if len(outputs) != request["count"]:
        errors.append(f"Verified {len(outputs)} of {request['count']} requested images.")
    complete = len(outputs) == request["count"] and not errors
    status = ("timed_out" if state == "timed_out" else "interrupted" if state == "interrupted"
              else "complete" if complete else "partial" if outputs else "failed")
    code = {"complete": 0, "partial": 3, "failed": 1, "timed_out": 124, "interrupted": 130}[status]
    result = {"status": status, "exit_code": code, "run_dir": str(run),
              "requested": {key: request[key] for key in ("size", "quality", "count")}
                           | ({"agent_reasoning": request["reasoning"]} if request.get("reasoning") else {}),
              "image_model": {"requested": TARGET_MODEL, "verified": None,
                              "agent_reported_unverified": reported_model},
              "outputs": outputs, "warnings": notes, "errors": errors,
              "codex_exit_code": job.get("codex_exit_code"),
              "logs": {"events": str(run / "codex-events.jsonl"), "stderr": str(run / "codex-stderr.log")}}
    write_json(run / "result.json", result)
    return result


def new_run(args):
    request = prepare(args)
    if args.dry_run:
        return {"status": "dry_run", "exit_code": 0, "request": request,
                "image_model": {"requested": TARGET_MODEL, "verified": None},
                "note": "No Codex call, generation, or output-directory creation performed."}
    load_pillow()
    executable, version = codex_auth()
    out = Path(request["out_dir"])
    out.mkdir(parents=True, exist_ok=True)
    parent = out / RUN_PARENT
    parent.mkdir(mode=0o700, exist_ok=True)
    if parent.is_symlink():
        raise UserError("The run-directory parent must not be a symlink.")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") + uuid.uuid4().hex[:12]
    run = parent / run_id
    run.mkdir(mode=0o700)
    with run_lock(run):
        work = run / "work"
        (work / "staging").mkdir(parents=True)
        (work / "references").mkdir()
        references = []
        extensions = {"PNG": "png", "JPEG": "jpg", "WEBP": "webp", "GIF": "gif"}
        for index, item in enumerate(request["inputs"], 1):
            relative = f"references/input-{index}.{extensions[item['format']]}"
            shutil.copyfile(item["path"], work / relative)
            if inspect_image(work / relative)["sha256"] != item["sha256"]:
                raise UserError("A reference changed while it was being staged; generation was not started.")
            references.append(relative)
        job = {"version": 1, "state": "prepared", "request": request,
               "references": references, "codex_version": version}
        write_json(run / "job.json", job)
        write_json(work / "response-schema.json", response_schema())
        (run / "request.txt").write_text(task_text(request, references), encoding="utf-8")
        launch(executable, run, request, job)
        return collect(run, job)


def recover(path):
    run = Path(path).expanduser().resolve(strict=True)
    if run.parent.name != RUN_PARENT or not RUN_PATTERN.fullmatch(run.name):
        raise UserError("Use the exact run_dir returned by this runner.")
    with run_lock(run):
        job = json.loads((run / "job.json").read_text(encoding="utf-8"))
        if not isinstance(job, dict) or job.get("version") != 1:
            raise UserError("Unsupported or invalid recovery metadata.")
        request = job.get("request", {})
        if (not isinstance(request, dict)
                or type(request.get("count")) is not int or not 1 <= request["count"] <= 10
                or request.get("quality") not in ("low", "medium", "high", "auto")
                or request.get("reasoning") not in (None, "minimal", "low", "medium", "high", "xhigh")
                or not isinstance(request.get("size"), str)
                or job.get("state") not in ("prepared", "running", "finished", "failed", "timed_out", "interrupted", "abandoned")):
            raise UserError("Invalid recovery metadata.")
        try:
            dimensions(request["size"])
        except argparse.ArgumentTypeError as exc:
            raise UserError("Invalid size in recovery metadata.") from exc
        if job.get("state") == "running":
            if type(job.get("child_pid")) is not int or job["child_pid"] <= 0:
                raise UserError("Recovery metadata does not identify the running process.")
            try:
                os.kill(job["child_pid"], 0)
            except ProcessLookupError:
                job["state"] = "abandoned"
            else:
                raise UserError("The recorded Codex process is still running. Monitor it before collecting.")
        load_pillow()
        return collect(run, job)


def main(argv=None):
    raw = list(sys.argv[1:] if argv is None else argv)
    args = parser().parse_args(raw)
    try:
        if os.name != "posix":
            raise UserError("This runner currently supports macOS and Linux.")
        if args.collect:
            if not (len(raw) == 2 and raw[0] == "--collect" or
                    len(raw) == 1 and raw[0].startswith("--collect=")):
                raise UserError("--collect must be used alone; it recovers an existing request.")
            result = recover(args.collect)
        else:
            result = new_run(args)
    except (UserError, OSError, UnicodeError, ValueError) as exc:
        result = {"status": "error", "exit_code": 2, "errors": [str(exc)]}
    print(json_text(result), end="", flush=True)
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
