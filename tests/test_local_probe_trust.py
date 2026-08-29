from __future__ import annotations

import io
import json
import os
import stat
import sys
import time
from pathlib import Path

import pytest

import scripts.init_local_probe_trust as init_module
import scripts.run_with_local_probe_trust as runner_module
from longworld.core.attestation import (
    ATTESTATION_ENV,
    ATTESTATION_ENVIRONMENT_ENV,
    ATTESTATION_ROLES,
    ROLE_KEY_ENVS,
    ROLE_KEY_ID_ENVS,
)
from scripts.init_local_probe_trust import (
    TRUST_FILENAME,
    ProbeTrustError,
    initialize_local_probe_trust,
)
from scripts.run_with_local_probe_trust import load_local_probe_trust, run_with_trust


def _private_parent(tmp_path: Path) -> Path:
    parent = tmp_path / "private"
    parent.mkdir(mode=0o700)
    parent.chmod(0o700)
    return parent


def _initialized_trust(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    trust_file = initialize_local_probe_trust(_private_parent(tmp_path) / "trust")
    return trust_file, json.loads(trust_file.read_text())


def test_initializer_creates_private_unique_role_credentials(tmp_path: Path) -> None:
    trust_file, payload = _initialized_trust(tmp_path)

    assert stat.S_IMODE(trust_file.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(trust_file.stat().st_mode) == 0o600
    assert payload["schema_version"] == "longworld-local-probe-trust-v1"
    assert payload["environment"] == "probe"
    assert str(payload["probe_id"]).startswith("local-probe-")
    roles = payload["roles"]
    assert isinstance(roles, dict)
    assert set(roles) == set(ATTESTATION_ROLES)
    keys = [roles[role]["key"] for role in ATTESTATION_ROLES]
    key_ids = [roles[role]["key_id"] for role in ATTESTATION_ROLES]
    assert len(set(keys)) == len(ATTESTATION_ROLES)
    assert len(set(key_ids)) == len(ATTESTATION_ROLES)
    assert all(len(key.encode()) >= 32 for key in keys)
    assert all("secret-of-at-least" not in key for key in keys)


def test_initializer_never_overwrites_existing_trust_file(tmp_path: Path) -> None:
    trust_file, _ = _initialized_trust(tmp_path)
    original = trust_file.read_bytes()

    with pytest.raises(ProbeTrustError, match="already exists"):
        initialize_local_probe_trust(trust_file.parent)

    assert trust_file.read_bytes() == original


def test_initializer_rejects_repo_internal_path() -> None:
    repo_internal = Path(__file__).resolve().parents[1] / ".local-probe-test"

    with pytest.raises(ProbeTrustError, match="outside the repository"):
        initialize_local_probe_trust(repo_internal)

    assert not repo_internal.exists()


def test_initializer_rejects_symlink_or_unsafe_existing_root(tmp_path: Path) -> None:
    parent = _private_parent(tmp_path)
    real_root = parent / "real"
    real_root.mkdir(mode=0o700)
    symlink_root = parent / "link"
    symlink_root.symlink_to(real_root, target_is_directory=True)

    with pytest.raises(ProbeTrustError, match="symlink"):
        initialize_local_probe_trust(symlink_root)

    unsafe_root = parent / "unsafe"
    unsafe_root.mkdir(mode=0o755)
    with pytest.raises(ProbeTrustError, match="mode 0700"):
        initialize_local_probe_trust(unsafe_root)


def test_initializer_rejects_symlinked_parent_component(tmp_path: Path) -> None:
    parent = _private_parent(tmp_path)
    real_parent = parent / "real-parent"
    real_parent.mkdir(mode=0o700)
    linked_parent = parent / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)

    with pytest.raises(ProbeTrustError, match="symlink"):
        initialize_local_probe_trust(linked_parent / "trust")


def test_initializer_rejects_replaceable_parent_directory(tmp_path: Path) -> None:
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o770)
    parent.chmod(0o770)

    with pytest.raises(ProbeTrustError, match="mode 0700"):
        initialize_local_probe_trust(parent / "trust")


def test_initializer_rejects_posix_acl(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parent = _private_parent(tmp_path)
    monkeypatch.setattr(init_module, "_has_posix_acl", lambda _fd: True)

    with pytest.raises(ProbeTrustError, match="POSIX ACL"):
        initialize_local_probe_trust(parent / "trust")


def test_runner_injects_only_current_probe_role_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    trust_file, payload = _initialized_trust(tmp_path)
    monkeypatch.setenv(ATTESTATION_ENV, "obsolete-shared-attestation-key-material")
    monkeypatch.setenv(
        "LONGWORLD_PREDECESSOR_GATE_ATTESTATION_KEY", "obsolete-predecessor-key"
    )
    monkeypatch.setenv("HF_TOKEN", "hf_must_not_reach_probe_child_123456789")
    monkeypatch.setenv("GH_TOKEN", "ghp_must_not_reach_probe_child_123456789")
    script = (
        "import json, os; "
        "print(json.dumps({"
        "'environment': os.environ['LONGWORLD_ATTESTATION_ENVIRONMENT'], "
        "'legacy_present': 'LONGWORLD_ATTESTATION_KEY' in os.environ, "
        "'predecessor_present': "
        "'LONGWORLD_PREDECESSOR_GATE_ATTESTATION_KEY' in os.environ, "
        "'hf_present': 'HF_TOKEN' in os.environ, "
        "'gh_present': 'GH_TOKEN' in os.environ, "
        "'combined_mode': os.environ.get('LONGWORLD_LOCAL_PROBE_COMBINED_ROLES'), "
        "'key_ids': sorted(v for k, v in os.environ.items() "
        "if k.endswith('_ATTESTATION_KEY_ID')) , "
        "'key_lengths': sorted(len(v) for k, v in os.environ.items() "
        "if k.endswith('_ATTESTATION_KEY'))}))"
    )

    roles = ("source", "candidate")
    result = run_with_trust(
        trust_file,
        [sys.executable, "-c", script],
        roles=roles,
        allow_combined_roles=True,
    )
    captured = capfd.readouterr()
    child = json.loads(captured.out)

    assert result == 0
    assert captured.err == ""
    assert child == {
        "environment": "probe",
        "legacy_present": False,
        "predecessor_present": False,
        "hf_present": False,
        "gh_present": False,
        "combined_mode": "non_independent_local_diagnostic",
        "key_ids": sorted(payload["roles"][role]["key_id"] for role in roles),
        "key_lengths": sorted(len(payload["roles"][role]["key"]) for role in roles),
    }


def test_runner_redacts_direct_secret_output(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    trust_file, payload = _initialized_trust(tmp_path)
    script = (
        "import os, sys; "
        "print(os.environ['LONGWORLD_SOURCE_ATTESTATION_KEY']); "
        "print(os.environ['LONGWORLD_REPORT_ATTESTATION_KEY'], file=sys.stderr)"
    )

    assert (
        run_with_trust(
            trust_file,
            [sys.executable, "-c", script],
            roles=("source", "report"),
            allow_combined_roles=True,
        )
        == 0
    )
    captured = capfd.readouterr()

    assert captured.out == "[REDACTED]\n"
    assert captured.err == "[REDACTED]\n"
    for role in ATTESTATION_ROLES:
        secret = payload["roles"][role]["key"]
        assert secret not in captured.out
        assert secret not in captured.err


def test_stream_redaction_covers_secret_split_across_read_boundaries() -> None:
    secret = b"role-secret-split-across-two-reads"

    class Chunked:
        def __init__(self) -> None:
            self.chunks = [b"before:" + secret[:13], secret[13:] + b":after", b""]

        def read(self, _size: int) -> bytes:
            return self.chunks.pop(0)

    destination = io.BytesIO()
    runner_module._stream_redacted(Chunked(), destination, (secret,))

    assert destination.getvalue() == b"before:[REDACTED]:after"


def test_runner_fails_closed_when_output_relay_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust_file, _ = _initialized_trust(tmp_path)

    def fail_relay(*_args) -> None:
        raise RuntimeError("output sink failed")

    monkeypatch.setattr(runner_module, "_stream_redacted", fail_relay)

    with pytest.raises(ProbeTrustError, match="output relay failed"):
        run_with_trust(
            trust_file,
            [sys.executable, "-c", "print('probe output')"],
            roles=("source",),
        )


def test_runner_kills_child_that_ignores_sigterm_after_relay_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust_file, _ = _initialized_trust(tmp_path)
    monkeypatch.setattr(runner_module, "_PROCESS_POLL_INTERVAL_SECONDS", 0.01)
    monkeypatch.setattr(runner_module, "_PROCESS_TERMINATE_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(runner_module, "_PROCESS_KILL_TIMEOUT_SECONDS", 0.5)

    def fail_after_child_is_ready(source, *_args) -> None:
        source.read(1)
        raise RuntimeError("output sink failed")

    monkeypatch.setattr(runner_module, "_stream_redacted", fail_after_child_is_ready)
    script = (
        "import signal, time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); "
        "time.sleep(60)"
    )

    started = time.monotonic()
    with pytest.raises(ProbeTrustError, match="output relay failed"):
        run_with_trust(
            trust_file,
            [sys.executable, "-c", script],
            roles=("source",),
        )

    assert time.monotonic() - started < 3.0


def test_runner_kills_grandchild_that_keeps_output_pipe_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    trust_file, _ = _initialized_trust(tmp_path)
    monkeypatch.setattr(runner_module, "_OUTPUT_DRAIN_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(runner_module, "_PROCESS_TERMINATE_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(runner_module, "_PROCESS_KILL_TIMEOUT_SECONDS", 0.5)
    script = (
        "import subprocess, sys, time; "
        "subprocess.Popen([sys.executable, '-c', "
        "'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "time.sleep(60)']); "
        "time.sleep(0.2); "
        "print('parent finished', flush=True)"
    )

    started = time.monotonic()
    with pytest.raises(ProbeTrustError, match="output relay did not terminate"):
        run_with_trust(
            trust_file,
            [sys.executable, "-c", script],
            roles=("source",),
        )

    assert time.monotonic() - started < 3.0
    assert capfd.readouterr().out == "parent finished\n"


def test_runner_terminates_process_group_when_parent_is_interrupted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust_file, _ = _initialized_trust(tmp_path)
    terminated: list[object] = []

    class InterruptedProcess:
        pid = 12345
        stdout = io.BytesIO()
        stderr = io.BytesIO()

        def wait(self, *, timeout: float) -> int:
            raise KeyboardInterrupt

    process = InterruptedProcess()
    monkeypatch.setattr(runner_module.subprocess, "Popen", lambda *_a, **_kw: process)
    monkeypatch.setattr(
        runner_module,
        "_terminate_process_group",
        lambda value: terminated.append(value) or -9,
    )

    with pytest.raises(KeyboardInterrupt):
        run_with_trust(
            trust_file,
            [sys.executable, "-c", "pass"],
            roles=("source",),
        )

    assert terminated == [process]


@pytest.mark.parametrize(
    ("setup_component", "error_type"),
    [("event", KeyboardInterrupt), ("thread", RuntimeError)],
)
def test_runner_terminates_process_group_when_relay_setup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    setup_component: str,
    error_type: type[BaseException],
) -> None:
    trust_file, _ = _initialized_trust(tmp_path)
    terminated: list[object] = []

    class SetupProcess:
        pid = 12345
        stdout = io.BytesIO()
        stderr = io.BytesIO()

    process = SetupProcess()
    monkeypatch.setattr(runner_module.subprocess, "Popen", lambda *_a, **_kw: process)
    monkeypatch.setattr(
        runner_module,
        "_terminate_process_group",
        lambda value: terminated.append(value) or -9,
    )

    def fail_setup(*_args, **_kwargs):
        raise error_type

    monkeypatch.setattr(runner_module.threading, setup_component.title(), fail_setup)

    with pytest.raises(error_type):
        run_with_trust(
            trust_file,
            [sys.executable, "-c", "pass"],
            roles=("source",),
        )

    assert terminated == [process]


def test_runner_rejects_symlink_and_insecure_file_mode(tmp_path: Path) -> None:
    trust_file, _ = _initialized_trust(tmp_path)
    symlink = trust_file.parent / "trust-link.json"
    symlink.symlink_to(trust_file)
    with pytest.raises(ProbeTrustError, match="symlink"):
        load_local_probe_trust(symlink)

    trust_file.chmod(0o644)
    with pytest.raises(ProbeTrustError, match="mode 0600"):
        load_local_probe_trust(trust_file)


def test_runner_rejects_trust_root_in_replaceable_parent(tmp_path: Path) -> None:
    safe_file, payload = _initialized_trust(tmp_path)
    shared_parent = tmp_path / "shared-runner-parent"
    shared_parent.mkdir(mode=0o770)
    shared_parent.chmod(0o770)
    trust_root = shared_parent / "trust"
    trust_root.mkdir(mode=0o700)
    trust_file = trust_root / safe_file.name
    trust_file.write_text(json.dumps(payload))
    trust_file.chmod(0o600)

    with pytest.raises(ProbeTrustError, match="trust parent must have mode 0700"):
        load_local_probe_trust(trust_file)


def test_runner_rejects_file_acl_or_hard_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trust_file, _ = _initialized_trust(tmp_path)
    monkeypatch.setattr(runner_module, "_has_posix_acl", lambda _fd: True)
    with pytest.raises(ProbeTrustError, match="POSIX ACL"):
        load_local_probe_trust(trust_file)

    monkeypatch.undo()
    hard_link = trust_file.parent / "copied-trust.json"
    os.link(trust_file, hard_link)
    with pytest.raises(ProbeTrustError, match="hard links"):
        load_local_probe_trust(trust_file)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda value: value.update({"unexpected": True}), "unexpected fields"),
        (
            lambda value: value["roles"]["report"].update(
                {"key": value["roles"]["source"]["key"]}
            ),
            "role keys must be unique",
        ),
        (
            lambda value: value["roles"]["report"].update(
                {"key_id": value["roles"]["source"]["key_id"]}
            ),
            "role key IDs must be unique",
        ),
        (
            lambda value: value["roles"]["report"].update(
                {"key": "use-a-secret-of-at-least-32-bytes"}
            ),
            "invalid key",
        ),
        (
            lambda value: value["roles"]["report"].update(
                {"key_id": "production-report-wrong-environment"}
            ),
            "invalid key ID",
        ),
    ],
)
def test_runner_rejects_invalid_or_placeholder_credentials(
    tmp_path: Path, mutation, message: str
) -> None:
    trust_file, payload = _initialized_trust(tmp_path)
    mutation(payload)
    trust_file.write_text(json.dumps(payload))
    trust_file.chmod(0o600)

    with pytest.raises(ProbeTrustError, match=message):
        load_local_probe_trust(trust_file)


def test_runner_rejects_duplicate_json_members(tmp_path: Path) -> None:
    trust_file, payload = _initialized_trust(tmp_path)
    encoded = json.dumps(payload)
    encoded = encoded.replace(
        '"environment": "probe"',
        '"environment": "probe", "environment": "probe"',
        1,
    )
    trust_file.write_text(encoded)
    trust_file.chmod(0o600)

    with pytest.raises(ProbeTrustError, match="duplicate JSON field"):
        load_local_probe_trust(trust_file)


def test_runner_passes_argv_without_shell_interpretation(tmp_path: Path, capfd) -> None:
    trust_file, _ = _initialized_trust(tmp_path)
    literal = "$(printf unsafe);still-one-argument"

    assert (
        run_with_trust(
            trust_file,
            [sys.executable, "-c", "import sys; print(sys.argv[1])", literal],
            roles=("source",),
        )
        == 0
    )

    assert capfd.readouterr().out == literal + "\n"


def test_runner_requires_explicit_roles_and_redacts_opt_in_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    trust_file, _ = _initialized_trust(tmp_path)
    passed_secret = "ghp_explicit_probe_passthrough_123456789"
    monkeypatch.setenv("GH_TOKEN", passed_secret)
    script = "import os; print(os.environ['GH_TOKEN'])"

    with pytest.raises(ProbeTrustError, match="at least one attestation role"):
        run_with_trust(trust_file, [sys.executable, "-c", script], roles=())
    assert (
        run_with_trust(
            trust_file,
            [sys.executable, "-c", script],
            roles=("source",),
            pass_env=("GH_TOKEN",),
        )
        == 0
    )

    assert capfd.readouterr().out == "[REDACTED]\n"


def test_runner_rejects_attestation_environment_passthrough(tmp_path: Path) -> None:
    trust_file, _ = _initialized_trust(tmp_path)

    with pytest.raises(ProbeTrustError, match="attestation environment"):
        run_with_trust(
            trust_file,
            [sys.executable, "-c", "pass"],
            roles=("source",),
            pass_env=(ROLE_KEY_ENVS["report"],),
        )


def test_runner_requires_explicit_opt_in_for_combined_roles(tmp_path: Path) -> None:
    trust_file, _ = _initialized_trust(tmp_path)

    with pytest.raises(ProbeTrustError, match="combined attestation roles"):
        run_with_trust(
            trust_file,
            [sys.executable, "-c", "pass"],
            roles=("source", "candidate"),
        )


@pytest.mark.parametrize("name", ["LD_PRELOAD", "PYTHONPATH", "BASH_ENV"])
def test_runner_rejects_execution_control_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    trust_file, _ = _initialized_trust(tmp_path)
    monkeypatch.setenv(name, "/tmp/unsafe-probe-injection")

    with pytest.raises(ProbeTrustError, match="dangerous execution environment"):
        run_with_trust(
            trust_file,
            [sys.executable, "-c", "pass"],
            roles=("source",),
            pass_env=(name,),
        )


def test_default_filename_is_stable() -> None:
    assert TRUST_FILENAME == "local_probe_trust.json"
    assert ATTESTATION_ENVIRONMENT_ENV == "LONGWORLD_ATTESTATION_ENVIRONMENT"
    assert set(ROLE_KEY_ENVS) == set(ATTESTATION_ROLES)
    assert set(ROLE_KEY_ID_ENVS) == set(ATTESTATION_ROLES)
