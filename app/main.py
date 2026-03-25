from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.database import Base, engine, SessionLocal
from app.routes.web import router as web_router
from app.services import bootstrap_database


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    #Base.metadata.create_all(bind=engine)
    if settings.auto_bootstrap_data:
        db = SessionLocal()
        try:
            bootstrap_database(db)
        finally:
            db.close()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, session_cookie=settings.session_cookie)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(web_router)
app.state.templates = Jinja2Templates(directory="app/templates")
