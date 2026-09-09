#!/usr/bin/env python3
"""Offline process/file regression tests. No network or real Codex execution."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest

from PIL import Image

sys.dont_write_bytecode = True

RUNNER = Path(__file__).with_name("generate.py").resolve()
SPEC = importlib.util.spec_from_file_location("codex_image_generate", RUNNER)
GEN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(GEN)

FAKE_CODEX = r'''
import json, os, pathlib, subprocess, sys, time
from PIL import Image
root = pathlib.Path(os.environ["CODEX_IMAGE_FIXTURE_DIR"])
args = sys.argv[1:]
mode = os.environ.get("CODEX_IMAGE_FIXTURE_MODE", "success")
if args == ["--version"]:
    print("codex-cli offline-fixture")
    sys.exit(0)
if args == ["login", "status"]:
    login = os.environ.get("CODEX_IMAGE_FIXTURE_LOGIN", "chatgpt")
    print("Logged in using ChatGPT" if login == "chatgpt" else "Logged in using an API key", file=sys.stderr)
    sys.exit(0 if login in ("chatgpt", "api") else 1)
assert args[:4] == ["-a", "never", "exec", "-"]
with (root / "calls.txt").open("a") as f:
    f.write("exec\n")
task = sys.stdin.read()
request = json.loads(task.split("CREATIVE REQUEST (JSON):\n", 1)[1])
(root / "received.json").write_text(json.dumps({"request": request, "args": args, "cwd": os.getcwd()}))
count = request["count"]
actual = count - 1 if mode == "partial" else 1 if mode in ("timeout", "wait") else count
if mode == "empty":
    actual = 0
for i in range(1, actual + 1):
    path = pathlib.Path("staging") / ("image-%d.png" % i)
    color = (43, 12, 91) if mode == "duplicate" else (i * 23, 12, 91)
    im = Image.new("RGB", (4, 3), color)
    if mode == "corrupt" and i == 2:
        path.write_bytes(b"\x89PNG\r\n\x1a\nnot-a-valid-png")
    elif mode == "jpeg":
        im.save(path, format="JPEG")
    elif mode == "symlink":
        outside = root / "outside.png"
        im.save(outside)
        path.symlink_to(outside)
    else:
        im.save(path)
if mode in ("timeout", "wait"):
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    (root / "child-pid.txt").write_text(str(child.pid))
    (root / "ready").write_text("ready")
    time.sleep(60)
if mode != "missing-report":
    report = {"tool_status": "partial" if mode == "partial" else "success",
              "image_model_reported": "gpt-image-2.5-unverified" if mode == "model" else None,
              "limitations": []}
    pathlib.Path(args[args.index("--output-last-message") + 1]).write_text(json.dumps(report))
print(json.dumps({"type": "turn.completed"}))
sys.exit(7 if mode == "nonzero" else 0)
'''


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="codex-image-tests-")
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        fake = self.bin / "codex"
        fake.write_text("#!" + sys.executable + "\n" + textwrap.dedent(FAKE_CODEX), encoding="utf-8")
        fake.chmod(0o700)
        self.output = self.root / "art, work's $folder"
        self.env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ.get("PATH", ""),
                        CODEX_IMAGE_FIXTURE_DIR=str(self.root))

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *args, mode="success", stdin=None, login="chatgpt"):
        env = dict(self.env, CODEX_IMAGE_FIXTURE_MODE=mode, CODEX_IMAGE_FIXTURE_LOGIN=login)
        result = subprocess.run([sys.executable, str(RUNNER), *args], cwd=self.root,
                                env=env, input=stdin, text=True, capture_output=True, timeout=15)
        data = json.loads(result.stdout) if result.stdout.strip() else None
        return result, data

    def generate(self, *args, mode="success", login="chatgpt"):
        return self.invoke("--out", str(self.output), "--size", "4x3", *args,
                           "--", "A small green illustration", mode=mode, login=login)

    def calls(self):
        path = self.root / "calls.txt"
        return path.read_text().splitlines() if path.exists() else []

    def test_complete_batch_and_model_not_invented(self):
        process, data = self.generate("-n", "3", "--quality", "high", mode="model")
        self.assertEqual(process.returncode, 0, process.stderr)
        self.assertEqual(data["status"], "complete")
        self.assertEqual(len(data["outputs"]), 3)
        self.assertIsNone(data["image_model"]["verified"])
        self.assertIn("unverified", data["image_model"]["agent_reported_unverified"])
        for output in data["outputs"]:
            self.assertEqual((output["width"], output["height"]), (4, 3))
            self.assertEqual(output["sha256"], GEN.inspect_image(Path(output["path"]))["sha256"])
        self.assertEqual(self.calls(), ["exec"])

    def test_literal_prompt_reference_order_and_comma_paths(self):
        refs = [self.root / "first, reference's.png", self.root / "second reference.png"]
        for index, path in enumerate(refs):
            Image.new("RGB", (3, 3), (index * 33, 0, 10)).save(path)
        original = [p.read_bytes() for p in refs]
        prompt = 'מחיר $20, `demo`, $(touch SHOULD_NOT_EXIST)\n"Line two"'
        brief = self.root / "brief's text.txt"
        brief.write_text(prompt, encoding="utf-8")
        process, data = self.invoke("--out", str(self.output), "--size", "4x3",
                                    "--input", str(refs[0]), "--input", str(refs[1]),
                                    "--prompt-file", str(brief))
        self.assertEqual(process.returncode, 0, process.stderr)
        received = json.loads((self.root / "received.json").read_text())
        self.assertEqual(received["request"]["prompt"], prompt)
        self.assertEqual(received["request"]["references"], ["references/input-1.png", "references/input-2.png"])
        for index, path in enumerate(refs):
            self.assertEqual(path.read_bytes(), original[index])
            copy = Path(data["run_dir"]) / "work" / received["request"]["references"][index]
            self.assertEqual(copy.read_bytes(), original[index])
        self.assertFalse((self.root / "SHOULD_NOT_EXIST").exists())
        self.assertFalse(list(self.output.rglob("SHOULD_NOT_EXIST")))

    def test_dry_run_and_stdin_create_nothing(self):
        prompt = "--literal prompt\nשלום $HOME"
        process, data = self.invoke("--dry-run", "--out", str(self.output), "--prompt-file", "-", stdin=prompt)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(data["request"]["prompt"], prompt)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.calls(), [])

    def test_relative_output_uses_project_root(self):
        project = self.root / "project"
        project.mkdir()
        process, data = self.invoke("--dry-run", "--project", str(project), "--out", "public/images", "--", "test")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(Path(data["request"]["out_dir"]).resolve(), (project / "public/images").resolve())

    def test_invalid_options_do_not_generate(self):
        for args in [("-n", "0"), ("-n", "11"), ("--timeout", "nan"), ("--timeout", "-1"),
                     ("--timeout", "inf"), ("--size", "0x100"), ("--quality", "best")]:
            with self.subTest(args=args):
                process, _ = self.generate(*args)
                self.assertEqual(process.returncode, 2)
        self.assertEqual(self.calls(), [])
        self.assertFalse(self.output.exists())

    def test_missing_prompt_and_conflicting_sources(self):
        process, _ = self.invoke("--out", str(self.output))
        self.assertEqual(process.returncode, 2)
        process, _ = self.invoke("--prompt-file", "-", "--", "other", stdin="text")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(self.calls(), [])

    def test_api_login_and_failed_login_do_not_generate(self):
        for login in ("api", "missing"):
            process, data = self.generate(login=login)
            self.assertEqual(process.returncode, 2)
            self.assertIn("ChatGPT", " ".join(data["errors"]))
        self.assertFalse(self.output.exists())
        self.assertEqual(self.calls(), [])

    def test_invalid_reference_fails_before_call(self):
        bad = self.root / "broken.png"
        bad.write_bytes(b"broken")
        process, data = self.generate("--input", str(bad))
        self.assertEqual(process.returncode, 2)
        self.assertIn("damaged", data["errors"][0])
        self.assertEqual(self.calls(), [])

    def test_partial_batch_and_recovery_are_idempotent(self):
        process, data = self.generate("-n", "3", mode="partial")
        self.assertEqual(process.returncode, 3)
        self.assertEqual(len(data["outputs"]), 2)
        expected = [p["path"] for p in data["outputs"]]
        recovered, result = self.invoke("--collect", data["run_dir"])
        self.assertEqual(recovered.returncode, 3)
        self.assertEqual([p["path"] for p in result["outputs"]], expected)
        self.assertEqual(len(list(self.output.glob("*.png"))), 2)
        self.assertEqual(self.calls(), ["exec"])

    def test_corrupt_and_duplicate_outputs_cannot_fill_batch(self):
        for mode in ("corrupt", "duplicate"):
            with self.subTest(mode=mode):
                process, data = self.generate("-n", "2", mode=mode)
                self.assertEqual(process.returncode, 3)
                self.assertEqual(len(data["outputs"]), 1)
                self.assertTrue(data["errors"])

    def test_wrong_format_and_symlink_are_not_published(self):
        for mode in ("jpeg", "symlink"):
            with self.subTest(mode=mode):
                process, data = self.generate(mode=mode)
                self.assertEqual(process.returncode, 1)
                self.assertEqual(data["outputs"], [])
                self.assertTrue(data["errors"])

    def test_missing_report_and_nonzero_exit_preserve_images(self):
        for mode in ("missing-report", "nonzero"):
            with self.subTest(mode=mode):
                process, data = self.generate(mode=mode)
                self.assertEqual(process.returncode, 3)
                self.assertEqual(len(data["outputs"]), 1)
                self.assertTrue(data["errors"])

    def test_size_mismatch_is_reported_without_resize(self):
        process, data = self.generate("--size", "1024x1536")
        self.assertEqual(process.returncode, 0)
        self.assertEqual(data["outputs"][0]["width"], 4)
        self.assertIn("got 4x3", " ".join(data["warnings"]))

    def test_claimed_success_without_images_is_failure(self):
        process, data = self.generate(mode="empty")
        self.assertEqual(process.returncode, 1)
        self.assertEqual(data["status"], "failed")

    def test_timeout_retains_partial_and_stops_child_group(self):
        process, data = self.generate("-n", "3", "--timeout", "0.6", mode="timeout")
        self.assertEqual(process.returncode, 124, process.stderr)
        self.assertEqual(data["status"], "timed_out")
        self.assertEqual(len(data["outputs"]), 1)
        pid = int((self.root / "child-pid.txt").read_text())
        status = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True)
        self.assertTrue(not status.stdout.strip() or status.stdout.strip().startswith("Z"), status.stdout)
        recovered, result = self.invoke("--collect", data["run_dir"])
        self.assertEqual(recovered.returncode, 124)
        self.assertEqual(data["outputs"], result["outputs"])
        self.assertEqual(self.calls(), ["exec"])

    def test_active_collection_refused_and_sigterm_retains_files(self):
        env = dict(self.env, CODEX_IMAGE_FIXTURE_MODE="wait")
        process = subprocess.Popen([sys.executable, str(RUNNER), "--out", str(self.output),
                                    "--size", "4x3", "--timeout", "10", "--", "test"],
                                   cwd=self.root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not (self.root / "ready").exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue((self.root / "ready").exists())
            run = next((self.output / GEN.RUN_PARENT).iterdir())
            refused, data = self.invoke("--collect", str(run))
            self.assertEqual(refused.returncode, 2)
            self.assertIn("active", data["errors"][0])
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=8)
            data = json.loads(stdout)
            self.assertEqual(process.returncode, 130, stderr)
            self.assertEqual(len(data["outputs"]), 1)
            self.assertEqual(self.calls(), ["exec"])
        finally:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=8)

    def test_publication_never_overwrites_existing_file(self):
        source = self.root / "source.png"
        Image.new("RGB", (4, 3)).save(source)
        destination = self.root / "keep.png"
        destination.write_bytes(b"Existing user content")
        saved = GEN.publish(source, destination, GEN.inspect_image(source, png=True))
        self.assertNotEqual(saved, destination)
        self.assertEqual(destination.read_bytes(), b"Existing user content")
        again = GEN.publish(source, destination, GEN.inspect_image(source, png=True))
        self.assertEqual(again, saved)

    def test_collect_rejects_generation_arguments(self):
        process, data = self.generate()
        self.assertEqual(process.returncode, 0)
        process, result = self.invoke("--collect", data["run_dir"], "--quality", "low")
        self.assertEqual(process.returncode, 2)
        self.assertEqual(self.calls(), ["exec"])

    def test_damaged_recovery_metadata_returns_a_clear_error(self):
        process, data = self.generate()
        self.assertEqual(process.returncode, 0)
        path = Path(data["run_dir"]) / "job.json"
        original = json.loads(path.read_text())
        for broken in ({**original, "request": []}, {**original, "state": "unknown"},
                       {**original, "state": []},
                       {**original, "request": {**original["request"], "quality": []}},
                       {**original, "state": "running", "child_pid": -1}):
            with self.subTest(broken=broken):
                path.write_text(json.dumps(broken))
                process, result = self.invoke("--collect", data["run_dir"])
                self.assertEqual(process.returncode, 2)
                self.assertTrue(result["errors"])
                self.assertNotIn("Traceback", process.stderr)
        self.assertEqual(self.calls(), ["exec"])

    def test_malformed_agent_success_is_unconfirmed(self):
        process, data = self.generate()
        self.assertEqual(process.returncode, 0)
        path = Path(data["run_dir"]) / "work" / "agent-result.json"
        for broken in ({"tool_status": "success"}, {"tool_status": []}):
            path.write_text(json.dumps(broken))
            process, recovered = self.invoke("--collect", data["run_dir"])
            self.assertEqual(process.returncode, 3)
            self.assertEqual(len(recovered["outputs"]), 1)
            self.assertIn("schema", " ".join(recovered["warnings"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
