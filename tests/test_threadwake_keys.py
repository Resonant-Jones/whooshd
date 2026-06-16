from __future__ import annotations

from whooshd.runtime.threadwake.compiler import compile_prompt_graph
from whooshd.runtime.threadwake.keys import build_threadwake_cache_key, hash_json, sha256_hex


def test_sha256_hex_is_deterministic():
    assert sha256_hex("same") == sha256_hex("same")
    assert sha256_hex("same") != sha256_hex("different")


def test_hash_json_normalizes_key_order():
    assert hash_json({"b": 2, "a": 1}) == hash_json({"a": 1, "b": 2})


def test_cache_key_is_deterministic_for_same_graph():
    graph = compile_prompt_graph(
        model_id="m",
        backend="stub",
        messages=[
            {"role": "system", "content": "Stable"},
            {"role": "user", "content": "Latest"},
        ],
    )

    assert build_threadwake_cache_key(graph) == build_threadwake_cache_key(graph)


def test_cache_key_changes_with_backend():
    first = compile_prompt_graph(
        model_id="m",
        backend="stub",
        messages=[
            {"role": "system", "content": "Stable"},
            {"role": "user", "content": "Latest"},
        ],
    )
    second = compile_prompt_graph(
        model_id="m",
        backend="llama_cpp",
        messages=[
            {"role": "system", "content": "Stable"},
            {"role": "user", "content": "Latest"},
        ],
    )

    assert build_threadwake_cache_key(first) != build_threadwake_cache_key(second)
