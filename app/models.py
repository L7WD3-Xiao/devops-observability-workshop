from sqlalchemy import Column, String, Integer, DateTime
from datetime import datetime
from database import Base

class URLMap(Base):
    __tablename__ = "url_maps"
    
    id = Column(Integer, primary_key=True, index=True)
    short_code = Column(String(16), unique=True, index=True, nullable=False)
    original_url = Column(String(2048), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    click_count = Column(Integer, default=0)