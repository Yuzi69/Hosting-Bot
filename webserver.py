import os
from pathlib import Path

from aiohttp import web, ClientSession, ClientTimeout
from dotenv import load_dotenv

import store

load_dotenv()
WEB_PORT = int(os.getenv("WEB_PORT", "8080"))


async def serve_static(request: web.Request) -> web.StreamResponse:
    project_id = request.match_info["id"]
    subpath = request.match_info.get("path", "") or "index.html"
    project = store.get(project_id)
    if not project or project.get("type") != "static":
        raise web.HTTPNotFound()

    root = Path(project["path"]).resolve()
    target = (root / subpath).resolve()

    # Prevent path traversal outside the project root.
    if root not in target.parents and target != root:
        raise web.HTTPForbidden()
    if target.is_dir():
        target = target / "index.html"
    if not target.exists():
        raise web.HTTPNotFound()

    return web.FileResponse(target)


async def proxy_app(request: web.Request) -> web.StreamResponse:
    project_id = request.match_info["id"]
    subpath = request.match_info.get("path", "")
    project = store.get(project_id)
    if not project or not project.get("port"):
        raise web.HTTPNotFound()
    if project.get("status") != "running":
        return web.Response(text="This project isn't running right now.", status=503)

    target_url = f"http://127.0.0.1:{project['port']}/{subpath}"
    if request.query_string:
        target_url += f"?{request.query_string}"

    headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
    body = await request.read()

    timeout = ClientTimeout(total=30)
    async with ClientSession(timeout=timeout) as session:
        async with session.request(
            request.method, target_url, headers=headers, data=body, allow_redirects=False
        ) as resp:
            resp_body = await resp.read()
            response = web.Response(status=resp.status, body=resp_body)
            for k, v in resp.headers.items():
                if k.lower() not in ("content-length", "transfer-encoding", "connection"):
                    response.headers[k] = v
            return response


async def health(request: web.Request) -> web.Response:
    return web.Response(text="ok")


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/sites/{id}/", serve_static)
    app.router.add_get("/sites/{id}/{path:.*}", serve_static)
    app.router.add_route("*", "/app/{id}/", proxy_app)
    app.router.add_route("*", "/app/{id}/{path:.*}", proxy_app)
    return app


if __name__ == "__main__":
    web.run_app(build_app(), port=WEB_PORT)
