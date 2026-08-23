from yukti.context import OUTPUT_LIMIT_BYTES, build_transcript


def test_transcript_lists_cells_outputs_and_then_the_question():
    cells = [
        {"cell_type": "code", "cell_id": "abc", "source": "print(1)",
         "outputs": [{"content": "1"}]},
        {"cell_type": "markdown", "cell_id": "def", "source": "Notes"},
    ]

    transcript = build_transcript(cells, "  why?  ")

    assert transcript == (
        "[code cell_id=abc]\nprint(1)\n\n"
        "[output]\n1\n\n"
        "[markdown cell_id=def]\nNotes\n\n"
        "[user]\nwhy?"
    )


def test_a_large_output_is_truncated_in_the_middle():
    cells = [
        {
            "cell_type": "code",
            "cell_id": "abc",
            "source": "print(rows)",
            "outputs": [{"content": "x" * (OUTPUT_LIMIT_BYTES + 100)}],
        }
    ]

    transcript = build_transcript(cells, "why?")

    assert "[output truncated: original 8 KB]" in transcript
    assert len(transcript.encode("utf-8")) < OUTPUT_LIMIT_BYTES + 200
