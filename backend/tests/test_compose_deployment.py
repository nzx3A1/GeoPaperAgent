#!/usr/bin/env python3
"""验证 backend/compose.yml 中的基础设施是否已正确部署。

可直接运行：
    python tests/test_compose_deployment.py

容器位于 WSL 时，也可以直接从 Windows PowerShell 运行；脚本会自动调用 WSL
中的 Docker。若 Docker 不在默认 WSL 发行版，可先设置 COMPOSE_WSL_DISTRO：
    $env:COMPOSE_WSL_DISTRO = "Ubuntu"

也可通过 pytest 运行：
    pytest -v tests/test_compose_deployment.py

脚本不会启动或停止容器，只会执行健康检查和可自动清理的临时读写测试。
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import socket
import subprocess
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
COMPOSE_FILE = BACKEND_DIR / "compose.yml"
SERVICES = (
    "postgres",
    "redis",
    "minio",
    "etcd",
    "milvus-minio",
    "milvus",
    "neo4j",
)
EXPECTED_MOUNTS = {
    "postgres": {"/var/lib/pgsql/data": "/root/GeoPaperAgentData/postgresql"},
    "redis": {"/data": "/root/GeoPaperAgentData/redis"},
    "minio": {"/data": "/root/GeoPaperAgentData/minio-business"},
    "etcd": {"/etcd": "/root/GeoPaperAgentData/milvus-etcd"},
    "milvus-minio": {"/data": "/root/GeoPaperAgentData/milvus-minio"},
    "milvus": {"/var/lib/milvus": "/root/GeoPaperAgentData/milvus"},
    "neo4j": {
        "/data": "/root/GeoPaperAgentData/neo4j/data",
        "/logs": "/root/GeoPaperAgentData/neo4j/logs",
    },
}
PUBLISHED_PORTS = {
    "postgres": (5432,),
    "redis": (6379,),
    "minio": (9000, 9001),
    "etcd": (2379,),
    "milvus": (19530, 9091),
    "neo4j": (7474, 7687),
}
COMMAND_TIMEOUT_SECONDS = 30
HEALTH_TIMEOUT_SECONDS = int(os.getenv("COMPOSE_VERIFY_TIMEOUT_SECONDS", "180"))
IS_WINDOWS = os.name == "nt"
WSL_DISTRO = os.getenv("COMPOSE_WSL_DISTRO", "").strip()


def run_command(*args: str, check: bool = True, timeout: int = COMMAND_TIMEOUT_SECONDS) -> str:
    """运行命令并返回标准输出；失败时提供可读诊断信息。"""
    try:
        result = subprocess.run(
            args,
            cwd=BACKEND_DIR,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        command = "WSL" if IS_WINDOWS else "docker"
        raise AssertionError(f"找不到 {command} 命令，请检查 WSL、Docker 和 Compose V2。") from exc
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(f"命令执行超时：{' '.join(args)}") from exc

    if check and result.returncode != 0:
        details = (result.stderr or result.stdout).strip()
        raise AssertionError(f"命令执行失败（退出码 {result.returncode}）：{' '.join(args)}\n{details}")
    return result.stdout.strip()


def wsl_prefix() -> tuple[str, ...]:
    if not IS_WINDOWS:
        return ()
    if WSL_DISTRO:
        return ("wsl.exe", "--distribution", WSL_DISTRO, "--")
    return ("wsl.exe", "--")


def docker(*args: str, check: bool = True, timeout: int = COMMAND_TIMEOUT_SECONDS) -> str:
    return run_command(*wsl_prefix(), "docker", *args, check=check, timeout=timeout)


@lru_cache(maxsize=1)
def docker_compose_file() -> str:
    """返回 Docker 所在环境能够访问的 Compose 文件路径。"""
    if not IS_WINDOWS:
        return str(COMPOSE_FILE)
    windows_path = str(COMPOSE_FILE).replace("\\", "/")
    return run_command(*wsl_prefix(), "wslpath", "-a", windows_path)


def compose(*args: str, check: bool = True, timeout: int = COMMAND_TIMEOUT_SECONDS) -> str:
    return docker(
        "compose",
        "-f",
        docker_compose_file(),
        *args,
        check=check,
        timeout=timeout,
    )


def compose_exec(service: str, *command: str, timeout: int = COMMAND_TIMEOUT_SECONDS) -> str:
    return compose("exec", "-T", service, *command, timeout=timeout)


def container_id(service: str) -> str:
    ids = compose("ps", "--all", "--quiet", service).splitlines()
    if len(ids) != 1:
        raise AssertionError(f"服务 {service!r} 没有且仅有一个容器；请先运行 docker compose -f {COMPOSE_FILE} up -d")
    return ids[0]


def inspect_container(service: str) -> dict:
    data = json.loads(docker("inspect", container_id(service)))
    if len(data) != 1:
        raise AssertionError(f"无法获得服务 {service!r} 的容器详情。")
    return data[0]


def service_state(service: str) -> tuple[str, str]:
    state = inspect_container(service)["State"]
    return state.get("Status", "unknown"), state.get("Health", {}).get("Status", "missing")


def wait_until_healthy() -> None:
    """等待所有容器运行且通过 Compose healthcheck。"""
    for service in SERVICES:
        container_id(service)

    deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
    latest: dict[str, tuple[str, str]] = {}
    while time.monotonic() < deadline:
        latest = {service: service_state(service) for service in SERVICES}
        if all(state == "running" and health == "healthy" for state, health in latest.values()):
            return
        time.sleep(2)

    status_text = ", ".join(f"{service}=status:{state}/health:{health}" for service, (state, health) in latest.items())
    logs = compose("logs", "--tail", "30", *SERVICES, check=False, timeout=60)
    raise AssertionError(f"等待服务健康超时（{HEALTH_TIMEOUT_SECONDS} 秒）：{status_text}\n最近的容器日志：\n{logs}")


def published_port(service: str, container_port: int) -> int:
    output = compose("port", service, str(container_port))
    if not output:
        raise AssertionError(f"服务 {service!r} 未发布容器端口 {container_port}。")
    try:
        return int(output.splitlines()[-1].rsplit(":", 1)[1])
    except (IndexError, ValueError) as exc:
        raise AssertionError(f"无法解析 {service}:{container_port} 的端口映射：{output!r}") from exc


def s3_request(
    endpoint: str,
    method: str,
    path: str,
    access_key: str,
    secret_key: str,
    payload: bytes = b"",
) -> bytes:
    """使用 AWS Signature V4 请求 MinIO，避免引入额外的 S3 SDK。"""
    now = datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = now.strftime("%Y%m%d")
    region = "us-east-1"
    service = "s3"
    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    payload_hash = hashlib.sha256(payload).hexdigest()
    canonical_uri = urllib.parse.quote(path, safe="/-_.~")
    host = urllib.parse.urlsplit(endpoint).netloc
    canonical_headers = f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join([method, canonical_uri, "", canonical_headers, signed_headers, payload_hash])
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )

    def sign(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode(), hashlib.sha256).digest()

    signing_key = sign(
        sign(sign(sign(("AWS4" + secret_key).encode(), date_stamp), region), service),
        "aws4_request",
    )
    signature = hmac.new(signing_key, string_to_sign.encode(), hashlib.sha256).hexdigest()
    authorization = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed_headers}, Signature={signature}"
    )
    request = urllib.request.Request(
        endpoint + canonical_uri,
        data=payload if method in {"PUT", "POST"} else None,
        method=method,
        headers={
            "Host": host,
            "X-Amz-Content-Sha256": payload_hash,
            "X-Amz-Date": amz_date,
            "Authorization": authorization,
        },
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(request, timeout=COMMAND_TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"MinIO S3 请求失败：{method} {path} -> HTTP {exc.code}\n{body}") from exc
    except urllib.error.URLError as exc:
        raise AssertionError(f"无法连接 MinIO：{endpoint}{path}：{exc}") from exc


class TestComposeDeployment(unittest.TestCase):
    """对 Compose 基础设施执行部署状态和功能验收。"""

    @classmethod
    def setUpClass(cls) -> None:
        if not COMPOSE_FILE.is_file():
            raise AssertionError(f"Compose 文件不存在：{COMPOSE_FILE}")
        docker("info", "--format", "{{.ServerVersion}}")
        compose("config", "--quiet")
        wait_until_healthy()

    def test_all_services_are_running_and_healthy(self) -> None:
        states = {service: service_state(service) for service in SERVICES}
        self.assertEqual(
            {service: ("running", "healthy") for service in SERVICES},
            states,
        )

    def test_persistent_mounts_use_expected_directories(self) -> None:
        for service, expected in EXPECTED_MOUNTS.items():
            mounts = {mount["Destination"]: mount["Source"] for mount in inspect_container(service).get("Mounts", [])}
            for destination, source in expected.items():
                self.assertEqual(source, mounts.get(destination), f"{service}:{destination} 挂载错误")

    def test_all_published_ports_accept_connections(self) -> None:
        for service, ports in PUBLISHED_PORTS.items():
            for container_port in ports:
                host_port = published_port(service, container_port)
                with self.subTest(service=service, container_port=container_port, host_port=host_port):
                    with socket.create_connection(("127.0.0.1", host_port), timeout=5):
                        pass

    def test_postgresql_authenticated_round_trip(self) -> None:
        output = compose_exec(
            "postgres",
            "sh",
            "-ec",
            """
result="$(PGPASSWORD="$POSTGRESQL_PASSWORD" psql \
  -h 127.0.0.1 -U "$POSTGRESQL_USER" -d "$POSTGRESQL_DATABASE" \
  -v ON_ERROR_STOP=1 -Atqc \
  'CREATE TEMP TABLE deployment_smoke_test(value integer); \
   INSERT INTO deployment_smoke_test VALUES (1); \
   SELECT value FROM deployment_smoke_test;')"
test "$result" = "1"
printf '%s' "$result"
""".strip(),
        )
        self.assertEqual("1", output)

    def test_redis_authenticated_round_trip(self) -> None:
        output = compose_exec(
            "redis",
            "sh",
            "-ec",
            """
key="geopaperagent:deployment-smoke-test"
cleanup() { redis-cli --no-auth-warning -a "$REDIS_PASSWORD" DEL "$key" >/dev/null 2>&1 || true; }
trap cleanup EXIT
redis-cli --no-auth-warning -a "$REDIS_PASSWORD" SET "$key" verified EX 30 >/dev/null
result="$(redis-cli --no-auth-warning -a "$REDIS_PASSWORD" GET "$key")"
test "$result" = "verified"
printf '%s' "$result"
""".strip(),
        )
        self.assertEqual("verified", output)

    def test_business_minio_authenticated_object_round_trip(self) -> None:
        access_key = compose_exec("minio", "printenv", "MINIO_ROOT_USER")
        secret_key = compose_exec("minio", "printenv", "MINIO_ROOT_PASSWORD")
        endpoint = f"http://127.0.0.1:{published_port('minio', 9000)}"
        bucket = f"geopaperagent-deployment-test-{uuid.uuid4().hex[:12]}"
        object_path = f"/{bucket}/proof.txt"
        payload = b"GeoPaperAgent Compose deployment verified"

        s3_request(endpoint, "PUT", f"/{bucket}", access_key, secret_key)
        try:
            s3_request(endpoint, "PUT", object_path, access_key, secret_key, payload)
            downloaded = s3_request(endpoint, "GET", object_path, access_key, secret_key)
            self.assertEqual(payload, downloaded)
        finally:
            try:
                s3_request(endpoint, "DELETE", object_path, access_key, secret_key)
            finally:
                s3_request(endpoint, "DELETE", f"/{bucket}", access_key, secret_key)

    def test_etcd_round_trip(self) -> None:
        output = compose_exec(
            "etcd",
            "sh",
            "-ec",
            """
key="/geopaperagent/deployment-smoke-test"
cleanup() { etcdctl del "$key" >/dev/null 2>&1 || true; }
trap cleanup EXIT
etcdctl put "$key" verified >/dev/null
result="$(etcdctl get "$key" --print-value-only)"
test "$result" = "verified"
printf '%s' "$result"
""".strip(),
        )
        self.assertEqual("verified", output)

    def test_milvus_and_its_object_storage_are_ready(self) -> None:
        compose_exec("milvus-minio", "curl", "-fsS", "http://127.0.0.1:9000/minio/health/ready")
        compose_exec("milvus", "curl", "-fsS", "http://127.0.0.1:9091/healthz")

    def test_neo4j_authenticated_round_trip(self) -> None:
        output = compose_exec(
            "neo4j",
            "sh",
            "-ec",
            """
password="${NEO4J_AUTH#*/}"
cypher-shell -a bolt://127.0.0.1:7687 -u neo4j -p "$password" --format plain \
  "CREATE (n:DeploymentSmokeTest {id: 'compose-verifier'}) \
   WITH n, n.id AS result DELETE n RETURN result;"
""".strip(),
        )
        self.assertIn("compose-verifier", output)


if __name__ == "__main__":
    unittest.main(verbosity=2)
