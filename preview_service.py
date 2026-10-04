#!/usr/bin/env python3
"""Loopback preview plus authenticated same-origin research actions."""

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlsplit

from research_workbench import Workbench
from workflow_storage import current_root

ROOT = Path(__file__).resolve().parent


class ResearchWorker:
    def __init__(self, store):
        self.store = store
        self.stop = threading.Event()
        self.process = None

    def run(self):
        while not self.stop.is_set():
            job = self.store.claim()
            if not job:
                self.stop.wait(2)
                continue
            keyword_job = job["kind"] == "keyword"
            if not keyword_job and self.store.research_page(job["asin"]):
                self.store.finish(job["id"], "completed", "已有研究，未重复调用")
                continue
            logs = self.store.root / "logs"
            logs.mkdir(exist_ok=True)
            try:
                command = ([sys.executable, "-u", "keyword_opportunity_search.py", "--keyword", job["keyword"],
                            "--run-id", f"job-{job['id']}"] if keyword_job else
                           [sys.executable, "-u", "refresh_product_research.py", "--asin", job["asin"]])
                with (logs / f"research-job-{job['id']}.log").open("a") as output:
                    self.process = subprocess.Popen(
                        command,
                        cwd=self.store.root, stdout=output, stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    self.store.set_pid(job["id"], self.process.pid)
                    code = self.process.wait(timeout=2700)
                if self.stop.is_set():
                    self.store.finish(job["id"], "interrupted", "服务重启，检查后可重试")
                elif code == 0 and keyword_job:
                    from keyword_opportunity_search import slugify
                    result_path = self.store.root / "web" / "keyword" / slugify(job["keyword"]) / f"job-{job['id']}" / "results.json"
                    result = json.loads(result_path.read_text(encoding="utf-8"))
                    summary = result["summary"]
                    self.store.finish(job["id"], "completed",
                                      f"搜索完成：{int(summary['raw_result_count'])} 个相关产品，{int(summary['eligible_candidate_count'])} 个通过基础过滤", result=result)
                elif code == 0 and self.store.research_page(job["asin"]):
                    self.store.finish(job["id"], "completed", "研究页已生成；缺失证据仍需复核")
                else:
                    self.store.finish(job["id"], "failed", "搜索未完成，请检查本地任务日志后重试" if keyword_job else "研究未完成，请检查本地任务日志后重试")
            except subprocess.TimeoutExpired:
                self.terminate_child()
                self.store.finish(job["id"], "failed", "研究超时，已停止；请检查后重试")
            except Exception:
                self.terminate_child()
                self.store.finish(job["id"], "failed", "执行异常，请检查本地服务配置")
            finally:
                self.process = None

    def terminate_child(self):
        process = self.process
        if process and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            except ProcessLookupError:
                pass


class Handler(SimpleHTTPRequestHandler):
    def send_head(self):
        # Pin each response to one committed generation; manual research pages
        # remain in the workspace and are not hidden by a weekly snapshot.
        path = urlsplit(self.path).path
        root = self.server.store.root
        if path in {"/", "/index.html"}:
            self.directory = str(current_root(root) / "web")
        else:
            self.directory = str(root / "web")
            candidate = Path(self.translate_path(self.path))
            if not candidate.is_file():
                self.directory = str(current_root(root) / "web")
        return super().send_head()

    def valid_host(self):
        return self.headers.get("Host") in self.server.allowed_hosts

    def valid_origin(self, allow_missing=False):
        site = self.headers.get("Sec-Fetch-Site")
        if site in {"cross-site", "same-site"}:
            return False
        origin = self.headers.get("Origin")
        if not origin:
            return allow_missing
        expected = "http://" + self.headers.get("Host", "")
        if origin == expected:
            return True
        # Some embedded browsers omit the port in Origin. Only accept this
        # with the browser-controlled same-origin signal, never just a host match.
        return site == "same-origin" and origin == "http://" + urlsplit(expected).hostname

    def reply(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, {"error": "仅允许本机访问"})
        path = urlsplit(self.path).path
        if path in {"/api/workbench", "/api/keyword-search/results"}:
            if not self.valid_origin(allow_missing=True):
                return self.reply(403, {"error": "来源不匹配"})
            if path == "/api/keyword-search/results":
                try:
                    job_id = parse_qs(urlsplit(self.path).query).get("job", [""])[0]
                    return self.reply(200, self.server.store.keyword_result(job_id))
                except (TypeError, ValueError):
                    return self.reply(404, {"error": "搜索结果尚未生成"})
            return self.reply(200, {**self.server.store.state(), "token": self.server.token})
        if path.startswith("/api/"):
            return self.reply(404, {"error": "接口不存在"})
        resolved = Path(self.translate_path(self.path)).resolve()
        if not resolved.is_relative_to(Path(self.directory).resolve()):
            return self.reply(403, {"error": "路径不可访问"})
        super().do_GET()

    def do_HEAD(self):
        resolved = Path(self.translate_path(self.path)).resolve()
        if not self.valid_host() or not resolved.is_relative_to(Path(self.directory).resolve()):
            self.send_error(403)
            return
        super().do_HEAD()

    def do_POST(self):
        if (not self.valid_host()
                or not self.valid_origin()
                or not secrets.compare_digest(self.headers.get("X-Workbench-Token", ""), self.server.token)):
            return self.reply(403, {"error": "请求验证失败，请刷新页面"})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.reply(415, {"error": "仅接受 JSON"})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 4096:
                raise ValueError("请求大小无效")
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError("请求格式无效")
            path = urlsplit(self.path).path
            store = self.server.store
            if path == "/api/keyword-search":
                return self.reply(200, store.enqueue_keyword(data.get("keyword"), data.get("request_id")))
            if path == "/api/dismissals":
                store.dismiss(data.get("kind"), data.get("key"), data.get("discarded"), data.get("label", ""))
                return self.reply(200, {"ok": True})
            if path == "/api/favorites":
                store.favorite(data.get("asin"), data.get("saved"), data.get("title", ""), data.get("family", ""))
                return self.reply(200, {"ok": True})
            if path == "/api/research":
                job = store.enqueue(data.get("asin"), data.get("title", ""))
                job.pop("pid", None)
                return self.reply(200, job)
            if path == "/api/research/cancel":
                store.cancel(data.get("id"))
                return self.reply(200, {"ok": True})
            return self.reply(404, {"error": "接口不存在"})
        except (ValueError, TypeError):
            return self.reply(400, {"error": "请求无效，或任务状态已变化；请刷新后再试"})
        except Exception:
            return self.reply(500, {"error": "无法保存，请检查本地服务日志"})

    def list_directory(self, path):
        self.send_error(403, "Directory listing disabled")


def make_server(root=ROOT, port=8765):
    server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, directory=str(Path(root) / "web")))
    server.store = Workbench(root)
    server.token = secrets.token_urlsafe(32)
    actual_port = server.server_address[1]
    server.allowed_hosts = {f"127.0.0.1:{actual_port}", f"localhost:{actual_port}"}
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = make_server(port=args.port)
    worker = ResearchWorker(server.store)
    thread = threading.Thread(target=worker.run, daemon=True)
    thread.start()

    def stop(*_):
        worker.stop.set()
        worker.terminate_child()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever()
    finally:
        worker.stop.set()
        worker.terminate_child()
        thread.join(timeout=10)
        server.server_close()


if __name__ == "__main__":
    main()
