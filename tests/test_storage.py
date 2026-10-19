"""T-01 / SR-30: upload filenames can't escape the storage root or a workspace prefix (defect D-01)."""

from __future__ import annotations

import pytest

from core.storage import LocalStorage, SupabaseStorage, safe_filename


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("policy.md", "policy.md"),
        ("../../etc/passwd.md", "passwd.md"),
        ("..\\..\\win\\evil.txt", "evil.txt"),
        ("/abs/path/report.pdf", "report.pdf"),
        ("other-workspace/../secret.md", "secret.md"),
        ("..", "upload"),
        ("", "upload"),
        (None, "upload"),
        (".hidden.md", "hidden.md"),
        ("na\x00me\n.txt", "name.txt"),
        ("my report (v2).pdf", "my report (v2).pdf"),
        ("rés;umé<>.md", "r_s_um___.md"),
    ],
)
def test_safe_filename(raw, expected):
    assert safe_filename(raw) == expected


def test_safe_filename_limits_length_and_keeps_extension():
    name = safe_filename("a" * 500 + ".pdf")
    assert len(name) <= 200 and name.endswith(".pdf")


def test_local_storage_round_trip(tmp_path):
    s = LocalStorage(tmp_path / "root")
    s.save("ws1/doc.md", b"hello")
    assert s.load("ws1/doc.md") == b"hello"
    s.delete("ws1/doc.md")
    assert not (tmp_path / "root" / "ws1" / "doc.md").exists()


@pytest.mark.parametrize("path", ["ws1/../../escaped.md", "../escaped.md", "/etc/passwd"])
def test_local_storage_rejects_paths_outside_root(tmp_path, path):
    s = LocalStorage(tmp_path / "root")
    with pytest.raises(ValueError):
        s.save(path, b"x")
    with pytest.raises(ValueError):
        s.load(path)
    with pytest.raises(ValueError):
        s.delete(path)
    assert not (tmp_path / "escaped.md").exists()


@pytest.mark.parametrize("path", ["ws1/../ws2/doc.md", "/ws1/doc.md", "ws1//doc.md", ""])
def test_supabase_storage_rejects_unsafe_object_paths(path):
    s = SupabaseStorage("https://example.supabase.co", "documents", "key", client=None)
    with pytest.raises(ValueError):
        s.save(path, b"x")
