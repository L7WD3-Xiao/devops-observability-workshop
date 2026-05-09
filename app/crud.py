from sqlalchemy.orm import Session
import models

def create_url(db: Session, short_code: str, original_url: str):
    db_url = models.URLMap(short_code=short_code, original_url=original_url)
    db.add(db_url)
    db.commit()
    db.refresh(db_url)
    return db_url

def get_url_by_code(db: Session, short_code: str):
    return db.query(models.URLMap).filter(models.URLMap.short_code == short_code).first()

def increment_click_count(db: Session, short_code: str):
    db_url = get_url_by_code(db, short_code)
    if db_url:
        db_url.click_count += 1
        db.commit()