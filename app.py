import re
import html
import time
from typing import List, Optional

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse, PlainTextResponse, HTMLResponse
from pydantic import BaseModel, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException


NOT_FOUND = "score not found"
REQUEST_TIMEOUT = "request timeout"
INVALID_MATCH_ID = "invalid score id"


class APIError(Exception):
    def __init__(self, status_code: int, message: str):
        self.status_code = status_code
        self.message = message


class Batsman(BaseModel):
    name: str = NOT_FOUND
    score: str = NOT_FOUND


class Bowler(BaseModel):
    name: str = NOT_FOUND


class ScoreResponse(BaseModel):
    status: str
    title: str
    score: str
    current_batsmen: List[Batsman]
    current_bowler: Bowler


class MatchValidator(BaseModel):
    score: str

    @field_validator("score")
    @classmethod
    def validate_match_id(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError(INVALID_MATCH_ID)
        if not value.isdigit():
            raise ValueError("score id must contain digits only")
        if len(value) < 4:
            raise ValueError("score id must be at least 4 digits")
        if len(value) > 20:
            raise ValueError("score id too long")
        return value


app = FastAPI(
    title="Score API",
    version="0.0.1",
    description="Live Cricket Score JSON API",
    docs_url=None,
    redoc_url=None
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Strict-Transport-Security"] = "max-age=31536000"
    return response


class ScoreService:
    HEADERS = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/146.0.0.0 Safari/537.36",
        "Referer": "https://www.cricbuzz.com/",
    }

    @staticmethod
    def clean(text: str) -> str:
        if not text:
            return NOT_FOUND
        return html.escape(" ".join(text.split()))

    @classmethod
    def default_batsmen(cls) -> List[Batsman]:
        return [Batsman(), Batsman()]

    @classmethod
    def format_tree(cls, data: ScoreResponse) -> str:
        batsmen_lines = "\n".join(
            f"│   ├── {player.name} : {player.score}" for player in data.current_batsmen
        )
        return (
            "🏏 Live Score\n"
            "│\n"
            f"├── Match    : {data.title}\n"
            f"├── Score    : {data.score}\n"
            f"├── Bowler   : {data.current_bowler.name}\n"
            "├── Batsmen\n"
            f"{batsmen_lines}"
        )

    @classmethod
    async def fetch_score(cls, match_id: str) -> ScoreResponse:
        try:
            url = f"https://www.cricbuzz.com/live-cricket-scores/{match_id}?_={time.time_ns()}"

            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(url, headers=cls.HEADERS)
                response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")

            title = cls.clean(
                re.sub(r"^Cricket commentary\s*\|\s*", "", 
                       soup.title.get_text(strip=True) if soup.title else NOT_FOUND, 
                       flags=re.IGNORECASE)
            )

            og_tag = soup.find("meta", property="og:title")
            og_title = og_tag.get("content", "") if og_tag else ""

            score = NOT_FOUND
            score_match = re.search(r"([A-Z]{2,4})\s+(\d+)/(\d+)\s*\(([\d.]+)\)", og_title)
            if score_match:
                team, runs, wickets, overs = score_match.groups()
                score = f"{team} {runs}/{wickets} ({overs})"

            batsmen = []
            batsman_match = re.search(r"\((.*?)\)\s*\|", og_title)
            if batsman_match:
                players = re.findall(r"([A-Za-z\s.'-]+)\s+(\d+\(\d+\))", batsman_match.group(1))
                batsmen = [Batsman(name=cls.clean(n), score=cls.clean(s)) for n, s in players[:2]]

            if len(batsmen) < 2:
                batsmen = cls.default_batsmen()

            page_text = cls.clean(soup.get_text(" ", strip=True))
            bowler_match = re.search(r"Bowler.*?([A-Za-z.'\- ]+?)\s+\d+\s+\d+", page_text, re.IGNORECASE)
            bowler_name = cls.clean(bowler_match.group(1)) if bowler_match else NOT_FOUND

            return ScoreResponse(
                status="success",
                title=title,
                score=score,
                current_batsmen=batsmen,
                current_bowler=Bowler(name=bowler_name)
            )

        except httpx.TimeoutException:
            raise APIError(408, REQUEST_TIMEOUT)
        except httpx.HTTPStatusError:
            raise APIError(404, "score data unavailable")
        except Exception:
            raise APIError(500, "failed to process score data")


@app.get("/docs", include_in_schema=False)
async def custom_swagger_docs():
    try:
        html_content = get_swagger_ui_html(
            openapi_url=app.openapi_url,
            title="Live Cricket Score API Docs"
        ).body.decode("utf-8")

        custom_style = """<style>body { font-family: system-ui; }</style>"""
        html_content = html_content.replace("</head>", custom_style + "</head>")
        return HTMLResponse(content=html_content)
    except Exception:
        return HTMLResponse(content="<h2>Docs Error</h2>", status_code=500)


@app.get("/", response_model=ScoreResponse)
async def root(
    score: Optional[str] = Query(None, min_length=4, max_length=20),
    text: bool = Query(False)
):
    if score is None:
        return ScoreResponse(
            status="success",
            title="Live Score API",
            score=NOT_FOUND,
            current_batsmen=ScoreService.default_batsmen(),
            current_bowler=Bowler()
        )

    try:
        validated = MatchValidator(score=score)
    except ValueError as e:
        raise APIError(422, str(e))

    result = await ScoreService.fetch_score(validated.score)

    if text:
        return PlainTextResponse(ScoreService.format_tree(result))

    return result


@app.exception_handler(APIError)
async def api_error_handler(request: Request, exc: APIError):
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "code": exc.status_code, "message": exc.message}
    )


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "code": exc.status_code, "message": "invalid api route"}
    )


@app.exception_handler(Exception)
async def global_error_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"status": "error", "code": 500, "message": "internal server error"}
    )
