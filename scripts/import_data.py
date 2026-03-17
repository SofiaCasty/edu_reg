from app.database import Base, SessionLocal, engine
from app.services import bootstrap_database


def main():
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        summary = bootstrap_database(db)
    print(summary)


if __name__ == "__main__":
    main()

