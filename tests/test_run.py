"""The commands of the hook are run with _run, these tests need neither helm nor kubectl."""

import sys
from unittest import mock

import pre_commit_flux.check_flux_helm_values as testm


def test_output_and_exit_code_of_a_command() -> None:
    result = testm._run(
        [
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)",
        ]
    )

    assert result.returncode == 3
    # both streams, their order in the pipe is not defined
    assert sorted(result.stdout.split()) == ["err", "out"]


def test_command_that_is_not_installed() -> None:
    result = testm._run(["no-such-command-for-the-hook"])

    assert result.returncode == 127
    assert result.stdout == "no-such-command-for-the-hook: command not found\n"


def test_command_that_takes_too_long() -> None:
    with mock.patch.object(testm, "TIMEOUT_SECONDS", 1):
        result = testm._run([sys.executable, "-c", "import time; time.sleep(60)"])

    assert result.returncode == 124
    assert "timed out after 1 seconds" in result.stdout
