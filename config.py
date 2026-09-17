import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY', 'quicktrolly-secret-key-2026')
    
    # Use PostgreSQL URL from environment
    DATABASE_URL = os.environ.get('DATABASE_URL')
    
    # Cloudinary Configuration
    CLOUDINARY_CLOUD_NAME = os.environ.get('CLOUDINARY_CLOUD_NAME')
    CLOUDINARY_API_KEY = os.environ.get('CLOUDINARY_API_KEY')
    CLOUDINARY_API_SECRET = os.environ.get('CLOUDINARY_API_SECRET')

    # Razorpay
    RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID', 'rzp_test_dhYJFlohg88eyl')
    RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', 'YOUR_SECRET_KEY_HERE')
