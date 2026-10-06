"""An unstamped row reports the dimension it measurably has, and an unknown model."""
from __future__ import annotations

import numpy as np

from memvault.memory import embedder_of
from memvault.vector_index import to_blob


def test_dimension_is_measured_from_the_blob():
    row = {"embedding": to_blob(np.zeros(1024, dtype=np.float32)), "metadata": "{}"}
    assert embedder_of(row) == ("unknown", 1024)


def test_a_stamp_still_wins_over_measurement():
    row = {"embedding": to_blob(np.zeros(1024, dtype=np.float32)),
           "embedder": "openai", "embed_dim": 1024, "metadata": "{}"}
    assert embedder_of(row) == ("openai", 1024)


def test_no_blob_and_no_stamp_is_unknown_not_local():
    assert embedder_of({"metadata": "{}"}) == ("unknown", 384)
    assert embedder_of({"metadata": '{"embedder": "local", "embed_dim": 384}'}) == ("local", 384)
