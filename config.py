import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'quicktrolly-secret-key-2026')
    DATABASE = os.environ.get('DATABASE', 'quicktrolly_db.sqlite')
    RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID', 'rzp_test_dhYJFlohg88eyl')
    RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', 'YOUR_SECRET_KEY_HERE')
