from __future__ import annotations

from datetime import datetime, timezone
from html import escape
import json
from math import ceil
import secrets
from urllib.parse import parse_qs

from bscli.auth.card import AuthCardResponse, MAX_AUTH_BODY_BYTES
from bscli.auth.embedded import EMBEDDED_SAFE_AREA_CSS, render_embedded_web_app_bridge
from bscli.broker.interactive_browser import (
    InteractiveBrowserAccessDenied,
    InteractiveBrowserUnavailable,
)
from bscli.core.auth_challenges import (
    AuthChallengeStore,
    ChallengeAccessDenied,
    ChallengeNotFound,
    ChallengeStateError,
)


class TrustedInteractiveBrowserApplication:
    def __init__(self, *, challenge_store: AuthChallengeStore, broker) -> None:
        self.challenge_store = challenge_store
        self.broker = broker

    def get_card(self, challenge_id: str, *, secure_cookie: bool) -> AuthCardResponse:
        try:
            challenge = self.challenge_store.get(challenge_id)
        except ChallengeNotFound:
            return _message_response(
                status=404,
                title="认证请求不存在",
                message="请从智能体重新发起登录。",
                tone="error",
            )
        if challenge["challenge_type"] != "interactive_browser_login":
            return _message_response(
                status=400,
                title="认证类型不匹配",
                message="请从智能体重新发起登录。",
                tone="error",
            )
        if challenge["state"] == "pending":
            csrf_token = self.challenge_store.issue_csrf(challenge_id)
            nonce = secrets.token_urlsafe(18)
            response = AuthCardResponse(
                200,
                _security_headers(nonce),
                _render_interactive_card(
                    challenge,
                    csrf_token=csrf_token,
                    nonce=nonce,
                ).encode("utf-8"),
            )
            cookie = (
                f"agentbridge_csrf={csrf_token}; Path=/auth/{challenge_id}; "
                f"HttpOnly; SameSite=Strict; Max-Age={_challenge_ttl_seconds(challenge)}"
            )
            if secure_cookie:
                cookie += "; Secure"
            response.headers["Set-Cookie"] = cookie
            return response
        if challenge["state"] == "processing":
            return _message_response(
                status=409,
                title="登录正在进行",
                message="请返回刚才打开的登录卡继续操作；刷新页面会丢失临时控制通道。",
                tone="processing",
            )
        if challenge["state"] == "succeeded":
            return _message_response(
                status=200,
                title="认证完成",
                message="部门信息库会话已经建立。此页面可以关闭，智能体将继续操作。",
                tone="success",
                close_when_complete=True,
            )
        return _message_response(
            status=410,
            title="认证未完成",
            message="本次交互式登录已失效，请返回智能体重新发起。",
            tone="error",
        )

    def start(
        self,
        challenge_id: str,
        *,
        body: bytes,
        content_type: str,
        csrf_cookie: str,
    ) -> AuthCardResponse:
        if not _valid_body(body, content_type, "application/x-www-form-urlencoded"):
            return _json_response(
                400,
                {"status": "failed", "error": {"code": "INVALID_REQUEST"}},
            )
        try:
            fields = parse_qs(
                body.decode("utf-8"),
                keep_blank_values=True,
                max_num_fields=2,
                strict_parsing=True,
            )
            if set(fields) != {"csrf_token"} or len(fields["csrf_token"]) != 1:
                raise ValueError("invalid CSRF form")
            result = self.broker.start(
                challenge_id=challenge_id,
                csrf_token=fields["csrf_token"][0],
                csrf_cookie=csrf_cookie,
            )
        except ChallengeAccessDenied:
            return _json_response(
                403,
                {"status": "failed", "error": {"code": "CSRF_REJECTED"}},
            )
        except (ChallengeNotFound, ChallengeStateError, UnicodeDecodeError, ValueError):
            return _json_response(
                409,
                {"status": "failed", "error": {"code": "CHALLENGE_UNAVAILABLE"}},
            )
        status = 202 if result.get("status") == "processing" else 500
        return _json_response(status, result)

    def frame(self, challenge_id: str, *, control_token: str) -> AuthCardResponse:
        try:
            frame = self.broker.frame(
                challenge_id=challenge_id,
                control_token=control_token,
            )
        except InteractiveBrowserAccessDenied:
            return _json_response(403, {"error": {"code": "CONTROL_DENIED"}})
        except InteractiveBrowserUnavailable:
            return _json_response(409, {"error": {"code": "BROWSER_UNAVAILABLE"}})
        return AuthCardResponse(
            200,
            {
                "Content-Type": "image/png",
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'",
            },
            frame,
        )

    def event(
        self,
        challenge_id: str,
        *,
        body: bytes,
        content_type: str,
        control_token: str,
    ) -> AuthCardResponse:
        if not _valid_body(body, content_type, "application/json"):
            return _json_response(400, {"error": {"code": "INVALID_REQUEST"}})
        try:
            event = json.loads(body.decode("utf-8"))
            if not isinstance(event, dict):
                raise ValueError("event must be an object")
            result = self.broker.send_event(
                challenge_id=challenge_id,
                control_token=control_token,
                event=event,
            )
        except InteractiveBrowserAccessDenied:
            return _json_response(403, {"error": {"code": "CONTROL_DENIED"}})
        except InteractiveBrowserUnavailable:
            return _json_response(409, {"error": {"code": "BROWSER_UNAVAILABLE"}})
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            return _json_response(400, {"error": {"code": "INVALID_EVENT"}})
        return _json_response(202, result)

    def status(self, challenge_id: str, *, control_token: str) -> AuthCardResponse:
        try:
            result = self.broker.status(
                challenge_id=challenge_id,
                control_token=control_token,
            )
        except InteractiveBrowserAccessDenied:
            return _json_response(403, {"error": {"code": "CONTROL_DENIED"}})
        except (InteractiveBrowserUnavailable, ChallengeNotFound):
            return _json_response(409, {"error": {"code": "BROWSER_UNAVAILABLE"}})
        return _json_response(200, result)


def _render_interactive_card(
    challenge: dict,
    *,
    csrf_token: str,
    nonce: str,
) -> str:
    principal = escape(challenge.get("expected_principal_ref") or "未指定")
    system_name = escape(challenge["system_name"])
    challenge_id = escape(challenge["challenge_id"])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="color-scheme" content="light">
  <title>{system_name}安全登录 · AgentBridge</title>
  <style nonce="{nonce}">
    :root {{
      color-scheme: light;
      --paper: #f2f4f1;
      --surface: #fff;
      --ink: #17201d;
      --muted: #65716c;
      --line: #d4dad6;
      --teal: #087d72;
      --teal-dark: #075f58;
      --amber: #b86c13;
    }}
    * {{ box-sizing: border-box; }}
    html, body {{ min-height: 100%; }}
    body {{
      margin: 0;
      padding: 18px;
      background: var(--paper);
      color: var(--ink);
      font-family: "Microsoft YaHei UI", "Noto Sans CJK SC", sans-serif;
      letter-spacing: 0;
    }}
    .shell {{ width: min(100%, 560px); margin: 0 auto; }}
    header {{ margin-bottom: 14px; }}
    .eyebrow {{ margin: 0 0 5px; color: var(--teal-dark); font-size: 11px; font-weight: 700; }}
    h1 {{ margin: 0; font-size: 22px; line-height: 1.3; }}
    .identity {{ margin: 7px 0 0; color: var(--muted); font-size: 13px; }}
    .notice {{
      margin: 0 0 14px;
      padding: 11px 12px;
      border-left: 3px solid var(--amber);
      background: #fffaf1;
      font-size: 13px;
      line-height: 1.6;
    }}
    button, input {{
      min-height: 44px;
      border-radius: 5px;
      font: inherit;
    }}
    button {{
      border: 0;
      padding: 0 15px;
      background: var(--teal);
      color: #fff;
      font-weight: 700;
      cursor: pointer;
    }}
    button:disabled {{ opacity: .55; cursor: wait; }}
    #start {{ width: 100%; }}
    #workspace {{ display: none; }}
    .screen-wrap {{
      position: relative;
      overflow: hidden;
      width: min(100%, calc(70vh * 430 / 760));
      aspect-ratio: 430 / 760;
      margin: 0 auto;
      border: 1px solid var(--line);
      background: #e7ebe8;
      touch-action: none;
      user-select: none;
    }}
    #screen {{
      display: block;
      width: 100%;
      height: 100%;
      object-fit: contain;
      touch-action: none;
      user-select: none;
      -webkit-user-drag: none;
    }}
    .toolbar {{
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 8px;
      margin-top: 10px;
    }}
    .toolbar input {{
      min-width: 0;
      width: 100%;
      border: 1px solid #abb6b0;
      padding: 9px 11px;
      background: var(--surface);
      color: var(--ink);
    }}
    .keys {{
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 7px;
      margin-top: 8px;
    }}
    .keys button {{
      min-width: 0;
      padding: 0;
      background: #3f514a;
    }}
    #status {{ min-height: 22px; margin: 10px 0 0; color: var(--muted); font-size: 13px; }}
    {EMBEDDED_SAFE_AREA_CSS}
  </style>
</head>
<body>
  <main class="shell">
    <header>
      <p class="eyebrow">AGENTBRIDGE TRUSTED INTERACTIVE AUTH</p>
      <h1>{system_name}</h1>
      <p class="identity">认证身份：{principal}</p>
    </header>
    <p class="notice">滑块和短信验证只发生在此可信页面。浏览器会话由 AgentBridge 保存，页面画面和输入不会进入智能体上下文。</p>
    <button id="start" type="button">启动安全登录</button>
    <section id="workspace" aria-label="受控浏览器">
      <div class="screen-wrap" id="screen-wrap">
        <img id="screen" alt="受控登录页面">
      </div>
      <div class="toolbar">
        <input id="text-entry" type="text" inputmode="text" autocomplete="off"
               placeholder="点选上方输入框后，在这里输入并发送">
        <button id="send-text" type="button">输入</button>
      </div>
      <div class="keys" aria-label="浏览器按键">
        <button type="button" data-key="Backspace" title="退格">⌫</button>
        <button type="button" data-key="Tab" title="切换焦点">Tab</button>
        <button type="button" data-key="Enter" title="确认">↵</button>
        <button type="button" data-wheel="-650" title="向上滚动">↑</button>
        <button type="button" data-wheel="650" title="向下滚动">↓</button>
      </div>
    </section>
    <p id="status" role="status">准备启动中央受控浏览器。</p>
  </main>
  <script nonce="{nonce}">
    (() => {{
      const challengeId = "{challenge_id}";
      const csrfToken = "{escape(csrf_token)}";
      const startButton = document.getElementById("start");
      const workspace = document.getElementById("workspace");
      const screen = document.getElementById("screen");
      const screenWrap = document.getElementById("screen-wrap");
      const status = document.getElementById("status");
      const textEntry = document.getElementById("text-entry");
      const sendText = document.getElementById("send-text");
      let controlToken = "";
      let viewport = {{ width: 430, height: 760 }};
      let frameUrl = "";
      let pointerActive = false;
      let pointerId = null;
      let eventChain = Promise.resolve();
      let gestureStartedAt = 0;
      let gesturePoints = [];
      let stopped = false;

      const coordinates = (event) => {{
        const box = screen.getBoundingClientRect();
        const scale = Math.min(box.width / viewport.width, box.height / viewport.height);
        const renderedWidth = viewport.width * scale;
        const renderedHeight = viewport.height * scale;
        const renderedLeft = box.left + (box.width - renderedWidth) / 2;
        const renderedTop = box.top + (box.height - renderedHeight) / 2;
        return {{
          x: Math.max(0, Math.min(viewport.width, (event.clientX - renderedLeft) * viewport.width / renderedWidth)),
          y: Math.max(0, Math.min(viewport.height, (event.clientY - renderedTop) * viewport.height / renderedHeight)),
        }};
      }};
      const sendEvent = (type, payload = {{}}) => {{
        eventChain = eventChain.then(() => fetch(`/auth/${{challengeId}}/interactive/event`, {{
          method: "POST",
          credentials: "same-origin",
          headers: {{
            "Content-Type": "application/json",
            "X-AgentBridge-Control-Token": controlToken,
          }},
          body: JSON.stringify({{ type, payload }}),
        }})).then((response) => {{
          if (!response.ok) throw new Error("event rejected");
        }}).catch(() => {{
          status.textContent = "浏览器控制暂时不可用，请稍后重试。";
        }});
        return eventChain;
      }};
      const appendGesturePoint = (event) => {{
        const point = coordinates(event);
        point.t = Math.max(0, Math.min(5000, Math.round(performance.now() - gestureStartedAt)));
        const previous = gesturePoints[gesturePoints.length - 1];
        if (previous) {{
          const distance = Math.hypot(point.x - previous.x, point.y - previous.y);
          if (point.t - previous.t < 8 && distance < 1.5) return;
        }}
        if (gesturePoints.length >= 240) {{
          gesturePoints[gesturePoints.length - 1] = point;
          return;
        }}
        gesturePoints.push(point);
      }};
      const collectGesturePoints = (event) => {{
        const samples = typeof event.getCoalescedEvents === "function"
          ? event.getCoalescedEvents()
          : [];
        samples.forEach(appendGesturePoint);
        appendGesturePoint(event);
      }};
      const pollFrame = async () => {{
        while (!stopped) {{
          try {{
            const response = await fetch(`/auth/${{challengeId}}/interactive/frame`, {{
              cache: "no-store",
              credentials: "same-origin",
              headers: {{ "X-AgentBridge-Control-Token": controlToken }},
            }});
            if (response.ok) {{
              const blob = await response.blob();
              const next = URL.createObjectURL(blob);
              screen.src = next;
              if (frameUrl) URL.revokeObjectURL(frameUrl);
              frameUrl = next;
            }}
          }} catch (_error) {{}}
          await new Promise((resolve) => setTimeout(resolve, 350));
        }}
      }};
      const pollStatus = async () => {{
        while (!stopped) {{
          try {{
            const response = await fetch(`/auth/${{challengeId}}/interactive/status`, {{
              cache: "no-store",
              credentials: "same-origin",
              headers: {{ "X-AgentBridge-Control-Token": controlToken }},
            }});
            const result = await response.json();
            if (result.status === "succeeded") {{
              stopped = true;
              status.textContent = "认证完成，正在返回智能体。";
              window.setTimeout(() => window.location.reload(), 250);
              return;
            }}
            if (["failed", "expired", "superseded"].includes(result.status)) {{
              stopped = true;
              status.textContent = "认证未完成，请返回智能体重新发起。";
              startButton.disabled = true;
              return;
            }}
          }} catch (_error) {{}}
          await new Promise((resolve) => setTimeout(resolve, 900));
        }}
      }};

      startButton.addEventListener("click", async () => {{
        startButton.disabled = true;
        startButton.textContent = "正在启动";
        status.textContent = "正在创建隔离的中央浏览器会话。";
        try {{
          const body = new URLSearchParams({{ csrf_token: csrfToken }});
          const response = await fetch(`/auth/${{challengeId}}/interactive/start`, {{
            method: "POST",
            credentials: "same-origin",
            headers: {{ "Content-Type": "application/x-www-form-urlencoded" }},
            body,
          }});
          const result = await response.json();
          if (!response.ok || result.status !== "processing") throw new Error("start failed");
          controlToken = result.controlToken;
          viewport = result.viewport || viewport;
          screenWrap.style.aspectRatio = `${{viewport.width}} / ${{viewport.height}}`;
          screenWrap.style.width = `min(100%, calc(70vh * ${{viewport.width}} / ${{viewport.height}}))`;
          startButton.hidden = true;
          workspace.style.display = "block";
          status.textContent = "请在上方受控页面完成滑块和短信验证。";
          pollFrame();
          pollStatus();
        }} catch (_error) {{
          startButton.disabled = false;
          startButton.textContent = "重新启动安全登录";
          status.textContent = "受控浏览器启动失败，请返回智能体重新发起。";
        }}
      }});

      screen.addEventListener("pointerdown", (event) => {{
        event.preventDefault();
        pointerActive = true;
        pointerId = event.pointerId;
        gestureStartedAt = performance.now();
        gesturePoints = [];
        screen.setPointerCapture(event.pointerId);
        appendGesturePoint(event);
      }});
      screen.addEventListener("pointermove", (event) => {{
        if (!pointerActive || event.pointerId !== pointerId) return;
        event.preventDefault();
        collectGesturePoints(event);
      }});
      const releasePointer = (event) => {{
        if (!pointerActive || event.pointerId !== pointerId) return;
        event.preventDefault();
        collectGesturePoints(event);
        pointerActive = false;
        pointerId = null;
        const points = gesturePoints;
        gesturePoints = [];
        status.textContent = "正在执行本次操作。";
        sendEvent("pointer_gesture", {{ points }}).then(() => {{
          if (!stopped) status.textContent = "操作已执行，请继续完成认证。";
        }});
      }};
      screen.addEventListener("pointerup", releasePointer);
      screen.addEventListener("pointercancel", releasePointer);

      sendText.addEventListener("click", () => {{
        const text = textEntry.value;
        if (!text) return;
        sendEvent("type_text", {{ text }}).then(() => {{
          textEntry.value = "";
          textEntry.focus();
        }});
      }});
      textEntry.addEventListener("keydown", (event) => {{
        if (event.key === "Enter") {{
          event.preventDefault();
          sendText.click();
        }}
      }});
      document.querySelectorAll("[data-key]").forEach((button) => {{
        button.addEventListener("click", () => sendEvent("key", {{ key: button.dataset.key }}));
      }});
      document.querySelectorAll("[data-wheel]").forEach((button) => {{
        button.addEventListener("click", () => sendEvent("wheel", {{
          deltaY: Number(button.dataset.wheel),
        }}));
      }});
    }})();
  </script>
  {render_embedded_web_app_bridge(nonce=nonce)}
</body>
</html>"""


def _message_response(
    *,
    status: int,
    title: str,
    message: str,
    tone: str,
    close_when_complete: bool = False,
) -> AuthCardResponse:
    nonce = secrets.token_urlsafe(18)
    color = "#087d72" if tone == "success" else "#b23a3a" if tone == "error" else "#b86c13"
    html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{escape(title)} · AgentBridge</title>
<style nonce="{nonce}">
body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px;background:#f2f4f1;color:#17201d;font-family:"Microsoft YaHei UI",sans-serif;letter-spacing:0}}
main{{width:min(100%,440px);padding:30px;background:#fff;border:1px solid #d4dad6;border-radius:8px;box-shadow:0 18px 45px rgba(23,32,29,.1);text-align:center}}
.mark{{width:22px;height:22px;margin:0 auto 18px;border:4px solid {color};transform:rotate(45deg)}}
p{{color:#65716c;line-height:1.7}} {EMBEDDED_SAFE_AREA_CSS}
</style></head><body><main><div class="mark"></div><h1>{escape(title)}</h1>
<p>{escape(message)}</p></main>
{render_embedded_web_app_bridge(nonce=nonce, close_when_complete=close_when_complete)}
</body></html>"""
    return AuthCardResponse(status, _security_headers(nonce), html.encode("utf-8"))


def _json_response(status: int, value: dict) -> AuthCardResponse:
    return AuthCardResponse(
        status,
        {
            "Content-Type": "application/json; charset=utf-8",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'",
        },
        json.dumps(value, ensure_ascii=True, separators=(",", ":")).encode("utf-8"),
    )


def _security_headers(nonce: str) -> dict[str, str]:
    return {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "no-store",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
        "Content-Security-Policy": (
            "default-src 'none'; "
            f"style-src 'nonce-{nonce}'; "
            f"script-src 'nonce-{nonce}'; "
            "connect-src 'self'; img-src blob:; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        ),
    }


def _valid_body(body: bytes, content_type: str, expected: str) -> bool:
    return (
        len(body) <= MAX_AUTH_BODY_BYTES
        and content_type.split(";", 1)[0].strip().lower() == expected
    )


def _challenge_ttl_seconds(challenge: dict) -> int:
    try:
        expires = datetime.fromisoformat(challenge["expires_at"])
        now = datetime.now(expires.tzinfo or timezone.utc)
        return max(1, min(900, ceil((expires - now).total_seconds())))
    except (KeyError, TypeError, ValueError):
        return 300
