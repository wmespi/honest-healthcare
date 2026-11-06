"""The screen: the built frontend (application/frontend/dist/), served by the
API's own process, so the screen and the API share one origin.

Every path outside /api is the screen's. A built file is served as it is, and
any other path, such as /find-care, is an address the screen's router knows, so
it gets index.html; a missing asset is a 404. Vite names each file under
assets/ by its content hash, so
those are cached for good, and everything else is checked on every load, so a
new deploy shows on the next one. Each is `private`, so only the browser
keeps it, and no shared cache hands one out past the invite gate (gate.py).
"""
import os

from starlette.exceptions import HTTPException
from starlette.staticfiles import StaticFiles

# The build's output (`npm run build`); the app's tests point it at their own.
SCREEN_DIR = os.getenv("SCREEN_DIR") or os.path.join(os.path.dirname(os.path.dirname(__file__)), "frontend", "dist")

_HASHED = "private, max-age=31536000, immutable"


class Screen(StaticFiles):
    async def get_response(self, path, scope):
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404)
        try:
            response = await super().get_response(path, scope)
        except HTTPException as e:
            if e.status_code != 404 or path.startswith("assets/"):
                raise
            try:
                response = await super().get_response("index.html", scope)
            except HTTPException:
                raise HTTPException(503, detail=f"the screen isn't built under {self.directory}: "
                                                "run `npm run build` in application/frontend")
        response.headers["Cache-Control"] = _HASHED if path.startswith("assets/") else "no-cache"
        return response
