"""SSH/SFTP utilities for downloading PDFs from the paper server.

Connection settings have usable defaults and can be overridden with ``SSH_*``
environment variables. This module is reusable from Python and executable as a CLI::

    python -m app.infrastructure.sshClient ./data/pdfs --count 20

中文说明：

- 该模块提供通过 SSH/SFTP 从远程服务器批量列出和下载 PDF 文件的工具。
- 配置优先从环境变量读取（以 `SSH_` 开头的变量），也可以通过命令行参数覆盖。
- 常用环境变量示例：
  - `SSH_HOST`, `SSH_PORT`, `SSH_USERNAME`, `SSH_PASSWORD`
  - `SSH_KEY_FILE`（私钥路径）, `SSH_KNOWN_HOSTS`（已知主机文件路径）
  - `SSH_REMOTE_PDF_DIR`（远程 PDF 根目录，默认：/house1/KUN_QT_DATA/PDF_all）

用法示例（命令行）：

    # 列出远程目录中的 PDF 文件（只列出，不下载）
    python -m app.infrastructure.sshClient --list-only

    # 下载前 20 个 PDF 到本地 data/pdfs
    python -m app.infrastructure.sshClient data/pdfs --count 20

用法示例（在代码中调用）：

    from app.infrastructure.sshClient import download_pdf_files, SSHConfig

    # 使用环境变量配置
    download_pdf_files("./data/pdfs", count=10)

    # 使用自定义 SSHConfig
    cfg = SSHConfig(host="10.0.0.1", username="user", password="pw")
    download_pdf_files("./data/pdfs", count=10, config=cfg)

安全与行为说明：

- 下载时先写入临时文件（带 `.part` 后缀），成功后再原子替换为目标文件，避免不完整文件留下。
- 当 `recursive=True` 时会保留远程目录结构到本地。
"""

from __future__ import annotations

import argparse
import getpass
import os
import posixpath
import stat
import sys
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath

import paramiko
from dotenv import load_dotenv

DEFAULT_HOST = "10.16.35.175"
DEFAULT_PORT = 22
DEFAULT_USERNAME = "nzx"
DEFAULT_PASSWORD = "niuzhixin#1504"
DEFAULT_REMOTE_PDF_DIR = "/house1/KUN_QT_DATA/PDF_all"

ProgressCallback = Callable[[str, int, int], None]


class SSHClientError(RuntimeError):
    """当 SSH 或 SFTP 操作失败时抛出。

    中文说明：该异常封装了所有网络/文件相关错误，调用方可捕获并显示友好错误信息。
    """


def _env_number(name: str, default: int | float, type_: type[int] | type[float]) -> int | float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return type_(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid {type_.__name__}") from exc


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    normalised = value.strip().casefold()
    if normalised in {"1", "true", "yes", "on"}:
        return True
    if normalised in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false")


@dataclass(frozen=True, slots=True)
class SSHConfig:
    """SSH 连接设置。

    中文说明：
    - 该 dataclass 封装主机、端口、用户名、密码、私钥文件、已知主机文件等配置。
    - 推荐使用 `SSHConfig.from_env()` 从环境变量加载配置，方便在不同环境（开发/部署）切换。
    """

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    username: str = DEFAULT_USERNAME
    password: str | None = DEFAULT_PASSWORD
    key_filename: Path | None = None
    known_hosts: Path | None = None
    accept_unknown_host_key: bool = False
    timeout: float = 15.0
    keepalive_interval: int = 30

    def __post_init__(self) -> None:
        if not self.host.strip() or not self.username.strip():
            raise ValueError("SSH host and username must not be empty")
        if not 1 <= self.port <= 65535:
            raise ValueError("SSH port must be between 1 and 65535")
        if self.timeout <= 0 or self.keepalive_interval < 0:
            raise ValueError("timeout must be positive and keepalive_interval non-negative")

    @classmethod
    def from_env(cls) -> SSHConfig:
        """从 backend/.env 和当前进程环境读取 ``SSH_*`` 配置。"""

        load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
        key_file = os.getenv("SSH_KEY_FILE", "").strip()
        known_hosts = os.getenv("SSH_KNOWN_HOSTS", "").strip()
        return cls(
            host=os.getenv("SSH_HOST", DEFAULT_HOST).strip(),
            port=int(_env_number("SSH_PORT", DEFAULT_PORT, int)),
            username=os.getenv("SSH_USERNAME", DEFAULT_USERNAME).strip(),
            password=os.getenv("SSH_PASSWORD", DEFAULT_PASSWORD) or None,
            key_filename=Path(key_file).expanduser() if key_file else None,
            known_hosts=Path(known_hosts).expanduser() if known_hosts else None,
            accept_unknown_host_key=_env_bool("SSH_ACCEPT_UNKNOWN_HOST_KEY"),
            timeout=float(_env_number("SSH_TIMEOUT", 15.0, float)),
            keepalive_interval=int(_env_number("SSH_KEEPALIVE_INTERVAL", 30, int)),
        )


class SSHClient:
    """Context-managed client for listing and downloading remote PDF files."""

    """
    中文说明：
    - 使用此类可以建立 SSH 连接并通过 SFTP 列出或下载远程 PDF 文件。
    - 支持上下文管理器用法：

        with SSHClient() as client:
            client.list_pdf_files("/remote/dir")

    - 主要方法：`list_pdf_files`, `download_pdf_files`。
    """

    def __init__(self, config: SSHConfig | None = None) -> None:
        self.config = config or SSHConfig.from_env()
        self._client: paramiko.SSHClient | None = None
        self._sftp: paramiko.SFTPClient | None = None

    @property
    def connected(self) -> bool:
        if self._client is None:
            return False
        transport = self._client.get_transport()
        return bool(transport and transport.is_active())

    def connect(self) -> SSHClient:
        """Connect to the configured server; repeated calls are harmless."""
        if self.connected:
            return self
        self.close()
        client = paramiko.SSHClient()
        try:
            if self.config.known_hosts:
                client.load_host_keys(str(self.config.known_hosts))
            else:
                client.load_system_host_keys()
            policy = paramiko.AutoAddPolicy() if self.config.accept_unknown_host_key else paramiko.RejectPolicy()
            client.set_missing_host_key_policy(policy)
            client.connect(
                hostname=self.config.host,
                port=self.config.port,
                username=self.config.username,
                password=self.config.password,
                key_filename=str(self.config.key_filename) if self.config.key_filename else None,
                timeout=self.config.timeout,
                banner_timeout=self.config.timeout,
                auth_timeout=self.config.timeout,
                allow_agent=True,
                look_for_keys=self.config.key_filename is None and self.config.password is None,
            )
            transport = client.get_transport()
            if transport is None or not transport.is_active():
                raise SSHClientError("SSH transport did not become active")
            if self.config.keepalive_interval:
                transport.set_keepalive(self.config.keepalive_interval)
        except Exception as exc:
            client.close()
            if isinstance(exc, SSHClientError):
                raise
            address = f"{self.config.username}@{self.config.host}:{self.config.port}"
            raise SSHClientError(f"Could not connect to {address}: {exc}") from exc
        self._client = client
        return self

    def close(self) -> None:
        """Close all network resources; repeated calls are safe."""
        try:
            if self._sftp is not None:
                self._sftp.close()
        finally:
            self._sftp = None
            if self._client is not None:
                self._client.close()
            self._client = None

    def __enter__(self) -> SSHClient:
        return self.connect()

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def _get_sftp(self) -> paramiko.SFTPClient:
        if not self.connected:
            self.connect()
        if self._sftp is None:
            assert self._client is not None
            try:
                self._sftp = self._client.open_sftp()
            except (OSError, paramiko.SSHException) as exc:
                raise SSHClientError(f"Could not open SFTP: {exc}") from exc
        return self._sftp

    def _walk_pdfs(self, directory: str, recursive: bool) -> Iterator[str]:
        entries = sorted(self._get_sftp().listdir_attr(directory), key=lambda item: item.filename.casefold())
        for entry in entries:
            if entry.filename in {".", ".."}:
                continue
            remote_path = posixpath.join(directory, entry.filename)
            if stat.S_ISDIR(entry.st_mode):
                if recursive:
                    yield from self._walk_pdfs(remote_path, True)
            elif entry.filename.casefold().endswith(".pdf"):
                yield remote_path

    def list_pdf_files(self, remote_directory: str = DEFAULT_REMOTE_PDF_DIR, *, recursive: bool = False) -> list[str]:
        """列出远程 PDF 文件，按不区分大小写的确定顺序排序。

        参数：
        - `remote_directory`：远程目录路径（POSIX 风格）。
        - `recursive`：若为 True，则递归遍历子目录。

        返回值：
        - 返回远程 PDF 文件的绝对路径列表（字符串）。
        """
        remote_directory = _normalise_remote_directory(remote_directory)
        try:
            return sorted(self._walk_pdfs(remote_directory, recursive), key=str.casefold)
        except (OSError, paramiko.SSHException) as exc:
            raise SSHClientError(f"Could not list {remote_directory!r}: {exc}") from exc

    def download_pdf_files(
        self,
        local_directory: str | Path,
        count: int | None = None,
        *,
        remote_directory: str = DEFAULT_REMOTE_PDF_DIR,
        recursive: bool = False,
        overwrite: bool = False,
        progress: ProgressCallback | None = None,
    ) -> list[Path]:
        """下载最多 `count` 个 PDF 文件并返回新写入的本地路径列表。

        行为说明：
        - 下载到临时文件（以 `.part` 结尾），下载成功后使用原子替换（`os.replace`）移动到目标路径。
        - 当 `recursive=True` 时，会在本地保留远程目录结构。
        - 若目标文件已存在且 `overwrite=False`，则跳过该文件。

        参数：
        - `local_directory`: 本地根目录，接受字符串或 `Path`。
        - `count`: 最大下载数量，None 表示下载所有匹配文件。
        - 其他参数与命令行选项行为一致。
        """
        if count is not None and count < 0:
            raise ValueError("count must be zero or greater")
        remote_directory = _normalise_remote_directory(remote_directory)
        remote_files = self.list_pdf_files(remote_directory, recursive=recursive)
        remote_files = remote_files if count is None else remote_files[:count]
        local_root = Path(local_directory).expanduser().resolve()
        local_root.mkdir(parents=True, exist_ok=True)
        downloaded: list[Path] = []

        for remote_path in remote_files:
            destination = _local_destination(local_root, remote_directory, remote_path)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and not overwrite:
                continue
            temporary = destination.with_name(destination.name + ".part")
            callback: Callable[[int, int], None] | None = None
            if progress:

                def callback(done: int, total: int, path: str = remote_path) -> None:
                    progress(path, done, total)

            try:
                self._get_sftp().get(remote_path, str(temporary), callback=callback)
                os.replace(temporary, destination)
            except Exception as exc:
                temporary.unlink(missing_ok=True)
                if isinstance(exc, SSHClientError):
                    raise
                raise SSHClientError(f"Could not download {remote_path!r}: {exc}") from exc
            downloaded.append(destination)
        return downloaded


def _normalise_remote_directory(directory: str) -> str:
    if not directory.strip():
        raise ValueError("remote_directory must not be empty")
    # 返回规范化的 POSIX 路径（去除多余的斜杠/点段）
    return posixpath.normpath(directory.strip())


def _local_destination(local_root: Path, remote_root: str, remote_path: str) -> Path:
    """将远程路径映射为本地安全目标路径。

    校验与说明：
    - 确保 `remote_path` 在 `remote_root` 下（避免上溯到其它目录）。
    - 拒绝包含空段、'.'、'..' 的路径以防目录遍历攻击。
    - 最终返回解析后的本地绝对路径，且保证其位于 `local_root` 之下。
    """
    try:
        relative = PurePosixPath(remote_path).relative_to(PurePosixPath(remote_root))
    except ValueError as exc:
        raise SSHClientError(f"Remote path {remote_path!r} is outside {remote_root!r}") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise SSHClientError(f"Unsafe remote path: {remote_path!r}")
    destination = local_root.joinpath(*relative.parts).resolve()
    if not destination.is_relative_to(local_root):
        raise SSHClientError(f"Unsafe remote path: {remote_path!r}")
    return destination


def download_pdf_files(
    local_directory: str | Path,
    count: int | None = None,
    *,
    remote_directory: str | None = None,
    recursive: bool = False,
    overwrite: bool = False,
    config: SSHConfig | None = None,
    progress: ProgressCallback | None = None,
) -> list[Path]:
    """连接到服务器，下载 PDF，然后可靠断开连接。

    这是对 `SSHClient.download_pdf_files` 的便捷封装：在内部会管理连接上下文，
    使用完成后自动关闭连接。
    """
    selected_remote_directory = remote_directory or os.getenv("SSH_REMOTE_PDF_DIR") or DEFAULT_REMOTE_PDF_DIR
    with SSHClient(config) as client:
        return client.download_pdf_files(
            local_directory,
            count,
            remote_directory=selected_remote_directory,
            recursive=recursive,
            overwrite=overwrite,
            progress=progress,
        )


def _parser(config: SSHConfig) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Download PDFs over SSH/SFTP")
    parser.add_argument("local_directory", nargs="?", default="data/pdfs")
    parser.add_argument("--host", default=config.host)
    parser.add_argument("--port", type=int, default=config.port)
    parser.add_argument("--username", default=config.username)
    parser.add_argument("--key-file", type=Path, default=config.key_filename)
    parser.add_argument("--known-hosts", type=Path, default=config.known_hosts)
    parser.add_argument("--remote-directory", default=os.getenv("SSH_REMOTE_PDF_DIR", DEFAULT_REMOTE_PDF_DIR))
    parser.add_argument("--count", type=int)
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--list-only", action="store_true")
    parser.add_argument("--accept-new-host-key", action="store_true", default=config.accept_unknown_host_key)
    parser.add_argument("--use-agent", action="store_true", help="do not prompt for a password")
    return parser


def _show_progress(remote_path: str, done: int, total: int) -> None:
    percent = 100 if total <= 0 else min(100, done * 100 // total)
    print(f"\r[{percent:3d}%] {remote_path}", end="\n" if done >= total else "", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    """运行命令行下载器。

    命令行示例已在模块顶端说明。该入口会加载 `.env`（若存在），
    并支持交互式密码输入（若未提供密码或私钥）。
    """
    try:
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
        base_config = SSHConfig.from_env()
        args = _parser(base_config).parse_args(argv)
        password = base_config.password
        if password is None and args.key_file is None and not args.use_agent:
            password = getpass.getpass(f"SSH password for {args.username}@{args.host}: ")
        config = replace(
            base_config,
            host=args.host,
            port=args.port,
            username=args.username,
            password=password,
            key_filename=args.key_file,
            known_hosts=args.known_hosts,
            accept_unknown_host_key=args.accept_new_host_key,
        )
        with SSHClient(config) as client:
            if args.list_only:
                remote_paths = client.list_pdf_files(args.remote_directory, recursive=args.recursive)
                if args.count is not None:
                    if args.count < 0:
                        raise ValueError("count must be zero or greater")
                    remote_paths = remote_paths[: args.count]
                print("\n".join(remote_paths))
                print(f"Found {len(remote_paths)} PDF file(s).", file=sys.stderr)
                return 0
            downloaded_paths = client.download_pdf_files(
                args.local_directory,
                args.count,
                remote_directory=args.remote_directory,
                recursive=args.recursive,
                overwrite=args.overwrite,
                progress=_show_progress,
            )
        print(f"Downloaded {len(downloaded_paths)} PDF file(s) to {Path(args.local_directory).resolve()}")
        return 0
    except (SSHClientError, ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
