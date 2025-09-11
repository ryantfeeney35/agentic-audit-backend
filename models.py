# models.py
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

class Property(db.Model):
    __tablename__ = 'properties'
    id = db.Column(db.Integer, primary_key=True)
    street = db.Column(db.String)
    city = db.Column(db.String)
    state = db.Column(db.String)
    zip_code = db.Column(db.String)
    year_built = db.Column(db.Integer)
    sqft = db.Column(db.Integer, nullable=True)
    utility_bill_url = db.Column(db.String, nullable=True)