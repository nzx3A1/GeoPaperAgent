from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path

import pytest

from app.infrastructure.sshClient import SSHClient, SSHClientError, SSHConfig, _local_destination


@dataclass
class FakeAttributes:
    filename: str
    st_mode: int


class FakeSFTP:
    def __init__(self) -> None:
        self.entries = {
            "/papers": [
                FakeAttributes("subdirectory", stat.S_IFDIR),
                FakeAttributes("notes.txt", stat.S_IFREG),
                FakeAttributes("B.PDF", stat.S_IFREG),
                FakeAttributes("a.pdf", stat.S_IFREG),
            ],
            "/papers/subdirectory": [FakeAttributes("nested.pdf", stat.S_IFREG)],
        }
        self.payloads = {
            "/papers/a.pdf": b"a",
            "/papers/B.PDF": b"b",
            "/papers/subdirectory/nested.pdf": b"nested",
        }

    def listdir_attr(self, directory: str) -> list[FakeAttributes]:
        return self.entries[directory]

    def get(self, remote_path: str, local_path: str, callback=None) -> None:
        payload = self.payloads[remote_path]
        Path(local_path).write_bytes(payload)
        if callback:
            callback(len(payload), len(payload))


class FakeSSHClient(SSHClient):
    def __init__(self, sftp: FakeSFTP) -> None:
        super().__init__(SSHConfig(host="example.test", username="tester"))
        self.fake_sftp = sftp

    def _get_sftp(self) -> FakeSFTP:
        return self.fake_sftp


def test_list_pdf_files_filters_sorts_and_recurses() -> None:
    client = FakeSSHClient(FakeSFTP())

    assert client.list_pdf_files("/papers") == ["/papers/a.pdf", "/papers/B.PDF"]
    assert client.list_pdf_files("/papers", recursive=True) == [
        "/papers/a.pdf",
        "/papers/B.PDF",
        "/papers/subdirectory/nested.pdf",
    ]


def test_download_pdf_files_respects_count_and_reports_progress(tmp_path: Path) -> None:
    client = FakeSSHClient(FakeSFTP())
    progress: list[tuple[str, int, int]] = []

    paths = client.download_pdf_files(
        tmp_path,
        count=2,
        remote_directory="/papers",
        recursive=True,
        progress=lambda path, done, total: progress.append((path, done, total)),
    )

    assert paths == [tmp_path / "a.pdf", tmp_path / "B.PDF"]
    assert [path.read_bytes() for path in paths] == [b"a", b"b"]
    assert progress == [
        ("/papers/a.pdf", 1, 1),
        ("/papers/B.PDF", 1, 1),
    ]
    assert not list(tmp_path.glob("*.part"))


def test_download_skips_existing_file_unless_overwrite_is_enabled(tmp_path: Path) -> None:
    client = FakeSSHClient(FakeSFTP())
    destination = tmp_path / "a.pdf"
    destination.write_bytes(b"existing")

    assert client.download_pdf_files(tmp_path, 1, remote_directory="/papers") == []
    assert destination.read_bytes() == b"existing"

    assert client.download_pdf_files(tmp_path, 1, remote_directory="/papers", overwrite=True) == [destination]
    assert destination.read_bytes() == b"a"


def test_count_cannot_be_negative(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="count"):
        FakeSSHClient(FakeSFTP()).download_pdf_files(tmp_path, -1)


def test_local_destination_rejects_paths_outside_remote_root(tmp_path: Path) -> None:
    with pytest.raises(SSHClientError, match="outside"):
        _local_destination(tmp_path, "/papers", "/other/file.pdf")
