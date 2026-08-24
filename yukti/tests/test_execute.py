import pytest

from IPython.core.interactiveshell import InteractiveShell

from yukti.execute import EMPTY, run_source


@pytest.fixture(scope="module")
def shell():
    """One real IPython shell, so the test exercises the hooks Yukti patches.

    A kernel would publish the same outputs on iopub; only the transport
    differs, and no test can start a kernel in milliseconds.
    """
    return InteractiveShell.instance()


def test_what_the_cell_printed_reaches_both_readers(shell):
    outputs, said = run_source(shell, "print('hello')")

    assert outputs == [
        {"output_type": "stream", "name": "stdout", "text": "hello\n"}
    ]
    assert said == "hello"


def test_the_value_of_the_last_expression_is_an_output(shell):
    """The capturing display hook holds it, so the value lands in the cell
    instead of in the %%ask cell that asked for the run."""
    outputs, said = run_source(shell, "2 + 3")

    assert outputs[0]["output_type"] == "display_data"
    assert outputs[0]["data"]["text/plain"] == "5"
    assert said == "5"


def test_the_run_sees_the_names_the_earlier_cells_bound(shell):
    run_source(shell, "answer = 6 * 7")

    _outputs, said = run_source(shell, "print(answer)")

    assert said == "42"


def test_a_failure_becomes_an_error_output_the_model_can_read(shell):
    outputs, said = run_source(shell, "1 / 0")

    assert outputs[-1]["output_type"] == "error"
    assert outputs[-1]["ename"] == "ZeroDivisionError"
    assert "ZeroDivisionError" in said
    # The model reads characters, so the colours of the traceback are gone.
    assert "\x1b[" not in said


def test_the_traceback_hook_is_put_back_after_a_failure(shell):
    original = shell._showtraceback

    run_source(shell, "raise ValueError('no')")

    assert shell._showtraceback == original


def test_a_cell_that_prints_nothing_says_so(shell):
    """A tool result of an empty string reads as a broken run, so the sentence
    tells the model the cell ran."""
    outputs, said = run_source(shell, "silent = 1")

    assert outputs == []
    assert said == EMPTY
