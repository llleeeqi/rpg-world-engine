import json
import os
import sys
import time
import urllib.request

os.environ.setdefault("QTWEBENGINE_CHROMIUM_FLAGS", "--no-sandbox --disable-gpu --disable-dev-shm-usage")
os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

from PyQt6.QtCore import QEventLoop, QTimer, QUrl  # noqa: E402
from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


BASE_URL = os.environ.get("RPG_WORLD_BASE_URL", "http://127.0.0.1:54925").rstrip("/")


def api(method: str, path: str, payload: dict | None = None) -> dict:
    body = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{BASE_URL}{path}", data=body, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def js_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def run_loop(timeout_ms: int, setup) -> object:
    loop = QEventLoop()
    box: dict[str, object] = {}

    def timeout() -> None:
        box["error"] = TimeoutError("browser smoke timed out")
        loop.quit()

    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(timeout)
    timer.start(timeout_ms)
    setup(loop, box)
    loop.exec()
    timer.stop()
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box.get("value")


def wait_load(view: QWebEngineView, timeout_ms: int = 15000) -> None:
    def setup(loop, box) -> None:
        def done(ok: bool) -> None:
            if not ok:
                box["error"] = RuntimeError("page load failed")
            loop.quit()

        view.loadFinished.connect(done)

    run_loop(timeout_ms, setup)


def evaluate(view: QWebEngineView, script: str, timeout_ms: int = 10000):
    def setup(loop, box) -> None:
        def done(value) -> None:
            box["value"] = value
            loop.quit()

        view.page().runJavaScript(script, done)

    return run_loop(timeout_ms, setup)


def wait_for(view: QWebEngineView, expression: str, timeout_ms: int = 15000) -> None:
    deadline = time.time() + timeout_ms / 1000
    last_value = None
    while time.time() < deadline:
        last_value = evaluate(view, expression, timeout_ms=3000)
        if last_value:
            return
        QApplication.processEvents()
        time.sleep(0.05)
    raise TimeoutError(f"condition did not become true: {expression}; last={last_value!r}")


def main() -> None:
    world_name = f"Browser Smoke {int(time.time())}"
    created = api("POST", "/api/worlds", {"display_name": world_name, "subtitle": "browser integration"})
    world_id = created["manifest"]["world_id"]
    api("POST", f"/api/worlds/{world_id}/characters", {
        "name": "Browser A",
        "profile": "浏览器 smoke 角色。",
        "current_goal": "验证 PC 页面",
    })

    app = QApplication(sys.argv)
    view = QWebEngineView()
    view.resize(1280, 900)
    view.show()
    view.load(QUrl(BASE_URL + "/"))
    wait_load(view)
    wait_for(view, f"document.body.innerText.includes({js_string(world_name)})")

    evaluate(view, f"document.querySelector('[data-open-world={js_string(world_id)}]').click()")
    wait_for(view, "Boolean(document.querySelector('#turnForm'))")

    layout = evaluate(
        view,
        "({"
        "hasShell:Boolean(document.querySelector('.shell')),"
        "hasInspector:Boolean(document.querySelector('.inspector')),"
        "slotCount:document.querySelectorAll('.slot').length,"
        "bodyMinWidth:getComputedStyle(document.body).minWidth,"
        "text:document.body.innerText"
        "})",
    )
    assert layout["hasShell"] is True
    assert layout["hasInspector"] is True
    assert layout["slotCount"] == 3
    assert layout["bodyMinWidth"] == "1180px"
    assert "本轮输入" in layout["text"]
    assert "世界信息" in layout["text"]
    assert "规则" in layout["text"]

    evaluate(
        view,
        "document.querySelector('textarea[name=dm_directive]').value='浏览器 smoke 发生冲突，需要调查和谈判。';"
        "document.querySelector('#turnForm').requestSubmit();",
    )
    wait_for(view, "Boolean(document.querySelector('.draft'))", timeout_ms=20000)
    assert "候选" in evaluate(view, "document.body.innerText")

    evaluate(view, "document.querySelector('#toggleRegen').click();document.querySelector('#editInput').click();")
    wait_for(view, "Boolean(document.querySelector('#editedInputRegenerate'))")
    edit_state = evaluate(
        view,
        "({"
        "title:document.body.innerText.includes('编辑本轮输入'),"
        "cancel:Boolean(document.querySelector('#cancelEditInput')),"
        "regen:Boolean(document.querySelector('#editedInputRegenerate')),"
        "reroll:Boolean(document.querySelector('#editedInputReroll'))"
        "})",
    )
    assert edit_state == {"title": True, "cancel": True, "regen": True, "reroll": True}
    print("browser smoke ok")
    app.quit()


if __name__ == "__main__":
    main()
