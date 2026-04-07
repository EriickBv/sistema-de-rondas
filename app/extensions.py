from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from flask_migrate import Migrate
from flask_apscheduler import APScheduler

db       = SQLAlchemy()
cors     = CORS()
migrate  = Migrate()
scheduler = APScheduler()